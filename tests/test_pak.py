"""Round-trip and robustness tests for the LSPK reader/writer."""

from __future__ import annotations

import pytest

from dos2forge.pak import CompressionMethod, PakEntry, PakHeader, PakReader, PakWriter
from dos2forge.pak import lz4compat
from dos2forge.pak.extractor import Extractor
from dos2forge.pak.format import FLAG_SOLID
from dos2forge.pak.reader import PakError, file_is_lspk

from conftest import SAMPLE_FILES


@pytest.mark.parametrize("version", [10, 13])
@pytest.mark.parametrize(
    "compression",
    [CompressionMethod.NONE, CompressionMethod.ZLIB, CompressionMethod.LZ4],
)
def test_round_trip(make_pak, version, compression):
    path = make_pak(version=version, compression=compression)
    with PakReader(path) as pak:
        assert pak.header.version == version
        assert sorted(pak.names()) == sorted(SAMPLE_FILES)
        for name, data in SAMPLE_FILES.items():
            assert pak.read(name) == data


@pytest.mark.parametrize("version", [10, 13])
def test_header_detection(make_pak, version):
    path = make_pak(version=version)
    assert file_is_lspk(path)
    with path.open("rb") as fh:
        header = PakHeader.read(fh)
    assert header.version == version
    assert header.num_parts == 1


def test_v13_header_is_at_the_tail(make_pak):
    # A v13 archive must not start with the signature: the header (and
    # signature) live at the end of the file, after the file list.
    path = make_pak(version=13)
    raw = path.read_bytes()
    assert not raw.startswith(b"LSPK")
    assert raw.endswith(b"LSPK")


def test_entry_lookup_and_container_protocol(make_pak):
    with PakReader(make_pak()) as pak:
        assert len(pak) == len(SAMPLE_FILES)
        name = next(iter(SAMPLE_FILES))
        assert name in pak
        assert "Nope/missing.txt" not in pak
        entry = pak.entry(name)
        assert entry.size == len(SAMPLE_FILES[name])
        with pytest.raises(PakError, match="not found"):
            pak.entry("Nope/missing.txt")


def test_writer_rejects_unsupported_versions():
    with pytest.raises(ValueError, match="v10 or v13"):
        PakWriter(version=18)


def test_non_pak_file_is_rejected(tmp_path):
    bogus = tmp_path / "bogus.pak"
    bogus.write_bytes(b"this is not an archive, definitely not")
    assert not file_is_lspk(bogus)
    with pytest.raises(PakError, match="not an LSPK archive"):
        PakReader(bogus)


def test_truncated_v13_archive_is_rejected(make_pak):
    path = make_pak(version=13)
    raw = path.read_bytes()
    # Drop bytes out of the middle: the file list can no longer be read.
    path.write_bytes(raw[:10] + raw[-40:])
    with pytest.raises(PakError):
        PakReader(path)


def test_corrupt_file_list_is_rejected(make_pak):
    path = make_pak(version=13, compression=CompressionMethod.NONE)
    with path.open("rb") as fh:
        header = PakHeader.read(fh)
    raw = bytearray(path.read_bytes())
    # An absurd entry count must be rejected before the decompressor
    # pre-allocates a table of that size.
    raw[header.file_list_offset : header.file_list_offset + 4] = (
        (10**8).to_bytes(4, "little")
    )
    path.write_bytes(bytes(raw))
    with pytest.raises(PakError, match="implausible file count"):
        PakReader(path)


def test_extractor_incremental(make_pak, tmp_path):
    out = tmp_path / "out"
    path = make_pak()
    result = Extractor(out).extract(path)
    assert sorted(result.extracted) == sorted(SAMPLE_FILES)
    for name, data in SAMPLE_FILES.items():
        assert (out / name).read_bytes() == data
    # A second run over unchanged data extracts nothing.
    again = Extractor(out).extract(path)
    assert not again.extracted
    assert sorted(again.skipped) == sorted(SAMPLE_FILES)


def test_extractor_patterns(make_pak, tmp_path):
    out = tmp_path / "out"
    result = Extractor(out).extract(make_pak(), patterns=["*/stats/*"])
    assert result.extracted == ["Public/Shared/Stats/Generated/Data/Weapon.txt"]


def test_extractor_rejects_escaping_paths(make_pak, tmp_path):
    path = make_pak(files={"../evil.txt": b"boom"})
    with pytest.raises(PakError, match="unsafe archive entry path"):
        Extractor(tmp_path / "out").extract(path)
    assert not (tmp_path / "evil.txt").exists()


def _write_solid_pak(path, files):
    """Assemble a v13 solid archive the way DE retail data is laid out:
    one LZ4 frame over all file data, no EndMark (the blocks run right up
    to the file list), and method-NONE entries with both sizes filled in.
    """
    lz4frame = pytest.importorskip("lz4.frame")
    compressor = lz4frame.LZ4FrameCompressor(auto_flush=True)
    frame = bytearray(compressor.begin())
    assert len(frame) == 7  # magic + FLG + BD + header checksum
    entries = []
    offset = 7
    for name, data in files.items():
        span = compressor.compress(data)
        entries.append(
            PakEntry(
                name=name, offset=offset, archive_part=0,
                flags=int(CompressionMethod.NONE),
                size_on_disk=len(span), uncompressed_size=len(data),
            )
        )
        frame += span
        offset += len(span)
    # No compressor.flush(): retail frames carry no EndMark.

    table = b"".join(e.pack10() for e in entries)
    file_list = len(entries).to_bytes(4, "little") + lz4compat.compress(table)
    header = PakHeader(
        version=13,
        file_list_offset=len(frame),
        file_list_size=len(file_list),
        flags=FLAG_SOLID,
        priority=0,
        md5=b"\x00" * 16,
        num_parts=1,
    )
    path.write_bytes(bytes(frame) + file_list + header.pack13())
    return path


def test_solid_archive(tmp_path):
    files = {
        "Public/Shared/a.txt": b"alpha " * 100,
        "Public/Shared/b.txt": b"bravo " * 50,
    }
    path = _write_solid_pak(tmp_path / "Solid.pak", files)
    with PakReader(path) as pak:
        assert pak.header.solid
        for name, data in files.items():
            assert pak.read(name) == data
            # Solid entries are method-NONE but their uncompressed_size
            # is authoritative; size must not fall back to the span size.
            assert pak.entry(name).size == len(data)


def test_pure_decoder_handles_linked_blocks_without_endmark():
    # Retail solid frames set FLG 0x40: linked blocks (matches may reach
    # into the previous block's output) and no EndMark.  Exercised
    # directly so it runs regardless of native lz4 being installed.
    literals = b"abcdefgh"
    block1 = bytes([len(literals) << 4]) + literals
    block2 = b"\x04\x08\x00"  # 0 literals, match: offset 8, length 8
    frame = (
        (0x184D2204).to_bytes(4, "little")
        + b"\x40\x40\x00"  # FLG (v1, linked), BD, header checksum (unverified)
        + len(block1).to_bytes(4, "little") + block1
        + len(block2).to_bytes(4, "little") + block2
    )
    out = lz4compat._py_decompress_frame(frame, allow_truncated=True)
    assert out == literals * 2
    with pytest.raises(lz4compat.LZ4Error, match="truncated"):
        lz4compat._py_decompress_frame(frame, allow_truncated=False)


def test_solid_archive_with_gap_is_rejected(tmp_path):
    # A solid archive whose first entry does not start at offset 7 (right
    # after the LZ4 frame header) violates the layout LSLib documents.
    data = b"alpha " * 100
    entry = PakEntry(
        name="Public/Shared/a.txt", offset=8, archive_part=0,
        flags=int(CompressionMethod.LZ4),
        size_on_disk=100, uncompressed_size=len(data),
    )
    table = entry.pack10()
    file_list = (1).to_bytes(4, "little") + lz4compat.compress(table)
    frame = b"\x00" * 120  # never reached: the offset check fires first
    header = PakHeader(
        version=13,
        file_list_offset=len(frame),
        file_list_size=len(file_list),
        flags=FLAG_SOLID,
        priority=0,
        md5=b"\x00" * 16,
        num_parts=1,
    )
    path = tmp_path / "Solid.pak"
    path.write_bytes(frame + file_list + header.pack13())
    with pytest.raises(PakError, match="not contiguous"):
        PakReader(path)


def test_v10_offsets_are_relative_to_data_offset(make_pak):
    # Regression guard for the v10 quirk: part-0 entry offsets are
    # relative to the header's data_offset, unlike every later version.
    path = make_pak(version=10, compression=CompressionMethod.NONE)
    with PakReader(path) as pak:
        name = "Public/Shared/Stats/Generated/Data/Weapon.txt"
        entry = pak.entry(name)
        raw = path.read_bytes()
        assert raw[entry.offset : entry.offset + entry.size_on_disk] == SAMPLE_FILES[name]
