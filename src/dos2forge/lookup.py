"""Resolve a name / UUID / handle to its game data and cross-references."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .game import Game, RootTemplate

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
#: DOS2 localization handles: h + uuid-ish with g separators.
_HANDLE_RE = re.compile(r"^h[0-9a-f]{1,8}(g[0-9a-f]{1,12}){4}$", re.I)

_MAX_SUGGESTIONS = 25
_MAX_STATS_KEYS = 24


@dataclass
class LookupResult:
    query: str
    sections: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.sections)


def lookup(game: Game, query: str) -> LookupResult:
    result = LookupResult(query=query.strip())
    query = result.query

    if _UUID_RE.match(query):
        template = game.templates.by_map_key.get(query.lower()) or (
            game.templates.by_map_key.get(query)
        )
        if template:
            result.sections.append(_template_section(game, template))
        return result

    if _HANDLE_RE.match(query):
        text = game.localization.resolve(query, default="")
        if text:
            result.sections.append(f"localization {query}\n  text: {text}")
        for template in game.templates.by_map_key.values():
            if template.handle.split(";", 1)[0] == query:
                result.sections.append(_template_section(game, template))
        return result

    if query in game.stats:
        # The shared stats block prints once; the templates that use it
        # follow without repeating it.
        result.sections.append(_stats_section(game, query))
        for template in game.templates.by_stats.get(query, ()):
            result.sections.append(
                _template_section(game, template, include_stats=False)
            )
        return result

    for template in game.templates.by_name.get(query.lower(), ()):
        result.sections.append(_template_section(game, template))
    if result.found:
        return result

    # Fuzzy fallback: substring over template names, display names, and
    # stats entry names.
    needle = query.lower()
    seen: set[str] = set()
    for template in game.templates.by_map_key.values():
        if needle in template.name.lower() or needle in template.display_name.lower():
            label = f"{template.name}  ({template.display_name})  {template.map_key}"
            if label not in seen:
                seen.add(label)
                result.suggestions.append(label)
            if len(result.suggestions) >= _MAX_SUGGESTIONS:
                return result
    for entry in game.stats:
        if needle in entry.name.lower():
            label = f"{entry.name}  (stats: {entry.type or 'entry'})"
            if label not in seen:
                seen.add(label)
                result.suggestions.append(label)
            if len(result.suggestions) >= _MAX_SUGGESTIONS:
                break
    return result


def _template_section(
    game: Game, template: RootTemplate, include_stats: bool = True
) -> str:
    lines = [f"template {template.map_key}"]
    for label, value in (
        ("name", template.name),
        ("display name", template.display_name),
        ("handle", template.handle),
        ("type", template.type),
        ("stats", template.stats),
        ("icon", template.icon),
        ("parent", template.parent_template),
        ("source", template.source),
    ):
        if value:
            lines.append(f"  {label}: {value}")
    if include_stats and template.stats and template.stats in game.stats:
        lines.append("")
        lines.append(_stats_section(game, template.stats, indent="  "))
    return "\n".join(lines)


def _stats_section(game: Game, name: str, indent: str = "") -> str:
    entry = game.stats[name]
    lines = [f"{indent}stats entry {name}"]
    if entry.type:
        lines.append(f"{indent}  type: {entry.type}")
    if entry.using:
        lines.append(f"{indent}  using: {entry.using}")
    resolved = game.stats.resolved(name)
    for key in list(resolved)[:_MAX_STATS_KEYS]:
        lines.append(f"{indent}  {key}: {resolved[key]}")
    if len(resolved) > _MAX_STATS_KEYS:
        lines.append(f"{indent}  … {len(resolved) - _MAX_STATS_KEYS} more fields")
    return "\n".join(lines)


def format_report(result: LookupResult) -> str:
    if result.found:
        return "\n\n".join(result.sections)
    if result.suggestions:
        header = f"no exact match for {result.query!r}; close matches:"
        return "\n".join([header] + [f"  {s}" for s in result.suggestions])
    return f"no match for {result.query!r}"
