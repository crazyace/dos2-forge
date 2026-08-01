"""On-disk structures of Larian LSPK (.pak) archives.

Divinity: Original Sin 2 ships LSPK version 13 (Definitive Edition) and
version 10 (classic) archives; the BG3-era versions 15/16/18 are also
readable so the tool can inspect newer Larian archives.  Struct layouts
follow LSLib, the reference implementation.

* v10 archive — header at offset 0::

      char[4]  signature      "LSPK"
      u32      version        10
      u32      data_offset    absolute start of packed data
      u32      file_list_size
      u16      num_parts
      u8       flags
      u8       priority
      u32      num_files

  followed immediately by ``num_files`` *uncompressed* 280-byte entries;
  entry offsets in part 0 are relative to ``data_offset``.

* v13 archive — header at the *end* of the file (LSLib's LSPKHeader13).
  The last 8 bytes are ``i32 header_size`` + ``"LSPK"``; the header
  starts ``header_size`` bytes before EOF::

      u32      version        13
      u32      file_list_offset
      u32      file_list_size
      u16      num_parts
      u8       flags
      u8       priority
      u8[16]   md5

  The file list at ``file_list_offset`` is ``i32 num_files`` followed by
  ``file_list_size - 4`` bytes of LZ4 block-compressed 280-byte entries.

* v10/v13 file entry (280 bytes; LSLib's ``FileEntry10``/``FileEntry13``)::

      char[256] name          null-padded UTF-8 path
      u32       offset
      u32       size_on_disk
      u32       uncompressed_size (0 when stored uncompressed)
      u32       archive_part
      u32       flags          low nibble: compression method
      u32       crc

* v15/16/18 layouts match bg3-forge (64-bit offsets from v15, the packed
  272-byte entry from v18); see the docstrings there for details.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import BinaryIO

SIGNATURE = b"LSPK"
SUPPORTED_VERSIONS = (10, 13, 15, 16, 18)
DEFAULT_VERSION = 13

_MAGIC_VERSION_STRUCT = struct.Struct("<4sI")
HEADER10_STRUCT = struct.Struct("<4sIIIHBBI")  # signature + LSPKHeader10
HEADER13_STRUCT = struct.Struct("<IIIHBB16s")  # LSPKHeader13 (no signature)
TRAILER13_STRUCT = struct.Struct("<i4s")  # header_size + signature, last 8 bytes
HEADER15_STRUCT = struct.Struct("<4sIQIBB16s")  # LSPKHeader15
HEADER16_STRUCT = struct.Struct("<4sIQIBB16sH")  # LSPKHeader16 (v16 and v18)
FILE_LIST_HEADER_STRUCT = struct.Struct("<II")  # v15+: num_files + compressed size

ENTRY10_STRUCT = struct.Struct("<256sIIIIII")  # v10/v13 (FileEntry10)
ENTRY10_SIZE = ENTRY10_STRUCT.size  # 280
ENTRY15_STRUCT = struct.Struct("<256sQQQIIII")  # v15/v16 (FileEntry15)
ENTRY15_SIZE = ENTRY15_STRUCT.size  # 296
ENTRY18_STRUCT = struct.Struct("<256sIHBBII")  # v18 (FileEntry18)
ENTRY18_SIZE = ENTRY18_STRUCT.size  # 272
NAME_SIZE = 256

#: v13 tail header block: header + trailer, as LSLib writes it.
HEADER13_BLOCK_SIZE = HEADER13_STRUCT.size + TRAILER13_STRUCT.size  # 40

FLAG_ALLOW_MEMORY_MAPPING = 0x02
#: All file data is compressed into a single LZ4 frame (DOS2 DE archives).
FLAG_SOLID = 0x04
FLAG_PRELOAD = 0x08


class CompressionMethod(IntEnum):
    NONE = 0
    ZLIB = 1
    LZ4 = 2
    ZSTD = 3  # BG3-era only; never produced by DOS2


@dataclass(frozen=True)
class PakHeader:
    version: int
    file_list_offset: int
    file_list_size: int
    flags: int
    priority: int
    md5: bytes
    num_parts: int
    #: v10 only: entry count lives in the header (the uncompressed file
    #: list has no count prefix of its own).
    num_files: int = 0
    #: v10 only: part-0 entry offsets are relative to this.
    data_offset: int = 0

    @classmethod
    def read(cls, fh: BinaryIO) -> "PakHeader":
        """Read the header from an open archive, wherever it lives.

        v10/v15/v16/v18 store it at the start of the file; v13 at the
        end, found via the trailing ``header_size + "LSPK"`` record.
        """
        fh.seek(0, 2)
        file_size = fh.tell()
        fh.seek(0)
        head = fh.read(HEADER16_STRUCT.size)
        if head[:4] == SIGNATURE:
            if len(head) < _MAGIC_VERSION_STRUCT.size:
                raise ValueError("file too small to be a .pak archive")
            (_, version) = _MAGIC_VERSION_STRUCT.unpack_from(head)
            return cls._parse_start_header(head, version)
        return cls._parse_tail_header(fh, file_size)

    @classmethod
    def _parse_start_header(cls, head: bytes, version: int) -> "PakHeader":
        if version == 10:
            if len(head) < HEADER10_STRUCT.size:
                raise ValueError("file too small to be a v10 .pak archive")
            (_, _, data_offset, file_list_size, num_parts, flags, priority,
             num_files) = HEADER10_STRUCT.unpack_from(head)
            return cls(
                version=10,
                file_list_offset=HEADER10_STRUCT.size,
                file_list_size=file_list_size,
                flags=flags,
                priority=priority,
                md5=b"\x00" * 16,
                num_parts=max(num_parts, 1),
                num_files=num_files,
                data_offset=data_offset,
            )
        if version == 15:
            if len(head) < HEADER15_STRUCT.size:
                raise ValueError("file too small to be a v15 .pak archive")
            (_, _, file_list_offset, file_list_size, flags, priority,
             md5) = HEADER15_STRUCT.unpack_from(head)
            return cls(version, file_list_offset, file_list_size, flags,
                       priority, md5, num_parts=1)
        if version in (16, 18):
            if len(head) < HEADER16_STRUCT.size:
                raise ValueError(f"file too small to be a v{version} .pak archive")
            (_, _, file_list_offset, file_list_size, flags, priority, md5,
             num_parts) = HEADER16_STRUCT.unpack_from(head)
            return cls(version, file_list_offset, file_list_size, flags,
                       priority, md5, num_parts=max(num_parts, 1))
        raise ValueError(f"unsupported LSPK version {version}")

    @classmethod
    def _parse_tail_header(cls, fh: BinaryIO, file_size: int) -> "PakHeader":
        if file_size < TRAILER13_STRUCT.size:
            raise ValueError("not an LSPK archive")
        fh.seek(file_size - TRAILER13_STRUCT.size)
        header_size, signature = TRAILER13_STRUCT.unpack(
            fh.read(TRAILER13_STRUCT.size)
        )
        if signature != SIGNATURE:
            raise ValueError("not an LSPK archive (no signature at start or end)")
        if not HEADER13_BLOCK_SIZE <= header_size <= file_size:
            raise ValueError(f"implausible v13 header size {header_size}")
        fh.seek(file_size - header_size)
        (version, file_list_offset, file_list_size, num_parts, flags,
         priority, md5) = HEADER13_STRUCT.unpack(fh.read(HEADER13_STRUCT.size))
        if version != 13:
            raise ValueError(f"unsupported LSPK version {version} in tail header")
        return cls(
            version=13,
            file_list_offset=file_list_offset,
            file_list_size=file_list_size,
            flags=flags,
            priority=priority,
            md5=md5,
            num_parts=max(num_parts, 1),
        )

    @property
    def entry_size(self) -> int:
        """Size of one file-list entry for this archive version."""
        if self.version <= 13:
            return ENTRY10_SIZE
        return ENTRY15_SIZE if self.version < 18 else ENTRY18_SIZE

    @property
    def solid(self) -> bool:
        return bool(self.flags & FLAG_SOLID)

    def pack10(self) -> bytes:
        """The v10 start-of-file header."""
        return HEADER10_STRUCT.pack(
            SIGNATURE,
            self.version,
            self.data_offset,
            self.file_list_size,
            self.num_parts,
            self.flags,
            self.priority,
            self.num_files,
        )

    def pack13(self) -> bytes:
        """The v13 end-of-file header block (header + size/signature trailer)."""
        return HEADER13_STRUCT.pack(
            self.version,
            self.file_list_offset,
            self.file_list_size,
            self.num_parts,
            self.flags,
            self.priority,
            self.md5,
        ) + TRAILER13_STRUCT.pack(HEADER13_BLOCK_SIZE, SIGNATURE)


@dataclass(frozen=True)
class PakEntry:
    """A single file inside a .pak archive."""

    name: str
    offset: int
    archive_part: int
    flags: int
    size_on_disk: int
    uncompressed_size: int
    crc: int = 0

    @property
    def compression(self) -> CompressionMethod:
        return CompressionMethod(self.flags & 0x0F)

    @property
    def size(self) -> int:
        """Decompressed size of the file.

        Ordinary uncompressed entries store 0 in ``uncompressed_size``
        and their real size in ``size_on_disk``.  Solid archives mark
        every entry method-NONE yet fill in both fields (the frame does
        the compressing), so a non-zero ``uncompressed_size`` is
        authoritative regardless of the method nibble.
        """
        return self.uncompressed_size or self.size_on_disk

    @classmethod
    def parse_all(cls, table: bytes, version: int) -> list["PakEntry"]:
        """Parse a whole file-list table at once.

        ``struct.iter_unpack`` walks the fixed-size records in C — the
        file list holds tens of thousands of entries across a retail
        install.  ``table`` must be exactly ``num_files * entry_size``
        bytes (the caller sizes it so).
        """
        if version <= 13:  # 280-byte FileEntry10
            return [
                cls(rn.rstrip(b"\x00").decode("utf-8", "replace"), off, part,
                    flags, sod, unc, crc)
                for rn, off, sod, unc, part, flags, crc
                in ENTRY10_STRUCT.iter_unpack(table)
            ]
        if version < 18:  # 296-byte FileEntry15
            return [
                cls(rn.rstrip(b"\x00").decode("utf-8", "replace"), off, part,
                    flags, sod, unc, crc)
                for rn, off, sod, unc, part, flags, crc, _unk
                in ENTRY15_STRUCT.iter_unpack(table)
            ]
        return [  # 272-byte FileEntry18
            cls(rn.rstrip(b"\x00").decode("utf-8", "replace"),
                lo | (hi << 32), part, flags, sod, unc)
            for rn, lo, hi, part, flags, sod, unc
            in ENTRY18_STRUCT.iter_unpack(table)
        ]

    def pack10(self) -> bytes:
        """Serialize in the 280-byte v10/v13 entry layout."""
        raw_name = self.name.encode("utf-8")
        if len(raw_name) > NAME_SIZE:
            raise ValueError(f"entry name too long: {self.name!r}")
        return ENTRY10_STRUCT.pack(
            raw_name.ljust(NAME_SIZE, b"\x00"),
            self.offset,
            self.size_on_disk,
            self.uncompressed_size,
            self.archive_part,
            self.flags,
            self.crc,
        )
