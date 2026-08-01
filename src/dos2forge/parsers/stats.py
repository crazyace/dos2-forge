"""Parser for Larian stats ``.txt`` files.

These are the ``Public/<Mod>/Stats/Generated/Data/*.txt`` files, e.g.::

    new entry "WPN_Sword_1H"
    type "Weapon"
    using "_BaseWeapon"
    data "Damage Range" "4"
    data "Damage Type" "Slashing"

Entries may inherit from another entry via ``using``; inheritance is
resolved lazily across every file loaded into a :class:`StatsCollection`.

Some files in the same directory (``Data.txt``, ``XPData.txt``, …) carry top-level global constants instead of
entries::

    key "AttributeBaseValue","10"

These are collected into :attr:`StatsDocument.globals` /
:attr:`StatsCollection.globals`.  Block types other than ``new entry``
(``new itemcolor`` and friends) are tolerated and skipped — we only
model entries — while malformed structural lines (``data``/``type``/
``using`` outside any block) still raise :class:`StatsParseError`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

_LINE_RE = re.compile(r'^(?P<keyword>[a-z ]+?)\s+"(?P<args>.*)"\s*$')
# Argument separators: `"a" "b"` (data lines) and `"a","b"` (key lines).
_ARG_SPLIT_RE = re.compile(r'"\s*,\s*"|"\s+"')
# Line shapes we model.  A line starting with one of these that then
# fails to parse is *malformed*, not merely unmodeled — silently
# dropping it would silently drop data.
_STRUCTURAL_RE = re.compile(r"^(?:new entry|data|type|using|key)\b")


def _strip_trailing_comment(line: str) -> str:
    """Drop a trailing ``// comment`` that sits outside any quotes."""
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "/" and not in_quote and line.startswith("//", i):
            return line[:i].rstrip()
    return line


@dataclass
class StatsEntry:
    name: str
    type: str = ""
    using: str | None = None
    data: dict[str, str] = field(default_factory=dict)
    source: str | None = None  # file the entry came from

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.data.get(key, default)


class StatsParseError(ValueError):
    def __init__(self, message: str, source: str | None = None, line: int | None = None):
        location = f"{source or '<stats>'}:{line}" if line else source or "<stats>"
        super().__init__(f"{location}: {message}")


class StatsWriteError(ValueError):
    """A value cannot be represented in the stats ``.txt`` format."""


#: The stats grammar has no escape syntax: a double quote inside a quoted
#: argument changes how the line splits, and a newline starts a new
#: directive — both silently reparse as *different* data (or worse, as
#: injected lines).  Writing them must fail loudly instead.
_UNWRITABLE_RE = re.compile(r'["\r\n]')


def _check_writable(value: str, what: str) -> str:
    if _UNWRITABLE_RE.search(value):
        raise StatsWriteError(
            f"{what} {value!r} contains a double quote or newline; "
            "the stats format has no escape syntax for them"
        )
    return value


@dataclass
class StatsDocument:
    entries: list[StatsEntry] = field(default_factory=list)
    globals: dict[str, str] = field(default_factory=dict)


def parse_stats_document(text: str, source: str | None = None) -> StatsDocument:
    """Parse one stats .txt document into entries plus global constants.

    A quoted value whose closing ``"`` lands on a later physical line
    (retail occasionally wraps a long value, e.g. a two-line
    ``TooltipStatusApply``) is joined back into one logical line.  A
    physical line is always parsed first: retail data has been seen to contain a
    valid directive with a stray extra terminal quote, and treating quote
    parity alone as proof of a continuation swallowed every following entry.
    """
    document = StatsDocument()
    current: StatsEntry | None = None
    physical = text.splitlines()
    index = 0
    total = len(physical)
    while index < total:
        lineno = index + 1
        line = physical[index].strip()
        index += 1
        if not line or line.startswith("//"):
            continue
        if "//" in line:
            line = _strip_trailing_comment(line)
        # Parse the physical line before considering continuation.  Retail
        # data has been seen with a value ending in a stray extra quote
        # (`...)"`); it still matches the directive grammar and must not
        # consume the next `new entry`.  Only a structural line that does
        # not yet parse and has an open quote can be a wrapped value.
        match = _LINE_RE.match(line)
        if (
            match is None
            and line.count('"') % 2 == 1
            and _STRUCTURAL_RE.match(line)
        ):
            while index < total and match is None:
                continuation = physical[index].strip()
                # A new directive cannot close the previous value.  Stop so
                # the original malformed line is reported rather than
                # swallowing otherwise-valid entries.
                if _STRUCTURAL_RE.match(continuation):
                    break
                line += continuation
                index += 1
                if "//" in line:
                    line = _strip_trailing_comment(line)
                match = _LINE_RE.match(line)
        if not match:
            # Unmodeled directives are fine; a *structural* line that
            # doesn't parse (stray trailing token, missing close quote)
            # used to be silently dropped — losing that data with no
            # trace anywhere.
            if _STRUCTURAL_RE.match(line):
                raise StatsParseError(
                    f"malformed {line.split(None, 1)[0]!r} line: {line!r}",
                    source,
                    lineno,
                )
            continue  # tolerate lines we don't model
        keyword = match.group("keyword")
        args = _ARG_SPLIT_RE.split(match.group("args"))
        if keyword == "new entry":
            current = StatsEntry(name=args[0], source=source)
            document.entries.append(current)
        elif keyword == "key":
            # Global constant (Data.txt / XPData.txt style); position-independent.
            if len(args) >= 2:
                document.globals[args[0]] = args[1]
        elif keyword.startswith("new "):
            # A block type we don't model (itemcolor, namegroup, ...):
            # parse it into a discarded sink so its fields attach nowhere.
            current = StatsEntry(name=args[0], source=source)
        elif keyword in ("type", "using", "data"):
            if current is None:
                raise StatsParseError(f"{keyword!r} outside any 'new ...' block", source, lineno)
            if keyword == "type":
                current.type = args[0]
            elif keyword == "using":
                current.using = args[0]
            else:
                if len(args) < 2:
                    raise StatsParseError("'data' needs a key and a value", source, lineno)
                current.data[args[0]] = args[1]
        # any other keyword: an unmodeled directive, ignored
    return document


def parse_stats(text: str, source: str | None = None) -> list[StatsEntry]:
    """Parse one stats .txt document into a list of entries."""
    return parse_stats_document(text, source).entries


def _write_entry(entry: StatsEntry) -> str:
    lines = [f'new entry "{_check_writable(entry.name, "entry name")}"']
    if entry.type:
        lines.append(f'type "{_check_writable(entry.type, "entry type")}"')
    if entry.using is not None:
        lines.append(f'using "{_check_writable(entry.using, "using reference")}"')
    for key, value in entry.data.items():
        lines.append(
            f'data "{_check_writable(key, "data key")}"'
            f' "{_check_writable(value, f"data value for {key!r}")}"'
        )
    return "\n".join(lines)


def write_stats_document(document: StatsDocument) -> str:
    """Serialize a :class:`StatsDocument` back to Larian stats ``.txt`` form.

    The inverse of :func:`parse_stats_document` at the data-model level:
    re-parsing the result yields an equivalent document (entries in the
    same order with the same ``type``/``using``/``data``, and the same
    globals).  What is intentionally *not* round-tripped carries no data:
    comments, blank-line layout, the provenance ``source`` field, and
    unmodeled ``new <kind>`` blocks (which parsing already discards).

    A global block, if any, is emitted first as consecutive ``key`` lines;
    entries follow, each separated by a blank line, matching retail files.

    Raises :class:`StatsWriteError` for strings the format cannot carry
    (double quotes and newlines have no escape syntax — emitting them
    would silently reparse as different data).
    """
    chunks: list[str] = []
    if document.globals:
        chunks.append(
            "\n".join(
                f'key "{_check_writable(name, "global key")}"'
                f',"{_check_writable(value, f"global value for {name!r}")}"'
                for name, value in document.globals.items()
            )
        )
    chunks.extend(_write_entry(entry) for entry in document.entries)
    return "\n\n".join(chunks) + "\n" if chunks else ""


def write_stats(
    entries: Iterable[StatsEntry], globals: dict[str, str] | None = None
) -> str:
    """Serialize loose entries (plus optional globals) to stats ``.txt`` form."""
    return write_stats_document(
        StatsDocument(entries=list(entries), globals=dict(globals or {}))
    )


class StatsCollection:
    """All stats entries across any number of files, with inheritance.

    ``resolved(name)`` returns the entry's effective data with every
    ``using`` ancestor's fields merged in (nearest definition wins).
    """

    def __init__(self, entries: Iterable[StatsEntry] = ()):
        self._entries: dict[str, StatsEntry] = {}
        # Every definition of a name in load order.  Retail patch files
        # redefine an entry `using` its own name to layer changes over
        # the earlier definition; resolution needs those older layers.
        self._layers: dict[str, list[StatsEntry]] = {}
        self.globals: dict[str, str] = {}  # key "Name","Value" constants
        for entry in entries:
            self.add(entry)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def __iter__(self) -> Iterator[StatsEntry]:
        return iter(self._entries.values())

    def __getitem__(self, name: str) -> StatsEntry:
        return self._entries[name]

    def add(self, entry: StatsEntry) -> None:
        # Later definitions override earlier ones, mirroring pak priority.
        self._entries[entry.name] = entry
        self._layers.setdefault(entry.name, []).append(entry)

    def load_text(self, text: str, source: str | None = None) -> None:
        document = parse_stats_document(text, source)
        for entry in document.entries:
            self.add(entry)
        self.globals.update(document.globals)

    def load_file(self, path: str | Path) -> None:
        path = Path(path)
        self.load_text(path.read_text("utf-8-sig"), source=path.name)

    def load_directory(self, directory: str | Path) -> None:
        for path in sorted(Path(directory).rglob("*.txt")):
            self.load_file(path)

    def by_type(self, *types: str) -> list[StatsEntry]:
        wanted = set(types)
        return [e for e in self._entries.values() if e.type in wanted]

    def resolved(self, name: str) -> dict[str, str]:
        """Effective key/value data for ``name`` with inheritance applied.

        A definition ``using`` its own name (retail's patch-layering
        pattern)
        resolves to the *previous* definition of that name rather than
        itself; ``using`` another name resolves to its latest
        definition.  Genuine cross-entry cycles raise
        :class:`StatsParseError`.
        """
        chain: list[StatsEntry] = []
        seen_ids: set[int] = set()
        cursor = self._entries.get(name)
        while cursor is not None:
            if id(cursor) in seen_ids:
                raise StatsParseError(f"inheritance cycle at {cursor.name!r}")
            seen_ids.add(id(cursor))
            chain.append(cursor)
            target = cursor.using
            if target is None:
                break
            if target == cursor.name:
                cursor = self._previous_layer(cursor)
            else:
                cursor = self._entries.get(target)
        data: dict[str, str] = {}
        for entry in reversed(chain):
            data.update(entry.data)
        return data

    def _previous_layer(self, definition: StatsEntry) -> StatsEntry | None:
        """The definition of the same name loaded just before this one."""
        layers = self._layers.get(definition.name, [])
        for index, layer in enumerate(layers):
            if layer is definition:
                return layers[index - 1] if index > 0 else None
        return None
