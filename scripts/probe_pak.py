"""Dump the low-level structure of a v13 .pak for format debugging.

Usage:  python scripts/probe_pak.py "E:/.../DefEd/Data/EngineShaders.pak"

Prints header fields, entry-table statistics, and — for solid archives —
walks the LZ4 frame block by block to report where it actually ends and
how much it decompresses to.  Pure stdlib except lz4 (already a dev dep).
"""

from __future__ import annotations

import struct
import sys
from collections import Counter
from pathlib import Path

import lz4.block
import lz4.frame


def main(path: str) -> None:
    raw = Path(path).read_bytes()
    size = len(raw)
    header_size, signature = struct.unpack("<i4s", raw[-8:])
    print(f"file size          {size}")
    print(f"tail header_size   {header_size}  signature {signature!r}")
    (version, file_list_offset, file_list_size, num_parts, flags,
     priority, _md5) = struct.unpack(
        "<IIIHBB16s", raw[size - header_size : size - header_size + 32]
    )
    print(f"version            {version}")
    print(f"file_list_offset   {file_list_offset}")
    print(f"file_list_size     {file_list_size}")
    print(f"num_parts          {num_parts}   flags 0x{flags:02x}   priority {priority}")

    num_files = int.from_bytes(
        raw[file_list_offset : file_list_offset + 4], "little"
    )
    table = lz4.block.decompress(
        raw[file_list_offset + 4 : file_list_offset + file_list_size],
        uncompressed_size=num_files * 280,
    )
    entries = []
    for i in range(num_files):
        name, off, sod, unc, part, eflags, crc = struct.unpack_from(
            "<256sIIIIII", table, i * 280
        )
        entries.append((off, sod, unc, part, eflags, name.rstrip(b"\x00")))
    entries.sort()

    print(f"entries            {num_files}")
    print(f"parts seen         {sorted({e[3] for e in entries})}")
    print(f"methods (flags&15) {Counter(e[4] & 15 for e in entries)}")
    print(f"flag bytes seen    {Counter(e[4] for e in entries).most_common(5)}")
    sum_sod = sum(e[1] for e in entries)
    sum_unc = sum(e[2] for e in entries)
    print(f"sum size_on_disk   {sum_sod}")
    print(f"sum uncompressed   {sum_unc}")
    print(f"first entries      {[(e[0], e[1], e[2], e[3]) for e in entries[:3]]}")
    print(f"last entry         {entries[-1][:5]}")
    tile_end = 7
    contiguous = True
    for off, sod, *_ in entries:
        if off != tile_end:
            contiguous = False
            print(f"tiling breaks at offset {off}, expected {tile_end}")
            break
        tile_end += sod
    print(f"tiled contiguously {contiguous}, tile end {tile_end}")
    print(f"gap tile_end -> file_list_offset: {file_list_offset - tile_end}")

    if not flags & 0x04:
        print("not a solid archive; done")
        return

    # Walk the LZ4 frame header + blocks manually.
    flg, bd = raw[4], raw[5]
    print(f"frame magic        {raw[:4].hex()} (expect 04224d18)")
    print(f"FLG 0x{flg:02x}: version={flg >> 6} indep={bool(flg & 0x20)} "
          f"blocksum={bool(flg & 0x10)} csize={bool(flg & 0x08)} "
          f"csum={bool(flg & 0x04)} dictid={bool(flg & 0x01)}")
    print(f"BD  0x{bd:02x}")
    pos = 6
    if flg & 0x08:
        pos += 8
    if flg & 0x01:
        pos += 4
    pos += 1  # header checksum
    print(f"first block at     {pos}")
    blocks = 0
    while pos + 4 <= len(raw):
        bsize = int.from_bytes(raw[pos : pos + 4], "little")
        pos += 4
        if bsize == 0:
            print(f"EndMark at         {pos - 4} (blocks: {blocks})")
            break
        bsize &= 0x7FFFFFFF
        pos += bsize
        if flg & 0x10:
            pos += 4
        blocks += 1
    else:
        print(f"ran off the end at {pos} without an EndMark (blocks: {blocks})")
    if flg & 0x04:
        pos += 4
    print(f"frame ends at      {pos}  (file_list_offset {file_list_offset})")

    # Stream-decompress the region up to the file list, unbounded, to
    # measure the real output size.
    decoder = lz4.frame.LZ4FrameDecompressor()
    out_len = 0
    data = raw[:file_list_offset]
    try:
        while True:
            chunk = decoder.decompress(data, 1 << 20)
            out_len += len(chunk)
            data = b""
            if decoder.eof or not chunk:
                break
        print(f"decoded output     {out_len}  eof={decoder.eof} "
              f"unused={len(decoder.unused_data or b'')}")
    except RuntimeError as exc:
        print(f"decode failed after {out_len} bytes: {exc}")


if __name__ == "__main__":
    main(sys.argv[1])
