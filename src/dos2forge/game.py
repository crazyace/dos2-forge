"""One object over the whole installed game: paks, templates, stats, text.

:class:`Game` opens every readable ``.pak`` in the data directory in load
order (base archives first, ``PatchN`` archives after, numerically), so a
file shipped by a later patch shadows the base copy — the same layering
the game applies.  Root templates, stats, and localization are parsed
lazily on first use and indexed for lookup.

    from dos2forge import Game

    game = Game()
    t = game.templates.by_map_key["a4448de2-a481-4a4c-b295-b1d80f61c8de"]
    print(t.name, t.display_name, t.stats)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from .locate import find_data_dir, find_game
from .pak.format import PakEntry
from .pak.reader import PakError, PakReader, file_is_lspk
from .parsers.localization import Localization, LocalizationError, parse_localization
from .parsers.lsx import LsxNode
from .parsers.resource import parse_resource
from .parsers.stats import StatsCollection, StatsParseError


class GameNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadIssue:
    """A file that failed to parse (recorded, never fatal)."""

    file: str
    error: str


@dataclass
class RootTemplate:
    map_key: str
    name: str = ""
    display_name: str = ""  # resolved text (localization, else inline)
    handle: str = ""  # DisplayName TranslatedString handle
    type: str = ""  # item / character / ...
    stats: str = ""  # stats entry id
    icon: str = ""
    parent_template: str = ""
    source: str = ""  # archived path the definition came from


@dataclass
class RootTemplateIndex:
    by_map_key: dict[str, RootTemplate] = field(default_factory=dict)
    by_name: dict[str, list[RootTemplate]] = field(default_factory=dict)
    by_stats: dict[str, list[RootTemplate]] = field(default_factory=dict)

    def add(self, template: RootTemplate) -> None:
        # Later definitions (patch paks) replace earlier ones wholesale.
        previous = self.by_map_key.get(template.map_key)
        if previous is not None:
            self._unlink(previous)
        self.by_map_key[template.map_key] = template
        if template.name:
            self.by_name.setdefault(template.name.lower(), []).append(template)
        if template.stats:
            self.by_stats.setdefault(template.stats, []).append(template)

    def _unlink(self, template: RootTemplate) -> None:
        for index, key in (
            (self.by_name, template.name.lower()),
            (self.by_stats, template.stats),
        ):
            bucket = index.get(key)
            if bucket and template in bucket:
                bucket.remove(template)
                if not bucket:
                    del index[key]

    def __len__(self) -> int:
        return len(self.by_map_key)


_PATCH_RE = re.compile(r"^patch(\d+)", re.IGNORECASE)


def _load_order_key(path: Path, priority: int):
    """Base archives first (alphabetically), PatchN after in numeric order,
    mirroring how the game layers its data.  The header priority byte wins
    over everything when archives actually differ in it."""
    match = _PATCH_RE.match(path.stem)
    patch_number = int(match.group(1)) if match else -1
    return (priority, match is not None, patch_number, path.stem.lower())


class Game:
    """Read-only view over an installed copy of DOS2."""

    def __init__(
        self,
        path: str | Path | None = None,
        data_dir: str | Path | None = None,
        language: str = "English",
    ):
        if data_dir is None:
            root = find_game(path)
            if root is None:
                raise GameNotFoundError(
                    "no DOS2 installation found — set DOS2_PATH or pass "
                    "path/data_dir explicitly"
                )
            data_dir = find_data_dir(root)
        self.data_dir = Path(data_dir)
        self.language = language
        self.load_issues: list[LoadIssue] = []

        with_priority: list[tuple[Path, PakReader]] = []
        for pak_path in sorted(self.data_dir.glob("*.pak")):
            if not file_is_lspk(pak_path):
                continue  # secondary parts / foreign files
            try:
                with_priority.append((pak_path, PakReader(pak_path)))
            except PakError as exc:
                self.load_issues.append(LoadIssue(pak_path.name, str(exc)))
        with_priority.sort(
            key=lambda item: _load_order_key(item[0], item[1].header.priority)
        )
        self._paks = [reader for _, reader in with_priority]

        # name -> providing reader, later paks shadowing earlier ones.
        self._files: dict[str, tuple[PakReader, PakEntry]] = {}
        for reader in self._paks:
            for entry in reader:
                self._files[entry.name] = (reader, entry)

    # -- files ---------------------------------------------------------------

    def __contains__(self, name: str) -> bool:
        return name in self._files

    def find_files(self, pattern: str) -> list[str]:
        """Archived paths matching a case-insensitive substring or glob."""
        import fnmatch

        lowered = pattern.lower()
        if any(ch in lowered for ch in "*?["):
            return [n for n in self._files if fnmatch.fnmatch(n.lower(), lowered)]
        return [n for n in self._files if lowered in n.lower()]

    def read(self, name: str) -> bytes:
        try:
            reader, entry = self._files[name]
        except KeyError:
            raise FileNotFoundError(f"{name!r} not in any game pak") from None
        return reader.read(entry)

    def close(self) -> None:
        for reader in self._paks:
            reader.close()

    def __enter__(self) -> "Game":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- datasets ------------------------------------------------------------

    @cached_property
    def stats(self) -> StatsCollection:
        """Every ``Stats/Generated/Data`` entry across all paks, in load order."""
        collection = StatsCollection()
        for name, data in self._layered_matches("/stats/generated/data/", ".txt"):
            try:
                collection.load_text(
                    data.decode("utf-8-sig", errors="replace"), source=name
                )
            except StatsParseError as exc:
                self.load_issues.append(LoadIssue(name, str(exc)))
        return collection

    @cached_property
    def localization(self) -> Localization:
        """Handle → text for the configured language."""
        table = Localization()
        marker = f"localization/{self.language.lower()}"
        for name, data in self._layered_matches("localization/", ".xml"):
            if marker not in name.lower():
                continue
            try:
                for entry in parse_localization(data):
                    table.add(entry)
            except LocalizationError as exc:
                self.load_issues.append(LoadIssue(name, str(exc)))
        return table

    @cached_property
    def templates(self) -> RootTemplateIndex:
        """Every root template (``GameObjects`` node) across all paks."""
        index = RootTemplateIndex()
        localization = self.localization
        for name, data in self._layered_matches("/roottemplates/", (".lsf", ".lsx")):
            try:
                document = parse_resource(data)
            except ValueError as exc:
                self.load_issues.append(LoadIssue(name, str(exc)))
                continue
            for node in document.find_all("GameObjects"):
                template = self._template_from_node(node, name, localization)
                if template is not None:
                    index.add(template)
        return index

    def _template_from_node(
        self, node: LsxNode, source: str, localization: Localization
    ) -> RootTemplate | None:
        map_key = node.get("MapKey")
        if not map_key:
            return None
        display_attr = node.attributes.get("DisplayName")
        handle = (display_attr.handle if display_attr else "") or ""
        inline = (display_attr.value if display_attr else "") or ""
        display = localization.resolve(handle, default=inline) if handle else inline
        return RootTemplate(
            map_key=map_key,
            name=node.get("Name", "") or "",
            display_name=display,
            handle=handle,
            type=node.get("Type", "") or "",
            stats=node.get("Stats", "") or "",
            icon=node.get("Icon", "") or "",
            parent_template=node.get("ParentTemplateId", "") or "",
            source=source,
        )

    def _layered_matches(self, marker: str, suffixes: str | tuple[str, ...]):
        """Yield (name, bytes) for matching files, in pak load order.

        Every pak's own copy is yielded — including files a later patch
        also ships — because merged datasets (stats, templates,
        localization) layer definitions rather than shadowing whole
        files; a patch's ``Weapon.txt`` carries only the changed entries.
        """
        if isinstance(suffixes, str):
            suffixes = (suffixes,)
        for reader in self._paks:
            for entry in reader:
                lowered = entry.name.lower()
                if marker in lowered and lowered.endswith(suffixes):
                    yield entry.name, reader.read(entry)
