"""LZ4 support (block and frame formats) with pure-Python fallbacks.

Pak file lists and most pak entries in DOS2 are LZ4 *block* compressed;
LSF resources use the LZ4 *frame* format for their chunked sections.
When the native :mod:`lz4` package is installed it is used; otherwise
this module falls back to:

* a complete pure-Python LZ4 block decompressor (correct for any input,
  just slower),
* a pure-Python LZ4 frame parser (reusing the block decompressor), and
* a literals-only LZ4 block compressor (produces valid but uncompressed
  LZ4 streams — fine for writing test fixtures and small files).

Frame *compression* has no pure-Python fallback (the frame header
checksum requires xxHash); writers that need it should store data
uncompressed instead when native LZ4 is missing.

This keeps the core library dependency-free.
"""

from __future__ import annotations

import zlib

try:  # pragma: no cover - exercised implicitly when lz4 is installed
    import lz4.block as _lz4block
except ImportError:  # pragma: no cover
    _lz4block = None

HAVE_NATIVE_LZ4 = _lz4block is not None

# Worst-case expansion each codec can achieve.  A declared uncompressed
# size beyond ``compressed_bytes * ratio`` is a corrupt length field or a
# decompression bomb — reject it before a native decompressor
# pre-allocates that many bytes.  DEFLATE tops out near 1032:1, an LZ4
# block near 255:1; zstd is given generous headroom.
MAX_RATIO_ZLIB = 1032
MAX_RATIO_LZ4 = 255
MAX_RATIO_ZSTD = 1024
_RATIO_SLACK = 4096


class LZ4Error(ValueError):
    """Raised when an LZ4 block cannot be decoded."""


class DecompressionBombError(ValueError):
    """A declared uncompressed size is implausible for its input."""


def guard_size(compressed_len: int, declared: int, ratio: int, what: str) -> None:
    """Reject an implausible declared uncompressed size before it drives a
    pre-allocation (native LZ4/zstd allocate exactly ``declared`` bytes)."""
    if declared > compressed_len * ratio + _RATIO_SLACK:
        raise DecompressionBombError(
            f"{what}: declared size {declared} implausible for "
            f"{compressed_len} compressed bytes (>{ratio}x)"
        )


def zlib_decompress(data: bytes, compressed_hint: int | None = None) -> bytes:
    """Inflate a zlib stream, refusing to expand it past DEFLATE's ~1032:1
    worst case (decompression-bomb guard).  Raises :class:`LZ4Error` on a
    corrupt stream and :class:`DecompressionBombError` on overflow."""
    limit = (compressed_hint if compressed_hint is not None else len(data))
    limit = limit * MAX_RATIO_ZLIB + _RATIO_SLACK
    obj = zlib.decompressobj()
    try:
        out = obj.decompress(data, limit)
        if obj.unconsumed_tail:
            raise DecompressionBombError("zlib stream expands beyond ~1032x its input")
        return out + obj.flush()
    except zlib.error as exc:
        raise LZ4Error(f"corrupt zlib stream: {exc}") from exc


def decompress(data: bytes, uncompressed_size: int) -> bytes:
    """Decompress a raw LZ4 block of known uncompressed size.

    Raises :class:`LZ4Error` (a ``ValueError``) for corrupt input or a
    size mismatch, with either backend.
    """
    if _lz4block is not None:
        try:
            result = _lz4block.decompress(data, uncompressed_size=uncompressed_size)
        except _lz4block.LZ4BlockError as exc:
            raise LZ4Error(f"corrupt LZ4 block: {exc}") from exc
        # Native lz4 treats uncompressed_size as a buffer size and happily
        # returns *less*; enforce the exact size the pure path already does.
        if len(result) != uncompressed_size:
            raise LZ4Error(
                f"decompressed size mismatch: got {len(result)}, "
                f"expected {uncompressed_size}"
            )
        return result
    return _py_decompress(data, uncompressed_size)


def compress(data: bytes) -> bytes:
    """Compress ``data`` into a raw LZ4 block."""
    if _lz4block is not None:
        return _lz4block.compress(data, store_size=False)
    return _py_compress_literals(data)


def decompress_frame(
    data: bytes,
    max_output_size: int | None = None,
    allow_truncated: bool = False,
) -> bytes:
    """Decompress LZ4 *frame* format data (possibly concatenated frames).

    When ``max_output_size`` is given, decoding is bounded to that many
    bytes so a malicious frame's content-size hint cannot drive an
    unbounded allocation; exceeding it raises
    :class:`DecompressionBombError`.  Raises :class:`LZ4Error` for corrupt
    input, with either backend.

    ``allow_truncated`` accepts a frame whose input ends cleanly at a
    block boundary without an EndMark.  DOS2 DE's solid archives are
    written that way: frame header plus blocks, terminated only by the
    file list that follows (callers there know the expected output size
    and verify it).
    """
    if _lz4block is not None:
        import lz4.frame

        out = bytearray()
        remaining = data
        # One frame per decompressor; loop for concatenated frames.  The
        # bounded max_length lets a bomb be caught before its full output
        # is materialized, unlike lz4.frame.decompress (which trusts the
        # frame's own content-size header and pre-allocates from it).
        while remaining:
            decoder = lz4.frame.LZ4FrameDecompressor()
            budget = -1 if max_output_size is None else max_output_size - len(out) + 1
            try:
                out += decoder.decompress(remaining, budget)
            except RuntimeError as exc:  # lz4.frame's corrupt-input error
                raise LZ4Error(f"corrupt LZ4 frame: {exc}") from exc
            if max_output_size is not None and len(out) > max_output_size:
                raise DecompressionBombError(
                    f"LZ4 frame output exceeds bound {max_output_size}"
                )
            if not decoder.eof:
                # The decoder consumed every input byte without reaching an
                # EndMark (output overflow was ruled out above).
                if allow_truncated:
                    break
                raise LZ4Error("truncated LZ4 frame")
            remaining = decoder.unused_data or b""
        return bytes(out)
    return _py_decompress_frame(data, max_output_size, allow_truncated)


def compress_frame(data: bytes) -> bytes:
    """Compress data into a single LZ4 frame (requires native lz4)."""
    if _lz4block is None:
        raise LZ4Error(
            "LZ4 frame compression requires the lz4 package; "
            "install dos2forge[lz4] or write uncompressed"
        )
    import lz4.frame

    return lz4.frame.compress(data)


_FRAME_MAGIC = 0x184D2204
_SKIPPABLE_MIN = 0x184D2A50
_SKIPPABLE_MAX = 0x184D2A5F


def _py_decompress_frame(
    src: bytes,
    max_output_size: int | None = None,
    allow_truncated: bool = False,
) -> bytes:
    out = bytearray()
    i = 0
    try:
        while i < len(src):
            magic = int.from_bytes(src[i : i + 4], "little")
            i += 4
            if _SKIPPABLE_MIN <= magic <= _SKIPPABLE_MAX:
                size = int.from_bytes(src[i : i + 4], "little")
                i += 4 + size
                continue
            if magic != _FRAME_MAGIC:
                raise LZ4Error(f"bad LZ4 frame magic {magic:#x}")
            flg = src[i]
            i += 2  # FLG + BD
            if flg >> 6 != 0b01:
                raise LZ4Error("unsupported LZ4 frame version")
            block_checksum = bool(flg & 0x10)
            # Without the block-independence bit, matches may reference
            # the output of earlier blocks in the frame (retail solid
            # archives are written this way).
            independent_blocks = bool(flg & 0x20)
            if flg & 0x08:  # content size present
                i += 8
            if flg & 0x01:  # dictionary id present
                i += 4
            i += 1  # header checksum (not verified)
            while True:
                if i >= len(src):
                    # Input ends at a block boundary without an EndMark —
                    # how DOS2 DE solid archives terminate their frame.
                    if allow_truncated:
                        break
                    raise LZ4Error("truncated LZ4 frame")
                block_size = int.from_bytes(src[i : i + 4], "little")
                i += 4
                if block_size == 0:  # EndMark
                    break
                is_uncompressed = bool(block_size & 0x80000000)
                block_size &= 0x7FFFFFFF
                block = src[i : i + block_size]
                if len(block) != block_size:
                    raise LZ4Error("truncated LZ4 frame block")
                i += block_size
                if is_uncompressed:
                    out += block
                else:
                    min_match_start = len(out) if independent_blocks else 0
                    _py_decompress_block(out, block, min_match_start, max_output_size)
                if max_output_size is not None and len(out) > max_output_size:
                    raise DecompressionBombError(
                        f"LZ4 frame output exceeds bound {max_output_size}"
                    )
                if block_checksum:
                    i += 4
            if flg & 0x04:  # content checksum (not verified)
                i += 4
    except IndexError as exc:
        raise LZ4Error("truncated LZ4 frame") from exc
    return bytes(out)


def _py_decompress(src: bytes, uncompressed_size: int | None) -> bytes:
    dst = bytearray()
    _py_decompress_block(dst, src, 0, uncompressed_size)
    if uncompressed_size is not None and len(dst) != uncompressed_size:
        raise LZ4Error(
            f"decompressed size mismatch: got {len(dst)}, expected {uncompressed_size}"
        )
    return bytes(dst)


def _py_decompress_block(
    dst: bytearray, src: bytes, min_match_start: int, size_limit: int | None
) -> None:
    """Decode one raw LZ4 block, appending to ``dst``.

    ``min_match_start`` is the lowest ``dst`` index a match may copy
    from: the block's own start for independent blocks, 0 when earlier
    frame output is a legal reference window (linked blocks).
    """
    i = 0
    n = len(src)
    try:
        while i < n:
            token = src[i]
            i += 1
            literal_len = token >> 4
            if literal_len == 15:
                while True:
                    extra = src[i]
                    i += 1
                    literal_len += extra
                    if extra != 255:
                        break
            if i + literal_len > n:
                raise LZ4Error("truncated literal run")
            dst += src[i : i + literal_len]
            i += literal_len
            if i >= n:
                break  # last sequence carries no match
            offset = src[i] | (src[i + 1] << 8)
            i += 2
            if offset == 0:
                raise LZ4Error("invalid zero match offset")
            match_len = (token & 0x0F) + 4
            if match_len == 19:
                while True:
                    extra = src[i]
                    i += 1
                    match_len += extra
                    if extra != 255:
                        break
            start = len(dst) - offset
            if start < min_match_start:
                raise LZ4Error("match offset beyond output start")
            if size_limit is not None and len(dst) + match_len > size_limit:
                raise LZ4Error("decompressed output exceeds expected size")
            # Matches may overlap the output being built; copy byte-wise.
            for j in range(match_len):
                dst.append(dst[start + j])
    except IndexError as exc:
        raise LZ4Error("truncated LZ4 block") from exc


def _py_compress_literals(data: bytes) -> bytes:
    """Encode ``data`` as a single literals-only LZ4 sequence."""
    out = bytearray()
    length = len(data)
    out.append(min(length, 15) << 4)
    if length >= 15:
        remaining = length - 15
        while remaining >= 255:
            out.append(255)
            remaining -= 255
        out.append(remaining)
    out += data
    return bytes(out)
