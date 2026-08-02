"""Typed dataset extraction: skills, statuses, weapons, armor.

Each dataset is the stats entries of the matching types with inheritance
resolved and the ``DisplayName``/``Description`` localization handles
resolved to text, as plain dict records ready for JSON/CSV export.
"""

from __future__ import annotations

from .game import Game

DATASET_TYPES = {
    "skills": ("SkillData",),
    "statuses": ("StatusData",),
    "weapons": ("Weapon",),
    "armor": ("Armor", "Shield"),
    "potions": ("Potion",),
    "objects": ("Object",),
}


def dataset(game: Game, name: str) -> list[dict]:
    types = set(DATASET_TYPES[name])
    records = []
    for entry in game.stats:
        effective_type = game.stats.resolved_type(entry.name)
        if effective_type not in types:
            continue
        resolved = game.stats.resolved(entry.name)
        records.append(
            {
                "name": entry.name,
                "type": effective_type,
                "using": entry.using or "",
                "display_name": game.localization.resolve(
                    resolved.get("DisplayName")
                ),
                "description": game.localization.resolve(
                    resolved.get("Description")
                ),
                "data": resolved,
            }
        )
    records.sort(key=lambda r: r["name"].lower())
    return records
