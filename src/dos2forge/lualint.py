"""Static checks for Script Extender mod sources.

Two families of problems ruin an SE mod session without ever raising an
error in game: setup files that make SE skip the mod entirely (a typo'd
FeatureFlag, a missing bootstrap, an ``Ext.Require`` of a file that is
not there), and string constants — UUIDs, skill ids, status ids — that
do not name anything, so the call they are passed to silently does
nothing.  ``lint_path`` catches both kinds before the game ever runs:

    from dos2forge import Game
    from dos2forge.lualint import format_issues, lint_path

    issues = lint_path("MyModProject", Game())
    print(format_issues(issues))

Structural checks (config, bootstraps, requires, block balance) always
run; the game-data reference checks need a :class:`~dos2forge.game.Game`
and also accept ids the mod itself defines under ``Public/<Folder>/``.
"""

from __future__ import annotations

import json
import re
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from .parsers.lsx import LsxError, parse_lsx
from .parsers.resource import parse_resource
from .parsers.stats import StatsCollection, StatsParseError
from .semod import FEATURE_FLAGS

if TYPE_CHECKING:  # pragma: no cover
    from .game import Game

_CONFIG_NAME = "OsiToolsConfig.json"
_CONFIG_KEYS = {"RequiredExtensionVersion", "ModTable", "FeatureFlags"}
_BOOTSTRAPS = ("BootstrapServer.lua", "BootstrapClient.lua")

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_STRING_RE = re.compile(r'"([^"\n]*)"|\'([^\'\n]*)\'')
_REQUIRE_RE = re.compile(r'\bExt\.Require\s*\(\s*(["\'])(.+?)\1')

# Osiris/Ext calls whose string argument must name game data.  The Osi.
# prefix is optional because DOS2 exposes the same calls both ways.
_ID_ARG_CALLS: tuple[tuple[str, int, str], ...] = (
    ("CharacterAddSkill", 2, "skill"),
    ("CharacterRemoveSkill", 2, "skill"),
    ("CharacterHasSkill", 2, "skill"),
    ("CharacterUseSkill", 2, "skill"),
    ("ApplyStatus", 2, "status"),
    ("RemoveStatus", 2, "status"),
    ("HasActiveStatus", 2, "status"),
    ("Ext.GetStat", 1, "stat"),
    ("Ext.StatGetAttribute", 1, "stat"),
    ("Ext.StatSetAttribute", 1, "stat"),
)


def _call_re(call: str, arg: int) -> re.Pattern[str]:
    name = r"\b(?:Osi\.)?" + re.escape(call) if "." not in call else r"\b" + re.escape(call)
    if arg == 1:
        return re.compile(name + r"\s*\(\s*([\"'])([^\"'\n]*)\1")
    return re.compile(name + r"\s*\(\s*[^,()\n]*,\s*([\"'])([^\"'\n]*)\1")


_KIND_ATTRS = {"skill": "skills", "status": "statuses", "stat": "stats"}
_COMPILED_CALLS = tuple(
    (_call_re(call, arg), call, kind, _KIND_ATTRS[kind])
    for call, arg, kind in _ID_ARG_CALLS
)


@dataclass(frozen=True)
class LintIssue:
    file: str
    line: int  # 0 = the file (or mod) as a whole
    severity: str  # "error" | "warning"
    code: str
    message: str


@dataclass
class _Knowledge:
    """Known-good ids: game data plus the mod's own definitions.

    ``None`` sets mean "no game index available — skip that check";
    structural lint stays useful without an installed game.
    """

    uuids: set[str] | None = None
    skills: set[str] | None = None
    statuses: set[str] | None = None
    stats: set[str] | None = None


def find_mod_dirs(path: str | Path) -> list[Path]:
    """Mod folders at ``path``: a project root with ``Mods/<Folder>/``
    subdirectories, or ``path`` itself when it is a mod folder."""
    path = Path(path)
    mods_root = path / "Mods"
    if mods_root.is_dir():
        return sorted(d for d in mods_root.iterdir() if d.is_dir() and _looks_like_mod(d))
    if _looks_like_mod(path):
        return [path]
    return []


def _looks_like_mod(directory: Path) -> bool:
    return (
        (directory / "meta.lsx").is_file()
        or (directory / _CONFIG_NAME).is_file()
        or (directory / "Story" / "RawFiles" / "Lua").is_dir()
    )


def lint_path(path: str | Path, game: "Game | None" = None) -> list[LintIssue]:
    """Lint every mod found at ``path`` (project root or mod folder)."""
    path = Path(path)
    mod_dirs = find_mod_dirs(path)
    if not mod_dirs:
        raise ValueError(
            f"no mod found at {path} — expected Mods/<Name_UUID>/ containing "
            f"meta.lsx or {_CONFIG_NAME}"
        )
    issues: list[LintIssue] = []
    for mod_dir in mod_dirs:
        issues.extend(lint_mod(mod_dir, game, base=path))
    return issues


def lint_mod(
    mod_dir: str | Path, game: "Game | None" = None, base: str | Path | None = None
) -> list[LintIssue]:
    """Lint one mod folder (``Mods/<Name_UUID>``)."""
    mod_dir = Path(mod_dir)
    base = Path(base) if base is not None else mod_dir.parent
    issues: list[LintIssue] = []

    def rel(path: Path) -> str:
        try:
            return str(path.relative_to(base))
        except ValueError:
            return str(path)

    def issue(path: Path, line: int, severity: str, code: str, message: str) -> None:
        issues.append(LintIssue(rel(path), line, severity, code, message))

    lua_root = mod_dir / "Story" / "RawFiles" / "Lua"
    lua_files = sorted(lua_root.rglob("*.lua")) if lua_root.is_dir() else []

    module_uuids = _check_meta(mod_dir, issue)
    _check_config(mod_dir, bool(lua_files), issue)

    if lua_files and not any((lua_root / name).is_file() for name in _BOOTSTRAPS):
        issue(
            lua_root, 0, "warning", "bootstrap-missing",
            "no BootstrapServer.lua or BootstrapClient.lua — Script Extender "
            "loads nothing else by itself",
        )

    knowledge = _build_knowledge(game, mod_dir, module_uuids)
    for lua_file in lua_files:
        _lint_lua_file(lua_file, lua_root, knowledge, issue)
    return issues


def format_issues(issues: list[LintIssue]) -> str:
    lines = []
    for i in issues:
        location = f"{i.file}:{i.line}" if i.line else i.file
        lines.append(f"{location}: {i.severity}: {i.message} [{i.code}]")
    errors = sum(1 for i in issues if i.severity == "error")
    warnings = len(issues) - errors
    lines.append(f"{errors} error(s), {warnings} warning(s)")
    return "\n".join(lines)


# -- setup files ---------------------------------------------------------------


def _check_meta(mod_dir: Path, issue) -> set[str]:
    """Validate meta.lsx; returns the module UUID(s) it declares."""
    meta = mod_dir / "meta.lsx"
    uuids: set[str] = set()
    if not meta.is_file():
        issue(
            mod_dir, 0, "warning", "meta-missing",
            "no meta.lsx — the game will not list this folder as a module",
        )
        return uuids
    try:
        document = parse_lsx(meta.read_bytes())
    except LsxError as exc:
        issue(meta, 0, "error", "meta-parse", str(exc))
        return uuids
    info = next(document.find_all("ModuleInfo"), None)
    if info is None:
        issue(meta, 0, "error", "meta-moduleinfo", "no ModuleInfo node")
        return uuids
    declared = info.get("UUID", "") or ""
    try:
        _uuid.UUID(declared)
        uuids.add(declared.lower())
    except ValueError:
        issue(meta, 0, "error", "meta-uuid", f"ModuleInfo UUID {declared!r} is not a UUID")
    folder = info.get("Folder", "") or ""
    if folder and folder != mod_dir.name:
        issue(
            meta, 0, "warning", "meta-folder",
            f"ModuleInfo Folder is {folder!r} but the directory is {mod_dir.name!r}",
        )
    return uuids


def _check_config(mod_dir: Path, has_lua: bool, issue) -> None:
    config_path = mod_dir / _CONFIG_NAME
    if not config_path.is_file():
        if has_lua:
            issue(
                mod_dir, 0, "error", "config-missing",
                f"Lua sources present but no {_CONFIG_NAME} — Script Extender "
                "will not activate for this mod",
            )
        return
    try:
        config = json.loads(config_path.read_text("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        issue(config_path, 0, "error", "config-json", f"not valid JSON: {exc}")
        return
    if not isinstance(config, dict):
        issue(config_path, 0, "error", "config-json", "top level must be a JSON object")
        return
    for key in sorted(set(config) - _CONFIG_KEYS):
        issue(
            config_path, 0, "warning", "config-key",
            f"unknown key {key!r} (ositools reads {sorted(_CONFIG_KEYS)})",
        )
    version = config.get("RequiredExtensionVersion")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int)):
        issue(
            config_path, 0, "error", "config-version",
            f"RequiredExtensionVersion must be an integer, got {version!r}",
        )
    flags = config.get("FeatureFlags", [])
    if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
        issue(
            config_path, 0, "error", "config-flags",
            "FeatureFlags must be a list of strings",
        )
        flags = []
    for flag in sorted(set(flags) - FEATURE_FLAGS):
        issue(
            config_path, 0, "error", "config-flag",
            f"unknown FeatureFlag {flag!r} — ositools understands "
            f"{sorted(FEATURE_FLAGS)}",
        )
    if has_lua and "Lua" not in flags:
        issue(
            config_path, 0, "error", "config-lua",
            'Lua sources present but "Lua" is not in FeatureFlags',
        )
    if "Lua" in flags or has_lua:
        mod_table = config.get("ModTable")
        if mod_table is None:
            issue(
                config_path, 0, "warning", "config-modtable",
                'no ModTable — Lua mods get their table in the "Mods" global '
                "from this name",
            )
        elif not isinstance(mod_table, str) or not re.fullmatch(
            r"[A-Za-z_]\w*", mod_table
        ):
            issue(
                config_path, 0, "warning", "config-modtable",
                f"ModTable {mod_table!r} is not a valid Lua identifier, so "
                "Mods.<name> access will not work",
            )


# -- game/mod data knowledge ---------------------------------------------------


def _build_knowledge(
    game: "Game | None", mod_dir: Path, module_uuids: set[str]
) -> _Knowledge:
    if game is None:
        return _Knowledge()
    uuids = {key.lower() for key in game.templates.by_map_key}
    uuids |= {key.lower() for key in game.level_instances.by_map_key}
    uuids |= module_uuids
    skills: set[str] = set()
    statuses: set[str] = set()
    stats: set[str] = set()
    _index_stats(game.stats, skills, statuses, stats)

    # The mod's own content is just as valid a reference target as game
    # data.  Editor-layout projects keep it in a sibling Public/<Folder>/.
    public = _public_dir(mod_dir)
    if public is not None:
        data_dir = public / "Stats" / "Generated" / "Data"
        if data_dir.is_dir():
            collection = StatsCollection()
            for txt in sorted(data_dir.glob("*.txt")):
                try:
                    collection.load_text(
                        txt.read_text("utf-8-sig", errors="replace"), source=txt.name
                    )
                except StatsParseError:
                    continue  # the game's own tools will complain; not our file
            _index_stats(collection, skills, statuses, stats, permissive=True)
        templates_dir = public / "RootTemplates"
        if templates_dir.is_dir():
            for resource in sorted(templates_dir.iterdir()):
                if resource.suffix.lower() not in (".lsf", ".lsx"):
                    continue
                try:
                    document = parse_resource(resource.read_bytes())
                except ValueError:
                    continue
                for node in document.find_all("GameObjects"):
                    map_key = node.get("MapKey")
                    if map_key:
                        uuids.add(map_key.lower())
    return _Knowledge(uuids=uuids, skills=skills, statuses=statuses, stats=stats)


def _index_stats(
    collection: StatsCollection,
    skills: set[str],
    statuses: set[str],
    stats: set[str],
    permissive: bool = False,
) -> None:
    for entry in collection:
        stats.add(entry.name)
        entry_type = collection.resolved_type(entry.name)
        if entry_type == "SkillData":
            skills.add(entry.name)
        elif entry_type == "StatusData":
            statuses.add(entry.name)
        elif permissive and not entry_type:
            # A mod delta whose base lives in game data we did not merge:
            # its type is unknowable here, so accept it everywhere.
            skills.add(entry.name)
            statuses.add(entry.name)


def _public_dir(mod_dir: Path) -> Path | None:
    if mod_dir.parent.name == "Mods":
        candidate = mod_dir.parent.parent / "Public" / mod_dir.name
        if candidate.is_dir():
            return candidate
    return None


# -- Lua sources ---------------------------------------------------------------


def _lint_lua_file(path: Path, lua_root: Path, knowledge: _Knowledge, issue) -> None:
    source = path.read_text("utf-8-sig", errors="replace")
    clean, code_only, scan_problems = _strip_comments_and_strings(source)
    for line, code, message in scan_problems:
        issue(path, line, "error", code, message)

    def line_of(offset: int) -> int:
        return clean.count("\n", 0, offset) + 1

    for match in _REQUIRE_RE.finditer(clean):
        target = match.group(2)
        line = line_of(match.start())
        if "\\" in target:
            issue(
                path, line, "warning", "require-path",
                f"Ext.Require path {target!r} uses backslashes; use forward slashes",
            )
            target = target.replace("\\", "/")
        if (lua_root / target).is_file():
            continue
        actual = _case_insensitive_match(lua_root, target)
        if actual is not None:
            issue(
                path, line, "warning", "require-case",
                f"Ext.Require target {target!r} exists as {actual!r} — paths "
                "inside a pak are case-sensitive",
            )
        else:
            issue(
                path, line, "error", "require-missing",
                f"Ext.Require target {target!r} not found under Story/RawFiles/Lua",
            )

    _check_blocks(path, code_only, issue)

    if knowledge.uuids is not None:
        for match in _STRING_RE.finditer(clean):
            value = match.group(1) if match.group(1) is not None else match.group(2)
            for uuid_match in _UUID_RE.finditer(value):
                if uuid_match.group(0).lower() not in knowledge.uuids:
                    issue(
                        path, line_of(match.start()), "warning", "uuid-unknown",
                        f"UUID {uuid_match.group(0)} matches no root template, "
                        "level instance, or mod-defined id",
                    )

    for pattern, call, kind, attr in _COMPILED_CALLS:
        known: set[str] | None = getattr(knowledge, attr)
        if known is None:
            continue
        for match in pattern.finditer(clean):
            value = match.group(2)
            if not value or _UUID_RE.search(value):
                continue  # UUID args are checked by the UUID pass
            if value not in known:
                issue(
                    path, line_of(match.start()), "warning", f"{kind}-unknown",
                    f"{call}: {kind} id {value!r} matches no game or mod entry",
                )


def _case_insensitive_match(root: Path, relative: str) -> str | None:
    current = root
    found: list[str] = []
    for part in PurePosixPath(relative).parts:
        if not current.is_dir():
            return None
        match = next(
            (e for e in sorted(current.iterdir()) if e.name.lower() == part.lower()),
            None,
        )
        if match is None:
            return None
        found.append(match.name)
        current = match
    return "/".join(found) if current.is_file() else None


_BLOCK_KEYWORD_RE = re.compile(r"\b(function|if|while|for|do|repeat|until|end)\b")


def _check_blocks(path: Path, code_only: str, issue) -> None:
    """Block balance over comment- and string-stripped source.

    ``while``/``for`` swallow their own ``do`` (one ``end`` closes the
    pair), which is what makes naive keyword counting wrong.
    """
    open_blocks: list[tuple[int, str]] = []
    pending_do = 0
    for match in _BLOCK_KEYWORD_RE.finditer(code_only):
        word = match.group(1)
        line = code_only.count("\n", 0, match.start()) + 1
        if word in ("function", "if", "repeat"):
            open_blocks.append((line, word))
        elif word in ("while", "for"):
            open_blocks.append((line, word))
            pending_do += 1
        elif word == "do":
            if pending_do:
                pending_do -= 1
            else:
                open_blocks.append((line, word))
        else:  # end / until
            if open_blocks:
                open_blocks.pop()
            else:
                issue(
                    path, line, "error", "lua-blocks",
                    f"{word!r} with no open block",
                )
    for line, word in open_blocks:
        issue(path, line, "error", "lua-blocks", f"{word!r} block is never closed")


def _strip_comments_and_strings(
    source: str,
) -> tuple[str, str, list[tuple[int, str, str]]]:
    """(comments blanked, comments+strings blanked, problems).

    Newlines survive blanking so offsets keep mapping to line numbers.
    """
    clean = list(source)
    code_only = list(source)
    problems: list[tuple[int, str, str]] = []

    def blank(start: int, stop: int, also_clean: bool) -> None:
        for j in range(start, stop):
            if source[j] != "\n":
                code_only[j] = " "
                if also_clean:
                    clean[j] = " "

    def line_at(offset: int) -> int:
        return source.count("\n", 0, offset) + 1

    i, n = 0, len(source)
    while i < n:
        char = source[i]
        if source.startswith("--", i):
            long = _long_bracket(source, i + 2)
            if long is not None:
                body_start, level = long
                closer = "]" + "=" * level + "]"
                end = source.find(closer, body_start)
                if end == -1:
                    problems.append((line_at(i), "lua-comment", "unterminated long comment"))
                    blank(i, n, True)
                    i = n
                else:
                    blank(i, end + len(closer), True)
                    i = end + len(closer)
            else:
                end = source.find("\n", i)
                end = n if end == -1 else end
                blank(i, end, True)
                i = end
        elif char in "\"'":
            j = i + 1
            while j < n and source[j] not in (char, "\n"):
                j += 2 if source[j] == "\\" else 1
            if j >= n or source[j] != char:
                problems.append((line_at(i), "lua-string", "unterminated string"))
                blank(i, min(j, n), False)
                i = min(j, n)
            else:
                blank(i, j + 1, False)
                i = j + 1
        elif char == "[":
            long = _long_bracket(source, i)
            if long is not None:
                body_start, level = long
                closer = "]" + "=" * level + "]"
                end = source.find(closer, body_start)
                if end == -1:
                    problems.append((line_at(i), "lua-string", "unterminated long string"))
                    blank(i, n, False)
                    i = n
                else:
                    blank(i, end + len(closer), False)
                    i = end + len(closer)
            else:
                i += 1
        else:
            i += 1
    return "".join(clean), "".join(code_only), problems


def _long_bracket(source: str, i: int) -> tuple[int, int] | None:
    """``[==[`` at ``i`` → (index past the opener, level), else None."""
    if i >= len(source) or source[i] != "[":
        return None
    j = i + 1
    while j < len(source) and source[j] == "=":
        j += 1
    if j < len(source) and source[j] == "[":
        return j + 1, j - i - 1
    return None
