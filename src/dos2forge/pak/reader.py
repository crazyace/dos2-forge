"""Reading Larian LSPK (.pak) archives (DOS2 v10/v13 and BG3-era v15+)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from . import lz4compat
from .format import (
    FILE_LIST_HEADER_STRUCT,
    SIGNATURE,
    TRAILER13_STRUCT,
    CompressionMethod,
    PakEntry,
    PakHeader,
)


class PakError(ValueError):
    pass


def file_is_lspk(path: str | Path) -> bool:
    """True when the file on disk carries the LSPK signature.

    Distinguishes a *damaged* archive (worth reporting) from secondary
    archive parts and foreign files (routinely skipped): only the former
    carry the signature.  v13 archives sign the *end* of the file, so
    both locations are checked.
    """
    try:
        with Path(path).open("rb") as fh:
            if fh.read(4) == SIGNATURE:
                return True
            fh.seek(0, 2)
            size = fh.tell()
            if size < TRAILER13_STRUCT.size:
                return False
            fh.seek(size - 4)
            return fh.read(4) == SIGNATURE
    except OSError:
        return False


class PakReader:
    """Random-access reader for a .pak archive.

    Multi-part archives (``Textures.pak`` + ``Textures_1.pak`` …) are
    handled transparently: entries whose ``archive_part`` is non-zero are
    read from the sibling part file.  Solid archives (all data in one LZ4
    frame, common for DOS2 DE retail paks) are decompressed once up front
    and served from memory.

    Usage::

        with PakReader("Shared.pak") as pak:
            for entry in pak:
                ...
            data = pak.read("Public/Shared/Stats/Generated/Data/Weapon.txt")
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fh = self.path.open("rb")
        self._part_handles: dict[int, object] = {0: self._fh}
        self._solid_frame: bytes | None = None
        self._solid_offsets: dict[str, int] = {}
        try:
            self.header = PakHeader.read(self._fh)
            self._entries = self._read_file_list()
            if self.header.solid and self._entries:
                self._load_solid_frame()
        except PakError:
            self.close()
            raise
        except ValueError as exc:
            self.close()
            raise PakError(str(exc)) from exc
        except Exception:
            self.close()
            raise
        self._by_name = {e.name: e for e in self._entries}

    # -- container protocol -------------------------------------------------

    def __enter__(self) -> "PakReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __iter__(self) -> Iterator[PakEntry]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def close(self) -> None:
        for handle in self._part_handles.values():
            handle.close()
        self._part_handles = {}
        self._solid_frame = None

    # -- public API ----------------------------------------------------------

    @property
    def entries(self) -> list[PakEntry]:
        return list(self._entries)

    def names(self) -> list[str]:
        return [e.name for e in self._entries]

    def entry(self, name: str) -> PakEntry:
        try:
            return self._by_name[name]
        except KeyError:
            raise PakError(f"{name!r} not found in {self.path.name}") from None

    def read(self, name_or_entry: str | PakEntry) -> bytes:
        """Return the decompressed content of an archived file."""
        entry = (
            name_or_entry
            if isinstance(name_or_entry, PakEntry)
            else self.entry(name_or_entry)
        )
        if self._solid_frame is not None:
            start = self._solid_offsets[entry.name]
            return self._solid_frame[start : start + entry.uncompressed_size]
        fh = self._part_handle(entry.archive_part)
        fh.seek(entry.offset)
        raw = fh.read(entry.size_on_disk)
        if len(raw) != entry.size_on_disk:
            raise PakError(f"truncated data for {entry.name!r}")
        return _decompress(raw, entry.compression, entry.uncompressed_size, entry.name)

    # -- internals -----------------------------------------------------------

    def _read_file_list(self) -> list[PakEntry]:
        header = self.header
        entry_size = header.entry_size

        if header.version <= 10:
            # v10: an uncompressed entry table sits right after the header.
            self._fh.seek(header.file_list_offset)
            table_size = header.num_files * entry_size
            if table_size > header.data_offset:
                raise PakError(
                    f"implausible file count {header.num_files} in v10 header"
                )
            table = self._fh.read(table_size)
            if len(table) != table_size:
                raise PakError("truncated file list")
            entries = PakEntry.parse_all(table, header.version)
            # Part-0 offsets are relative to data_offset.
            return [
                e if e.archive_part != 0
                else _with_offset(e, e.offset + header.data_offset)
                for e in entries
            ]

        self._fh.seek(header.file_list_offset)
        if header.version <= 13:
            # v13: i32 count, then (file_list_size - 4) compressed bytes.
            raw_count = self._fh.read(4)
            if len(raw_count) != 4:
                raise PakError("truncated file list header")
            num_files = int.from_bytes(raw_count, "little")
            compressed_size = header.file_list_size - 4
            if compressed_size < 0:
                raise PakError("implausible v13 file list size")
        else:
            raw_header = self._fh.read(FILE_LIST_HEADER_STRUCT.size)
            if len(raw_header) != FILE_LIST_HEADER_STRUCT.size:
                raise PakError("truncated file list header")
            num_files, compressed_size = FILE_LIST_HEADER_STRUCT.unpack(raw_header)

        table_size = num_files * entry_size
        # An LZ4 block expands at most ~255x, so a table_size far beyond
        # that bound means num_files is corrupt.  Reject it here, before
        # the decompressor pre-allocates a buffer of that size.
        if table_size > compressed_size * 256 + 4096:
            raise PakError(f"implausible file count {num_files} in file list")
        compressed = self._fh.read(compressed_size)
        if len(compressed) != compressed_size:
            raise PakError("truncated file list")
        try:
            table = lz4compat.decompress(compressed, table_size)
        except lz4compat.LZ4Error as exc:
            raise PakError(f"corrupt file list: {exc}") from exc
        return PakEntry.parse_all(table, header.version)

    def _load_solid_frame(self) -> None:
        """Decompress a solid archive's single LZ4 frame up front.

        Layout per LSLib: the frame starts at file offset 0 (its 7-byte
        header puts the first entry at offset 7) and entries tile it
        contiguously in offset order.  Entry data inside the decompressed
        frame is laid out in the same order, each spanning its
        uncompressed size.
        """
        ordered = sorted(self._entries, key=lambda e: e.offset)
        expected_offset = 7
        total_uncompressed = 0
        for entry in ordered:
            if entry.offset != expected_offset:
                raise PakError("file list in solid archive not contiguous")
            expected_offset += entry.size_on_disk
            self._solid_offsets[entry.name] = total_uncompressed
            total_uncompressed += entry.uncompressed_size
        # The entry spans stop just short of the frame's end marker; the
        # data region runs up to the file list (v13 keeps its header at
        # the end of the file), so read that far to get the whole frame.
        frame_end = (
            self.header.file_list_offset
            if self.header.version == 13
            else expected_offset
        )
        if frame_end < expected_offset:
            raise PakError("solid archive data region smaller than its entries")
        self._fh.seek(0)
        frame = self._fh.read(frame_end)
        if len(frame) != frame_end:
            raise PakError("truncated solid archive")
        try:
            # Retail solid frames carry no EndMark: the blocks run right
            # up to the file list and simply stop (allow_truncated); the
            # exact-size check below is the integrity guarantee instead.
            self._solid_frame = lz4compat.decompress_frame(
                frame, max_output_size=total_uncompressed, allow_truncated=True
            )
        except (lz4compat.LZ4Error, lz4compat.DecompressionBombError) as exc:
            raise PakError(f"corrupt solid archive: {exc}") from exc
        if len(self._solid_frame) != total_uncompressed:
            raise PakError(
                f"solid archive decompressed to {len(self._solid_frame)} bytes, "
                f"expected {total_uncompressed}"
            )

    def _part_handle(self, part: int):
        if part not in self._part_handles:
            part_path = self._part_path(part)
            if not part_path.exists():
                raise PakError(f"missing archive part: {part_path.name}")
            self._part_handles[part] = part_path.open("rb")
        return self._part_handles[part]

    def _part_path(self, part: int) -> Path:
        if part == 0:
            return self.path
        return self.path.with_name(f"{self.path.stem}_{part}{self.path.suffix}")


def _with_offset(entry: PakEntry, offset: int) -> PakEntry:
    from dataclasses import replace

    return replace(entry, offset=offset)


def _decompress(
    raw: bytes, method: CompressionMethod, uncompressed_size: int, name: str
) -> bytes:
    if method is CompressionMethod.NONE:
        return raw
    if method is CompressionMethod.ZLIB:
        try:
            return lz4compat.zlib_decompress(raw)
        except lz4compat.LZ4Error as exc:
            raise PakError(f"corrupt zlib data for {name!r}: {exc}") from exc
        except ValueError as exc:  # DecompressionBombError
            raise PakError(f"{name!r}: {exc}") from exc
    if method is CompressionMethod.LZ4:
        try:
            lz4compat.guard_size(len(raw), uncompressed_size, lz4compat.MAX_RATIO_LZ4, name)
            return lz4compat.decompress(raw, uncompressed_size)
        except lz4compat.LZ4Error as exc:
            raise PakError(f"corrupt LZ4 data for {name!r}: {exc}") from exc
        except ValueError as exc:  # DecompressionBombError
            raise PakError(f"{name!r}: {exc}") from exc
    if method is CompressionMethod.ZSTD:
        # Never produced by DOS2; only reachable via BG3-era archives.
        raise PakError(
            f"{name!r} is zstd-compressed — a BG3-era archive; "
            "use bg3forge for these"
        )
    raise PakError(f"unknown compression method {method} for {name!r}")
