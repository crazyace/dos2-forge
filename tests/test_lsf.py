"""LSF round-trip and robustness tests, focused on the DOS2 layouts."""

from __future__ import annotations

import pytest

from dos2forge.pak.format import CompressionMethod
from dos2forge.pak.lz4compat import HAVE_NATIVE_LZ4
from dos2forge.parsers.lsf import (
    LsfError,
    is_lsf,
    pack_version32,
    parse_lsf,
    unpack_version32,
    write_lsf,
)

from conftest import sample_document


def _assert_matches_sample(document):
    root = document.regions["Templates"]
    child = root.children[0]
    assert child.id == "GameObjects"
    assert child.get("MapKey") == "123e4567-e89b-42d3-a456-426614174000"
    assert child.get("Name") == "Longsword"
    assert child.get("Level") == "3"
    assert child.get("Weight") == "1.5"
    assert child.get("IsUnique") == "True"
    assert child.get("Rotation") == "0.0 1.0 0.0"
    assert child.get("Parent") == "123e4567-e89b-42d3-a456-426614174000"
    assert child.get("Big") == "9007199254740993"
    translated = child.attributes["DisplayName"]
    assert translated.handle == "h11111111g2222g3333g4444g555555555555"
    # DOS2-era files carry the display text inline; it must survive.
    assert translated.value == "Longsword"


@pytest.mark.parametrize("version", [1, 2, 3])
def test_dos2_round_trip(version):
    blob = write_lsf(sample_document(), version=version)
    assert is_lsf(blob)
    _assert_matches_sample(parse_lsf(blob))


@pytest.mark.parametrize("version", [5, 6, 7])
def test_bg3_layouts_still_round_trip(version):
    # BG3-era writes drop the inline translated value (their format has
    # no place for it), so check everything else survives.
    blob = write_lsf(sample_document(), version=version)
    document = parse_lsf(blob)
    child = document.regions["Templates"].children[0]
    assert child.get("Name") == "Longsword"
    translated = child.attributes["DisplayName"]
    assert translated.handle == "h11111111g2222g3333g4444g555555555555"
    assert translated.value is None


def test_zlib_compressed_sections_round_trip():
    blob = write_lsf(sample_document(), version=3, compression=CompressionMethod.ZLIB)
    _assert_matches_sample(parse_lsf(blob))


@pytest.mark.skipif(not HAVE_NATIVE_LZ4, reason="LZ4 frame compression needs native lz4")
def test_lz4_compressed_sections_round_trip():
    # v3 sections other than the string table use the LZ4 *frame* format.
    blob = write_lsf(sample_document(), version=3, compression=CompressionMethod.LZ4)
    _assert_matches_sample(parse_lsf(blob))


def test_v1_sections_use_lz4_blocks_not_frames():
    # Version 1 predates chunked compression: LZ4 sections are raw blocks,
    # which the dependency-free fallback can write too.
    blob = write_lsf(sample_document(), version=1, compression=CompressionMethod.LZ4)
    _assert_matches_sample(parse_lsf(blob))


def test_engine_version_is_dos2_for_v3():
    blob = write_lsf(sample_document(), version=3)
    engine = int.from_bytes(blob[8:12], "little")
    assert unpack_version32(engine) == (3, 6, 4, 0)


def test_bad_magic_is_rejected():
    with pytest.raises(LsfError, match="not an LSF resource"):
        parse_lsf(b"XXXX" + b"\x00" * 64)


def test_unsupported_version_is_rejected():
    blob = bytearray(write_lsf(sample_document(), version=3))
    blob[4:8] = (99).to_bytes(4, "little")
    with pytest.raises(LsfError, match="unsupported LSF version"):
        parse_lsf(bytes(blob))


def test_truncated_resource_is_rejected():
    blob = write_lsf(sample_document(), version=3)
    with pytest.raises(LsfError):
        parse_lsf(blob[: len(blob) // 2])


def test_unsupported_write_version_is_rejected():
    with pytest.raises(LsfError, match="unsupported write version"):
        write_lsf(sample_document(), version=4)


def test_pack_version32_round_trip():
    packed = pack_version32(3, 6, 4, 271)
    assert unpack_version32(packed) == (3, 6, 4, 271)
    with pytest.raises(ValueError, match="does not fit"):
        pack_version32(16, 0, 0, 0)
