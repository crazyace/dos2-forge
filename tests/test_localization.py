"""DOS2 XML localization tests."""

from __future__ import annotations

import pytest

from dos2forge.parsers.localization import (
    Localization,
    LocalizationEntry,
    LocalizationError,
    parse_localization,
    write_localization,
)

ENGLISH_XML = b"""\
<?xml version="1.0" encoding="utf-8"?>
<contentList>
  <content contentuid="h04ff2ab1g53f5g4f6cg9912g6d1c7e59f6dc" version="1">Sourcerer</content>
  <content contentuid="haabbccddg0000g0000g0000g000000000001" version="2">Divine &amp; Beyond</content>
  <content contentuid="haabbccddg0000g0000g0000g000000000001" version="1">Stale older text</content>
</contentList>
"""


def test_parse_entries():
    entries = parse_localization(ENGLISH_XML)
    assert entries[0] == LocalizationEntry(
        handle="h04ff2ab1g53f5g4f6cg9912g6d1c7e59f6dc", version=1, text="Sourcerer"
    )
    assert entries[1].text == "Divine & Beyond"


def test_highest_version_wins():
    table = Localization()
    table.load_bytes(ENGLISH_XML)
    assert table.resolve("haabbccddg0000g0000g0000g000000000001") == "Divine & Beyond"


def test_resolve_accepts_version_suffix():
    table = Localization()
    table.load_bytes(ENGLISH_XML)
    handle = "h04ff2ab1g53f5g4f6cg9912g6d1c7e59f6dc"
    assert table.resolve(handle + ";1") == "Sourcerer"
    assert handle in table
    assert table.resolve("hmissing", default="?") == "?"
    assert table.resolve(None, default="?") == "?"


def test_merge_missing_is_a_fallback_only():
    primary = Localization()
    primary.load_bytes(ENGLISH_XML)
    fallback = Localization()
    fallback.load_bytes(
        b'<contentList>'
        b'<content contentuid="h04ff2ab1g53f5g4f6cg9912g6d1c7e59f6dc" version="9">Override attempt</content>'
        b'<content contentuid="hnewg0g0g0g1" version="1">Only in fallback</content>'
        b"</contentList>"
    )
    primary.merge_missing(fallback)
    assert primary.resolve("h04ff2ab1g53f5g4f6cg9912g6d1c7e59f6dc") == "Sourcerer"
    assert primary.resolve("hnewg0g0g0g1") == "Only in fallback"


def test_write_round_trip():
    entries = parse_localization(ENGLISH_XML)
    reparsed = parse_localization(write_localization(entries))
    assert reparsed == entries


def test_wrong_root_is_rejected():
    with pytest.raises(LocalizationError, match="contentList"):
        parse_localization(b"<save />")


def test_malformed_xml_is_rejected():
    with pytest.raises(LocalizationError, match="malformed"):
        parse_localization(b"<contentList><content></contentList>")
