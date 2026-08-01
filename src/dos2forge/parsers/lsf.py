"""Parser and writer for Larian LSF resources (binary node trees).

LSF is the binary serialization of the same node/attribute tree that LSX
stores as XML; DOS2 ships most RootTemplates and level data as ``.lsf``.
:func:`parse_lsf` produces the exact same :class:`~dos2forge.parsers.lsx.LsxDocument`
structure as :func:`~dos2forge.parsers.lsx.parse_lsx`, so all downstream
code works with either format.

File layout (little-endian; struct layouts follow Norbyte's LSLib, the
reference implementation)::

    char[4] magic          "LSOF"
    u32     version        1..7 (DOS2 DE ships 3; BG3 ships 5..7)
    i64     engine_version (i32 before version 5)
    -- metadata --
    version < 6 ("V5"): strings/nodes/attributes/values sizes (u32 pairs
        of uncompressed size + size on disk), u8 compression flags,
        u8+u16 padding, u32 metadata_format
    version >= 6 ("V6"): same but with an extra keys size pair after the
        strings sizes
    -- sections, in stream order --
    strings, nodes, attributes, values [, keys]

Sections are individually compressed per the compression flags (method
in the low nibble, as in .pak files).  From version 2 on, LZ4 sections
other than the string table use the LZ4 *frame* format ("chunked");
the string table always uses a raw LZ4 block.

Node/attribute tables come in two layouts: compact "V2" entries (values
stored sequentially, attributes reference their owner node — what DOS2
data uses) and extended "V3" entries with sibling/next links and explicit
value offsets, used when ``metadata_format == KEYS_AND_ADJACENCY``.

DOS2-era translated strings store their display text *inline* next to
the handle (BG3 stores only the handle); both are preserved, with the
inline text as the attribute value.
"""

from __future__ import annotations

import base64
import struct
import uuid
import zlib
from dataclasses import dataclass

from ..pak import lz4compat
from ..pak.format import CompressionMethod
from .lsx import LsxAttribute, LsxDocument, LsxNode
from .types import TYPE_IDS, TYPE_NAMES

MAGIC = b"LSOF"

# Version numbering follows LSLib's LSFVersion enum exactly.
VER_INITIAL = 1
VER_CHUNKED_COMPRESS = 2
VER_EXTENDED_NODES = 3  # what DOS2 DE ships
VER_BG3 = 4
VER_BG3_EXTENDED_HEADER = 5
VER_BG3_NODE_KEYS = 6
VER_BG3_PATCH3 = 7
MAX_VERSION = VER_BG3_PATCH3
DEFAULT_WRITE_VERSION = VER_EXTENDED_NODES

METADATA_KEYS_AND_ADJACENCY = 1

_MAGIC_STRUCT = struct.Struct("<4sI")
_METADATA_V5 = struct.Struct("<8IBBHI")
_METADATA_V6 = struct.Struct("<10IBBHI")
_NODE_V2 = struct.Struct("<Iii")
_NODE_V3 = struct.Struct("<Iiii")
_ATTR_V2 = struct.Struct("<IIi")
_ATTR_V3 = struct.Struct("<IIiI")
_KEY_ENTRY = struct.Struct("<II")

_STRING_TYPE_IDS = {20, 21, 22, 23, 29, 30}
_SCALAR_FORMATS = {
    1: "<B", 2: "<h", 3: "<H", 4: "<i", 5: "<I", 6: "<f", 7: "<d",
    24: "<Q", 26: "<q", 27: "<b", 32: "<q",
}
_SCALAR_STRUCTS = {tid: struct.Struct(fmt) for tid, fmt in _SCALAR_FORMATS.items()}
_VECTOR_SHAPES = {  # type id → (element format char, count)
    8: ("i", 2), 9: ("i", 3), 10: ("i", 4),
    11: ("f", 2), 12: ("f", 3), 13: ("f", 4),
    14: ("f", 4), 15: ("f", 9), 16: ("f", 12), 17: ("f", 12), 18: ("f", 16),
}
_TRANSLATED_STRING = 28
_TRANSLATED_FS_STRING = 33
_BOOL = 19
_GUID = 31
_SCRATCH_BUFFER = 25


def _guid_to_text(buf: bytes) -> str:
    """Canonical GUID text for Larian's LSF byte layout.

    LSF stores the first three GUID groups little-endian (like Microsoft
    ``bytes_le``) but the last two groups as four little-endian 16-bit
    words — one extra pairwise swap over ``bytes_le``.  This matches
    LSLib's ``ByteSwapGuid``, which is shared across every game/version.
    """
    tail = bytes(buf[i + 1 - 2 * (i % 2)] for i in range(8, 16))
    return str(uuid.UUID(bytes_le=buf[:8] + tail))


def _guid_from_text(value: str) -> bytes:
    """Inverse of :func:`_guid_to_text` (byte-identical round trip)."""
    raw = uuid.UUID(value).bytes_le
    tail = bytes(raw[i + 1 - 2 * (i % 2)] for i in range(8, 16))
    return raw[:8] + tail


class LsfError(ValueError):
    pass


def is_lsf(data: bytes) -> bool:
    return data[:4] == MAGIC


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

@dataclass
class _NodeInfo:
    name_ref: int
    parent: int
    first_attr: int
    key: str | None = None


@dataclass
class _AttrInfo:
    name_ref: int
    type_id: int
    length: int
    offset: int
    next_attr: int = -1


def parse_lsf(data: bytes) -> LsxDocument:
    """Parse an LSF resource.

    Raises :class:`LsfError` (a ``ValueError``) for *any* malformed input:
    the guards below catch the specific failure shapes, and this wrapper
    guarantees the contract for anything they miss, so callers can record
    a bad file instead of aborting a whole collection load.
    """
    try:
        return _parse_lsf(data)
    except LsfError:
        raise
    except (
        struct.error,
        IndexError,
        OverflowError,
        zlib.error,
        lz4compat.LZ4Error,
        lz4compat.DecompressionBombError,
    ) as exc:
        raise LsfError(f"malformed LSF resource: {exc}") from exc


def _parse_lsf(data: bytes) -> LsxDocument:
    if len(data) < _MAGIC_STRUCT.size:
        raise LsfError("file too small to be an LSF resource")
    magic, version = _MAGIC_STRUCT.unpack_from(data)
    if magic != MAGIC:
        raise LsfError(f"not an LSF resource (magic {magic!r})")
    if not VER_INITIAL <= version <= MAX_VERSION:
        raise LsfError(f"unsupported LSF version {version}")

    pos = _MAGIC_STRUCT.size
    if version >= VER_BG3_EXTENDED_HEADER:
        (engine_version,) = struct.unpack_from("<q", data, pos)
        pos += 8
        game_version = unpack_version64(engine_version)
    else:
        (engine_version,) = struct.unpack_from("<i", data, pos)
        pos += 4
        game_version = unpack_version32(engine_version)

    if version < VER_BG3_NODE_KEYS:
        fields = _METADATA_V5.unpack_from(data, pos)
        pos += _METADATA_V5.size
        (strings_unc, strings_disk, nodes_unc, nodes_disk, attrs_unc,
         attrs_disk, values_unc, values_disk, flags, _, _, meta_fmt) = fields
        keys_unc = keys_disk = 0
    else:
        fields = _METADATA_V6.unpack_from(data, pos)
        pos += _METADATA_V6.size
        (strings_unc, strings_disk, keys_unc, keys_disk, nodes_unc,
         nodes_disk, attrs_unc, attrs_disk, values_unc, values_disk,
         flags, _, _, meta_fmt) = fields

    try:
        method = CompressionMethod(flags & 0x0F)
    except ValueError:
        raise LsfError(f"unknown compression method {flags & 0x0F}") from None
    chunked_ok = version >= VER_CHUNKED_COMPRESS

    names_blob, pos = _read_section(data, pos, strings_disk, strings_unc, method, False)
    nodes_blob, pos = _read_section(data, pos, nodes_disk, nodes_unc, method, chunked_ok)
    attrs_blob, pos = _read_section(data, pos, attrs_disk, attrs_unc, method, chunked_ok)
    values_blob, pos = _read_section(data, pos, values_disk, values_unc, method, chunked_ok)
    keys_blob = b""
    if meta_fmt == METADATA_KEYS_AND_ADJACENCY:
        keys_blob, pos = _read_section(data, pos, keys_disk, keys_unc, method, chunked_ok)

    names = _parse_names(names_blob)
    has_adjacency = (
        version >= VER_EXTENDED_NODES and meta_fmt == METADATA_KEYS_AND_ADJACENCY
    )
    node_infos = _parse_nodes(nodes_blob, has_adjacency)
    attr_infos, attrs_by_node = _parse_attrs(attrs_blob, has_adjacency)
    _apply_keys(keys_blob, node_infos, names)

    bg3_translated = version >= VER_BG3 or _is_bg3_engine(game_version)
    return _build_document(
        names, node_infos, attr_infos, attrs_by_node, values_blob,
        has_adjacency, bg3_translated,
    )


def load_lsf(path) -> LsxDocument:
    from pathlib import Path

    return parse_lsf(Path(path).read_bytes())


def _read_section(
    data: bytes,
    pos: int,
    size_on_disk: int,
    uncompressed_size: int,
    method: CompressionMethod,
    allow_chunked: bool,
) -> tuple[bytes, int]:
    if size_on_disk == 0 and uncompressed_size != 0:
        blob = data[pos : pos + uncompressed_size]
        if len(blob) != uncompressed_size:
            raise LsfError("truncated section")
        return blob, pos + uncompressed_size
    if size_on_disk == 0:
        return b"", pos

    stored_size = size_on_disk if method is not CompressionMethod.NONE else uncompressed_size
    raw = data[pos : pos + stored_size]
    if len(raw) != stored_size:
        raise LsfError("truncated section")
    pos += stored_size

    if method is CompressionMethod.NONE:
        blob = raw
    elif method is CompressionMethod.ZLIB:
        blob = lz4compat.zlib_decompress(raw)
    elif method is CompressionMethod.LZ4:
        # uncompressed_size is attacker-controlled metadata; bound both
        # paths so a corrupt/hostile section can't drive a huge alloc.
        if allow_chunked:
            blob = lz4compat.decompress_frame(raw, max_output_size=uncompressed_size)
        else:
            lz4compat.guard_size(
                len(raw), uncompressed_size, lz4compat.MAX_RATIO_LZ4, "LSF section"
            )
            blob = lz4compat.decompress(raw, uncompressed_size)
    elif method is CompressionMethod.ZSTD:
        # Only BG3-era resources use zstd; DOS2 never produces it.
        raise LsfError("zstd-compressed LSF section — a BG3-era resource")
    else:  # pragma: no cover - CompressionMethod is exhaustive
        raise LsfError(f"unsupported compression {method}")
    if len(blob) != uncompressed_size:
        raise LsfError(
            f"section size mismatch: got {len(blob)}, expected {uncompressed_size}"
        )
    return blob, pos


def _parse_names(blob: bytes) -> list[list[str]]:
    if not blob:
        return []
    pos = 4
    buckets: list[list[str]] = []
    try:
        (num_buckets,) = struct.unpack_from("<I", blob, 0)
        for _ in range(num_buckets):
            (count,) = struct.unpack_from("<H", blob, pos)
            pos += 2
            bucket: list[str] = []
            for _ in range(count):
                (length,) = struct.unpack_from("<H", blob, pos)
                pos += 2
                bucket.append(blob[pos : pos + length].decode("utf-8", errors="replace"))
                pos += length
            buckets.append(bucket)
    except struct.error as exc:
        raise LsfError("truncated string table") from exc
    return buckets


def _resolve_name(names: list[list[str]], ref: int) -> str:
    bucket, offset = ref >> 16, ref & 0xFFFF
    try:
        return names[bucket][offset]
    except IndexError:
        raise LsfError(f"dangling name reference {ref:#x}") from None


def _parse_nodes(blob: bytes, has_adjacency: bool) -> list[_NodeInfo]:
    layout = _NODE_V3 if has_adjacency else _NODE_V2
    if len(blob) % layout.size:
        raise LsfError("node table size is not a multiple of the entry size")
    infos = []
    for entry_pos in range(0, len(blob), layout.size):
        if has_adjacency:
            name_ref, parent, _next_sibling, first_attr = layout.unpack_from(blob, entry_pos)
        else:
            name_ref, first_attr, parent = layout.unpack_from(blob, entry_pos)
        infos.append(_NodeInfo(name_ref=name_ref, parent=parent, first_attr=first_attr))
    return infos


def _parse_attrs(
    blob: bytes, has_adjacency: bool
) -> tuple[list[_AttrInfo], dict[int, list[int]]]:
    """Returns (attribute infos, per-node attribute indices for V2 layout)."""
    attrs: list[_AttrInfo] = []
    attrs_by_node: dict[int, list[int]] = {}
    layout = _ATTR_V3 if has_adjacency else _ATTR_V2
    if len(blob) % layout.size:
        raise LsfError("attribute table size is not a multiple of the entry size")
    data_offset = 0
    for index, entry_pos in enumerate(range(0, len(blob), layout.size)):
        if has_adjacency:
            name_ref, type_and_length, next_attr, offset = layout.unpack_from(blob, entry_pos)
            attrs.append(
                _AttrInfo(
                    name_ref=name_ref,
                    type_id=type_and_length & 0x3F,
                    length=type_and_length >> 6,
                    offset=offset,
                    next_attr=next_attr,
                )
            )
        else:
            name_ref, type_and_length, node_index = layout.unpack_from(blob, entry_pos)
            info = _AttrInfo(
                name_ref=name_ref,
                type_id=type_and_length & 0x3F,
                length=type_and_length >> 6,
                offset=data_offset,
            )
            data_offset += info.length
            attrs.append(info)
            attrs_by_node.setdefault(node_index, []).append(index)
    return attrs, attrs_by_node


def _apply_keys(blob: bytes, nodes: list[_NodeInfo], names: list[list[str]]) -> None:
    if len(blob) % _KEY_ENTRY.size:
        raise LsfError("key table size is not a multiple of the entry size")
    for entry_pos in range(0, len(blob), _KEY_ENTRY.size):
        node_index, name_ref = _KEY_ENTRY.unpack_from(blob, entry_pos)
        if node_index >= len(nodes):
            raise LsfError(f"key entry references missing node {node_index}")
        nodes[node_index].key = _resolve_name(names, name_ref)


def _build_document(
    names: list[list[str]],
    node_infos: list[_NodeInfo],
    attr_infos: list[_AttrInfo],
    attrs_by_node: dict[int, list[int]],
    values: bytes,
    has_adjacency: bool,
    bg3_translated: bool,
) -> LsxDocument:
    document = LsxDocument()
    instances: list[LsxNode] = []
    for index, info in enumerate(node_infos):
        node = LsxNode(id=_resolve_name(names, info.name_ref), key=info.key)
        if has_adjacency:
            attr_index = info.first_attr
            steps = 0
            while attr_index != -1:
                # first_attr/next_attr come straight from the file: reject
                # out-of-range indices (negative values would silently wrap
                # as Python indexing) and chains longer than the table
                # itself, which can only mean a cycle.
                if not 0 <= attr_index < len(attr_infos):
                    raise LsfError(
                        f"node {index} references invalid attribute {attr_index}"
                    )
                steps += 1
                if steps > len(attr_infos):
                    raise LsfError(f"attribute chain of node {index} loops")
                attr_info = attr_infos[attr_index]
                attr = _decode_attribute(names, attr_info, values, bg3_translated)
                node.attributes[attr.id] = attr
                attr_index = attr_info.next_attr
        else:
            for attr_index in attrs_by_node.get(index, ()):
                attr_info = attr_infos[attr_index]
                attr = _decode_attribute(names, attr_info, values, bg3_translated)
                node.attributes[attr.id] = attr
        instances.append(node)
        if info.parent == -1:
            document.regions[node.id] = node
        else:
            if not 0 <= info.parent < len(instances):
                raise LsfError(f"node {index} references invalid parent {info.parent}")
            instances[info.parent].children.append(node)
    return document


def _decode_attribute(
    names: list[list[str]], info: _AttrInfo, values: bytes, bg3_translated: bool
) -> LsxAttribute:
    name = _resolve_name(names, info.name_ref)
    buf = values[info.offset : info.offset + info.length]
    if len(buf) != info.length:
        raise LsfError(f"attribute {name!r} value is out of bounds")
    type_id = info.type_id
    type_name = TYPE_NAMES.get(type_id, f"unknown_{type_id}")

    if type_id in _STRING_TYPE_IDS:
        return LsxAttribute(id=name, type=type_name, value=_cut_string(buf))
    if type_id in (_TRANSLATED_STRING, _TRANSLATED_FS_STRING):
        try:
            if type_id == _TRANSLATED_STRING:
                value, handle, version = _decode_translated(buf, bg3_translated)
            else:
                value, handle, version, _ = _decode_translated_fs(buf, 0, bg3_translated)
        except struct.error as exc:
            raise LsfError(f"attribute {name!r} has a malformed {type_name}") from exc
        # DOS2-era files carry the display text inline; keep it as the
        # attribute value so lookups work without a localization table.
        return LsxAttribute(
            id=name, type=type_name, value=value, handle=handle, version=version
        )
    # The stored length is file-controlled and independent of the type id;
    # a mismatch must fail as LsfError, not leak struct.error/IndexError.
    if type_id in _SCALAR_STRUCTS:
        layout = _SCALAR_STRUCTS[type_id]
        if len(buf) != layout.size:
            raise LsfError(
                f"attribute {name!r} has {len(buf)} bytes, "
                f"{type_name} needs {layout.size}"
            )
        (value,) = layout.unpack(buf)
        return LsxAttribute(id=name, type=type_name, value=_fmt_scalar(value))
    if type_id in _VECTOR_SHAPES:
        elem, count = _VECTOR_SHAPES[type_id]
        if len(buf) != count * 4:
            raise LsfError(
                f"attribute {name!r} has {len(buf)} bytes, "
                f"{type_name} needs {count * 4}"
            )
        parts = struct.unpack(f"<{count}{elem}", buf)
        return LsxAttribute(
            id=name, type=type_name, value=" ".join(_fmt_scalar(p) for p in parts)
        )
    if type_id == _BOOL:
        if not buf:
            raise LsfError(f"attribute {name!r} has an empty bool value")
        return LsxAttribute(id=name, type=type_name, value="True" if buf[0] else "False")
    if type_id == _GUID:
        if len(buf) != 16:
            raise LsfError(f"attribute {name!r} has {len(buf)} bytes, guid needs 16")
        return LsxAttribute(id=name, type=type_name, value=_guid_to_text(buf))
    if type_id == _SCRATCH_BUFFER:
        return LsxAttribute(
            id=name, type=type_name, value=base64.b64encode(buf).decode("ascii")
        )
    if type_id == 0:  # None
        return LsxAttribute(id=name, type=type_name, value="")
    # Unknown/future type: preserve the raw bytes as hex so nothing is lost.
    return LsxAttribute(id=name, type=type_name, value=buf.hex())


def _cut_string(buf: bytes) -> str:
    return buf.rstrip(b"\x00").decode("utf-8", errors="replace")


def _check_length(value: int, what: str) -> int:
    """Reject negative length fields: they move ``pos`` backwards, silently
    re-reading earlier bytes as new data (or stalling forever)."""
    if value < 0:
        raise LsfError(f"negative {what} {value} in translated string")
    return value


def _decode_translated(buf: bytes, bg3: bool) -> tuple[str | None, str, int]:
    """Returns (inline value or None, handle, version)."""
    pos = 0
    version = 0
    value: str | None = None
    if bg3:
        (version,) = struct.unpack_from("<H", buf, pos)
        pos += 2
    else:
        (value_length,) = struct.unpack_from("<i", buf, pos)
        pos += 4
        value = _cut_string(buf[pos : pos + _check_length(value_length, "value length")])
        pos += value_length
    (handle_length,) = struct.unpack_from("<i", buf, pos)
    pos += 4
    handle = _cut_string(buf[pos : pos + _check_length(handle_length, "handle length")])
    return value, handle, version


#: Argument lists nest one level per argument; retail data nests once or
#: twice.  Anything deeper is a corrupt or crafted file, not real data.
_MAX_TRANSLATED_DEPTH = 32


def _decode_translated_fs(
    buf: bytes, pos: int, bg3: bool, depth: int = 0
) -> tuple[str | None, str, int, int]:
    if depth > _MAX_TRANSLATED_DEPTH:
        raise LsfError("TranslatedFSString arguments nest too deeply")
    version = 0
    value: str | None = None
    if bg3:
        (version,) = struct.unpack_from("<H", buf, pos)
        pos += 2
    else:
        (value_length,) = struct.unpack_from("<i", buf, pos)
        pos += 4
        _check_length(value_length, "value length")
        value = _cut_string(buf[pos : pos + value_length])
        pos += value_length
    (handle_length,) = struct.unpack_from("<i", buf, pos)
    pos += 4
    _check_length(handle_length, "handle length")
    handle = _cut_string(buf[pos : pos + handle_length])
    pos += handle_length
    (num_arguments,) = struct.unpack_from("<i", buf, pos)
    pos += 4
    if not 0 <= num_arguments <= len(buf):
        raise LsfError(f"implausible argument count {num_arguments} in translated string")
    for _ in range(num_arguments):
        (key_length,) = struct.unpack_from("<i", buf, pos)
        pos += 4 + _check_length(key_length, "key length")
        _, _, _, pos = _decode_translated_fs(buf, pos, bg3, depth + 1)
        (value_length,) = struct.unpack_from("<i", buf, pos)
        pos += 4 + _check_length(value_length, "value length")
    return value, handle, version, pos


def _fmt_scalar(value) -> str:
    if isinstance(value, float):
        return repr(value)
    return str(value)


def unpack_version64(value: int) -> tuple[int, int, int, int]:
    """Split a Larian ``Version64`` integer into (major, minor, build, revision)."""
    return (
        (value >> 55) & 0x1FF,
        (value >> 47) & 0xFF,
        (value >> 31) & 0xFFFF,
        value & 0x7FFFFFFF,
    )


def unpack_version32(value: int) -> tuple[int, int, int, int]:
    """Split a Larian ``Version32`` integer into (major, minor, revision, build)."""
    return (
        (value >> 28) & 0x0F,
        (value >> 24) & 0x0F,
        (value >> 16) & 0xFF,
        value & 0xFFFF,
    )


def pack_version32(major: int, minor: int, revision: int, build: int) -> int:
    """Pack (major, minor, revision, build) into a Larian ``Version32`` integer."""
    for name, value, bits in (
        ("major", major, 4), ("minor", minor, 4),
        ("revision", revision, 8), ("build", build, 16),
    ):
        if not 0 <= value < (1 << bits):
            raise ValueError(f"{name} {value} does not fit in {bits} bits")
    return (major << 28) | (minor << 24) | (revision << 16) | build


def _is_bg3_engine(version: tuple[int, int, int, int]) -> bool:
    major, _, revision, build = version
    return major > 4 or (major == 4 and (revision > 0 or build >= 0x1A))


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

#: DOS2 DE engine version (3.6.4.0) as a packed Version32.
_ENGINE_VERSION_DOS2 = pack_version32(3, 6, 4, 0)
_ENGINE_VERSION_BG3 = (4 << 55) | (1 << 47) | (1 << 31) | 100
_NAME_BUCKETS = 0x200


class _NameTable:
    def __init__(self):
        self._buckets: list[list[str]] = [[] for _ in range(_NAME_BUCKETS)]
        self._refs: dict[str, int] = {}

    def ref(self, name: str) -> int:
        if name not in self._refs:
            bucket = zlib.crc32(name.encode("utf-8")) & (_NAME_BUCKETS - 1)
            offset = len(self._buckets[bucket])
            if offset > 0xFFFF:
                raise LsfError("string table bucket overflow")
            self._buckets[bucket].append(name)
            self._refs[name] = (bucket << 16) | offset
        return self._refs[name]

    def pack(self) -> bytes:
        out = bytearray(struct.pack("<I", _NAME_BUCKETS))
        for bucket in self._buckets:
            out += struct.pack("<H", len(bucket))
            for name in bucket:
                raw = name.encode("utf-8")
                out += struct.pack("<H", len(raw)) + raw
        return bytes(out)


def write_lsf(
    document: LsxDocument,
    version: int = DEFAULT_WRITE_VERSION,
    compression: CompressionMethod = CompressionMethod.NONE,
) -> bytes:
    """Serialize a document to LSF.

    Versions 1-3 write the DOS2-native layout: compact (V2) tables, V5
    metadata, and a 32-bit engine version — version 3 (the default)
    matches DOS2 DE retail output.  Versions 5-7 write the BG3-era
    layouts (extended header; keyed V3 tables from 6 up).  Regions are
    stored by their root node's id (LSF has no separate region name).
    """
    if version not in (
        VER_INITIAL, VER_CHUNKED_COMPRESS, VER_EXTENDED_NODES,
        VER_BG3_EXTENDED_HEADER, VER_BG3_NODE_KEYS, VER_BG3_PATCH3,
    ):
        raise LsfError(f"unsupported write version {version} (use 1-3 or 5-7)")
    keyed = version >= VER_BG3_NODE_KEYS
    legacy_translated = version < VER_BG3

    names = _NameTable()
    flat: list[tuple[LsxNode, int]] = []  # (node, parent index) in DFS order

    def visit(node: LsxNode, parent: int) -> None:
        index = len(flat)
        flat.append((node, parent))
        for child in node.children:
            visit(child, index)

    for root in document.regions.values():
        visit(root, -1)

    node_blob = bytearray()
    attr_blob = bytearray()
    value_blob = bytearray()
    key_blob = bytearray()

    # Attributes of each node are contiguous, in node order, so the V2
    # sequential-value invariant and the V3 next-links are both trivial.
    children_of: dict[int, list[int]] = {}
    for index, (_, parent) in enumerate(flat):
        children_of.setdefault(parent, []).append(index)

    attr_count = 0
    for index, (node, parent) in enumerate(flat):
        first_attr = attr_count if node.attributes else -1
        for position, attr in enumerate(node.attributes.values()):
            encoded = _encode_attribute(attr, legacy_translated)
            type_id = TYPE_IDS.get(attr.type)
            if type_id is None:
                raise LsfError(f"cannot encode attribute type {attr.type!r}")
            type_and_length = type_id | (len(encoded) << 6)
            name_ref = names.ref(attr.id)
            if keyed:
                is_last = position == len(node.attributes) - 1
                next_attr = -1 if is_last else attr_count + 1
                attr_blob += _ATTR_V3.pack(name_ref, type_and_length, next_attr, len(value_blob))
            else:
                attr_blob += _ATTR_V2.pack(name_ref, type_and_length, index)
            value_blob += encoded
            attr_count += 1

        name_ref = names.ref(node.id)
        if keyed:
            siblings = children_of[parent]
            sibling_pos = siblings.index(index)
            next_sibling = (
                siblings[sibling_pos + 1] if sibling_pos + 1 < len(siblings) else -1
            )
            node_blob += _NODE_V3.pack(name_ref, parent, next_sibling, first_attr)
            if node.key:
                key_blob += _KEY_ENTRY.pack(index, names.ref(node.key))
        else:
            node_blob += _NODE_V2.pack(name_ref, first_attr, parent)

    name_blob = names.pack()

    chunked_ok = version >= VER_CHUNKED_COMPRESS
    strings = _write_section(name_blob, compression, False)
    nodes = _write_section(bytes(node_blob), compression, chunked_ok)
    attrs = _write_section(bytes(attr_blob), compression, chunked_ok)
    values = _write_section(bytes(value_blob), compression, chunked_ok)
    keys = _write_section(bytes(key_blob), compression, chunked_ok)

    meta_fmt = METADATA_KEYS_AND_ADJACENCY if keyed else 0
    out = bytearray()
    out += _MAGIC_STRUCT.pack(MAGIC, version)
    if version >= VER_BG3_EXTENDED_HEADER:
        out += struct.pack("<q", _ENGINE_VERSION_BG3)
    else:
        out += struct.pack("<i", _ENGINE_VERSION_DOS2)
    if keyed:
        out += _METADATA_V6.pack(
            len(name_blob), strings.size_on_disk,
            len(key_blob), keys.size_on_disk,
            len(node_blob), nodes.size_on_disk,
            len(attr_blob), attrs.size_on_disk,
            len(value_blob), values.size_on_disk,
            int(compression), 0, 0, meta_fmt,
        )
    else:
        out += _METADATA_V5.pack(
            len(name_blob), strings.size_on_disk,
            len(node_blob), nodes.size_on_disk,
            len(attr_blob), attrs.size_on_disk,
            len(value_blob), values.size_on_disk,
            int(compression), 0, 0, meta_fmt,
        )
    out += strings.data + nodes.data + attrs.data + values.data
    if keyed:
        out += keys.data
    return bytes(out)


@dataclass
class _Section:
    data: bytes
    size_on_disk: int


def _write_section(blob: bytes, method: CompressionMethod, chunked: bool) -> _Section:
    if not blob:
        return _Section(b"", 0)
    if method is CompressionMethod.NONE:
        # size_on_disk == 0 with a non-zero uncompressed size marks the
        # section as stored raw.
        return _Section(blob, 0)
    if method is CompressionMethod.ZLIB:
        compressed = zlib.compress(blob)
    elif method is CompressionMethod.LZ4:
        compressed = (
            lz4compat.compress_frame(blob) if chunked else lz4compat.compress(blob)
        )
    else:
        raise LsfError(f"unsupported write compression: {method}")
    return _Section(compressed, len(compressed))


def _encode_attribute(attr: LsxAttribute, legacy_translated: bool) -> bytes:
    type_id = TYPE_IDS.get(attr.type)
    if type_id is None:
        raise LsfError(f"cannot encode attribute type {attr.type!r}")
    value = attr.value

    if type_id in _STRING_TYPE_IDS:
        return (value or "").encode("utf-8") + b"\x00"
    if type_id == _TRANSLATED_STRING:
        handle = (attr.handle or "").encode("utf-8") + b"\x00"
        if legacy_translated:
            inline = (value or "").encode("utf-8") + b"\x00"
            return (
                struct.pack("<i", len(inline)) + inline
                + struct.pack("<i", len(handle)) + handle
            )
        return struct.pack("<H", attr.version or 1) + struct.pack("<i", len(handle)) + handle
    if type_id == _TRANSLATED_FS_STRING:
        handle = (attr.handle or "").encode("utf-8") + b"\x00"
        if legacy_translated:
            inline = (value or "").encode("utf-8") + b"\x00"
            return (
                struct.pack("<i", len(inline)) + inline
                + struct.pack("<i", len(handle)) + handle
                + struct.pack("<i", 0)  # no arguments
            )
        return (
            struct.pack("<H", attr.version or 1)
            + struct.pack("<i", len(handle)) + handle
            + struct.pack("<i", 0)  # no arguments
        )
    if type_id in _SCALAR_FORMATS:
        fmt = _SCALAR_FORMATS[type_id]
        number = float(value or 0) if fmt[-1] in "fd" else int(value or 0)
        return struct.pack(fmt, number)
    if type_id in _VECTOR_SHAPES:
        elem, count = _VECTOR_SHAPES[type_id]
        parts = (value or "").split()
        if len(parts) != count:
            raise LsfError(f"{attr.type} needs {count} components, got {len(parts)}")
        numbers = [float(p) if elem == "f" else int(p) for p in parts]
        return struct.pack(f"<{count}{elem}", *numbers)
    if type_id == _BOOL:
        return b"\x01" if (value or "").lower() in ("true", "1") else b"\x00"
    if type_id == _GUID:
        return _guid_from_text(value or "00000000-0000-0000-0000-000000000000")
    if type_id == _SCRATCH_BUFFER:
        return base64.b64decode(value or "")
    if type_id == 0:
        return b""
    raise LsfError(f"cannot encode attribute type {attr.type!r}")
