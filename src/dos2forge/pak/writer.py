"""Writing Larian LSPK (.pak) archives.

Primarily used for building test fixtures and mod paks, this writer
produces single-part v13 (Definitive Edition) or v10 (classic) archives
readable by the game and by :class:`dos2forge.pak.reader.PakReader`.
Layouts follow LSLib's DOS2-era writers.
"""

from __future__ import annotations

import hashlib
import zlib
from pathlib import Path

from . import lz4compat
from .format import (
    DEFAULT_VERSION,
    ENTRY10_SIZE,
    HEADER10_STRUCT,
    CompressionMethod,
    PakEntry,
    PakHeader,
)


class PakWriter:
    """Build a single-part .pak archive.

    Usage::

        writer = PakWriter()
        writer.add("Public/MyMod/Stats/Generated/Data/Weapon.txt", data)
        writer.write("MyMod.pak")
    """

    def __init__(
        self,
        version: int = DEFAULT_VERSION,
        compression: CompressionMethod = CompressionMethod.LZ4,
        priority: int = 0,
    ):
        if version not in (10, 13):
            # Other versions use different entry/header layouts; stamping
            # their version onto these structures would produce an archive
            # no reader can open.
            raise ValueError("PakWriter writes v10 or v13 archives only")
        self.version = version
        self.compression = compression
        self.priority = priority
        self._files: list[tuple[str, bytes]] = []

    def add(self, name: str, data: bytes) -> None:
        self._files.append((name.replace("\\", "/"), data))

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        if self.version == 10:
            return self._write_v10(path)
        return self._write_v13(path)

    def _write_v13(self, path: Path) -> Path:
        # Layout: file data from offset 0, then the file list (i32 count +
        # LZ4 block table), then the tail header block.
        entries: list[PakEntry] = []
        blobs: list[bytes] = []
        offset = 0
        for name, data in self._files:
            blob, method = self._compress(data)
            entries.append(self._entry(name, offset, blob, data, method))
            blobs.append(blob)
            offset += len(blob)

        table = b"".join(e.pack10() for e in entries)
        compressed_table = lz4compat.compress(table)
        file_list = len(entries).to_bytes(4, "little") + compressed_table
        header = PakHeader(
            version=13,
            file_list_offset=offset,
            file_list_size=len(file_list),
            flags=0,
            priority=self.priority,
            md5=hashlib.md5(table).digest(),
            num_parts=1,
        )
        with path.open("wb") as fh:
            for blob in blobs:
                fh.write(blob)
            fh.write(file_list)
            fh.write(header.pack13())
        return path

    def _write_v10(self, path: Path) -> Path:
        # Layout: start header, uncompressed entry table, then file data;
        # part-0 entry offsets are relative to data_offset.
        table_size = len(self._files) * ENTRY10_SIZE
        data_offset = HEADER10_STRUCT.size + table_size
        entries: list[PakEntry] = []
        blobs: list[bytes] = []
        offset = 0  # relative to data_offset
        for name, data in self._files:
            blob, method = self._compress(data)
            entries.append(self._entry(name, offset, blob, data, method))
            blobs.append(blob)
            offset += len(blob)

        header = PakHeader(
            version=10,
            file_list_offset=HEADER10_STRUCT.size,
            file_list_size=table_size,
            flags=0,
            priority=self.priority,
            md5=b"\x00" * 16,
            num_parts=1,
            num_files=len(entries),
            data_offset=data_offset,
        )
        with path.open("wb") as fh:
            fh.write(header.pack10())
            for entry in entries:
                fh.write(entry.pack10())
            for blob in blobs:
                fh.write(blob)
        return path

    def _entry(
        self, name: str, offset: int, blob: bytes, data: bytes,
        method: CompressionMethod,
    ) -> PakEntry:
        return PakEntry(
            name=name,
            offset=offset,
            archive_part=0,
            flags=int(method),
            size_on_disk=len(blob),
            uncompressed_size=0 if method is CompressionMethod.NONE else len(data),
            crc=0,
        )

    def _compress(self, data: bytes) -> tuple[bytes, CompressionMethod]:
        if self.compression is CompressionMethod.NONE or not data:
            return data, CompressionMethod.NONE
        if self.compression is CompressionMethod.ZLIB:
            return zlib.compress(data), CompressionMethod.ZLIB
        if self.compression is CompressionMethod.LZ4:
            return lz4compat.compress(data), CompressionMethod.LZ4
        raise ValueError(f"unsupported write compression: {self.compression}")
