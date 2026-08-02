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

#: Game text uses typographic quotes; queries are typed with ASCII ones.
_QUOTE_FOLD = str.maketrans({"‘": "'", "’": "'", "ʼ": "'"})


def _fold(text: str) -> str:
    return text.lower().translate(_QUOTE_FOLD)


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

    indexes = (game.templates, game.level_instances)

    if _UUID_RE.match(query):
        for index in indexes:
            template = index.by_map_key.get(query.lower()) or (
                index.by_map_key.get(query)
            )
            if template:
                result.sections.append(_template_section(game, template))
        return result

    if _HANDLE_RE.match(query):
        text = game.localization.resolve(query, default="")
        if text:
            result.sections.append(f"localization {query}\n  text: {text}")
        for index in indexes:
            for template in index.by_map_key.values():
                if template.handle.split(";", 1)[0] == query:
                    result.sections.append(_template_section(game, template))
        return result

    if query in game.stats:
        # The shared stats block prints once; the templates that use it
        # follow without repeating it.
        result.sections.append(_stats_section(game, query))
        for index in indexes:
            for template in index.by_stats.get(query, ()):
                result.sections.append(
                    _template_section(game, template, include_stats=False)
                )
        result.sections.extend(_recipe_sections(game, query))
        return result

    for index in indexes:
        for template in index.by_name.get(query.lower(), ()):
            result.sections.append(_template_section(game, template))
    if result.found:
        result.sections.extend(_recipe_sections(game, query))
        return result

    # An exact display-name match (case- and apostrophe-folded) is as
    # good as an identifier hit: show the full record(s).
    folded = _fold(query)
    for index in indexes:
        for template in index.by_map_key.values():
            if template.display_name and _fold(template.display_name) == folded:
                result.sections.append(_template_section(game, template))
                if len(result.sections) >= 10:
                    break
    if result.found:
        return result

    # Ids that exist only in the crafting database (many recipe objects
    # are neither stats entries nor template names).
    recipe_sections = _recipe_sections(game, query)
    if recipe_sections:
        result.sections.extend(recipe_sections)
        return result

    # Fuzzy fallback: substring over template/instance names, display
    # names, and stats entry names.
    needle = _fold(query)
    seen: set[str] = set()
    for index in indexes:
        for template in index.by_map_key.values():
            if needle in _fold(template.name) or needle in _fold(template.display_name):
                label = f"{template.name}  ({template.display_name})  {template.map_key}"
                if label not in seen:
                    seen.add(label)
                    result.suggestions.append(label)
                if len(result.suggestions) >= _MAX_SUGGESTIONS:
                    return result
    for entry in game.stats:
        if needle in _fold(entry.name):
            label = f"{entry.name}  (stats: {entry.type or 'entry'})"
            if label not in seen:
                seen.add(label)
                result.suggestions.append(label)
            if len(result.suggestions) >= _MAX_SUGGESTIONS:
                return result
    # Localization text: finds strings nothing in the template/stats
    # indexes references (level-instance names, dialog, journal text).
    for handle, text in game.localization:
        if needle in _fold(text):
            label = f"{handle}  (text: {text})"
            if label not in seen:
                seen.add(label)
                result.suggestions.append(label)
            if len(result.suggestions) >= _MAX_SUGGESTIONS:
                break
    return result


def _recipe_sections(game: Game, object_id: str) -> list[str]:
    """Crafting cross-references for a stats/template id."""
    sections = []
    used_in = game.recipes.using_ingredient(object_id)
    produced_by = game.recipes.producing(object_id)
    for label, recipes in (("ingredient in", used_in), ("crafted by", produced_by)):
        for recipe in recipes[:10]:
            sections.append(f"{label}: {_format_recipe(recipe)}")
    return sections


def _format_recipe(recipe) -> str:
    inputs = " + ".join(i.object for i in recipe.ingredients if i.object) or "?"
    outputs = ", ".join(
        f"{r.object} x{r.amount}" for r in recipe.results if r.object
    ) or "?"
    return f"{recipe.name}: {inputs} -> {outputs}"


def _template_section(
    game: Game, template: RootTemplate, include_stats: bool = True
) -> str:
    kind = "instance" if "instance" in template.type else "template"
    lines = [f"{kind} {template.map_key}"]
    base = game.templates.by_map_key.get(template.parent_template)
    for label, value in (
        ("name", template.name),
        ("display name", template.display_name),
        ("handle", template.handle),
        ("type", template.type),
        ("level", template.level),
        ("stats", template.stats),
        ("icon", template.icon),
        ("base template", template.parent_template),
        ("base name", base.name if base else ""),
        ("source", template.source),
    ):
        if value:
            lines.append(f"  {label}: {value}")
    # An instance without its own stats inherits its base template's.
    if include_stats and not template.stats and base and base.stats:
        template = base
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
