"""Parser for DOS2 XML localization files.

DOS2 ships its localization as XML ``contentList`` documents (inside
``Localization.pak``, e.g. ``Localization/English/english.xml``) rather
than the binary ``.loca`` format BG3 introduced::

    <contentList>
      <content contentuid="h04ff2ab1g53f5g4f6cg9912g6d1c7e59f6dc" version="1">
        Sourcerer
      </content>
    </contentList>

Handles referenced from stats/LSX/LSF look like ``h<uuid-ish with g
separators>`` and may carry a ``;<version>`` suffix;
:class:`Localization` lookups accept both.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from xml.sax.saxutils import escape, quoteattr


class LocalizationError(ValueError):
    pass


@dataclass(frozen=True)
class LocalizationEntry:
    handle: str
    version: int
    text: str


def parse_localization(data: bytes | str) -> list[LocalizationEntry]:
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise LocalizationError(f"malformed localization XML: {exc}") from exc
    if root.tag != "contentList":
        raise LocalizationError(
            f"expected <contentList> root, found <{root.tag}>"
        )
    entries: list[LocalizationEntry] = []
    for content in root.findall("content"):
        handle = content.get("contentuid")
        if not handle:
            continue  # a content element without a handle carries nothing
        raw_version = content.get("version", "1")
        try:
            version = int(raw_version)
        except ValueError:
            version = 1
        entries.append(
            LocalizationEntry(
                handle=handle, version=version, text=content.text or ""
            )
        )
    return entries


def write_localization(entries: list[LocalizationEntry]) -> bytes:
    """Serialize entries back to contentList XML (fixtures, mod locales).

    Hand-assembled rather than via ElementTree so the output preserves
    text exactly (no whitespace normalization) and stays byte-stable.
    """
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<contentList>"]
    for entry in entries:
        lines.append(
            f"  <content contentuid={quoteattr(entry.handle)} "
            f'version="{entry.version}">{escape(entry.text)}</content>'
        )
    lines.append("</contentList>")
    return ("\n".join(lines) + "\n").encode("utf-8")


class Localization:
    """Handle → text lookup across one or more localization files."""

    def __init__(self):
        self._texts: dict[str, str] = {}
        self._versions: dict[str, int] = {}

    def __len__(self) -> int:
        return len(self._texts)

    def __contains__(self, handle: str) -> bool:
        return self._normalize(handle) in self._texts

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self._texts.items())

    def add(self, entry: LocalizationEntry) -> None:
        """Add one entry; an equal-or-higher version replaces older text."""
        key = self._normalize(entry.handle)
        if entry.version >= self._versions.get(key, -1):
            self._texts[key] = entry.text
            self._versions[key] = entry.version

    def load_bytes(self, data: bytes | str) -> None:
        for entry in parse_localization(data):
            self.add(entry)

    def load_file(self, path: str | Path) -> None:
        self.load_bytes(Path(path).read_bytes())

    def merge_missing(self, fallback: "Localization") -> None:
        """Fill in handles absent from this table (language fallback).

        Existing entries always win — only handles this table has no
        text for at all are copied from ``fallback``.
        """
        for key, text in fallback._texts.items():
            if key not in self._texts:
                self._texts[key] = text
                self._versions[key] = fallback._versions.get(key, 0)

    def resolve(self, handle: str | None, default: str = "") -> str:
        """Resolve a handle like ``h0abc...;1`` to its display text."""
        if not handle:
            return default
        return self._texts.get(self._normalize(handle), default)

    @staticmethod
    def _normalize(handle: str) -> str:
        return handle.split(";", 1)[0].strip()
