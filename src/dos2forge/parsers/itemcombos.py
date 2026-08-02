"""Parser for DOS2 crafting recipes (``ItemCombos*.txt``).

Recipes use the stats line grammar with their own block kinds::

    new ItemCombination "BoletusWaterFlask"
    data "Type 1" "Object"
    data "Object 1" "CON_Herb_Boletus_A"
    data "Combine 1" "Base"
    data "Type 2" "Object"
    data "Object 2" "CON_Flask_Water"
    data "Combine 2" "Base"

    new ItemCombinationResult "BoletusWaterFlask_1"
    data "Result 1" "CON_Potion_Poison_Minor"
    data "ResultAmount 1" "1"

Result blocks are linked to their combination by name: the block is
named after the combination, optionally with a ``_<n>`` suffix (one
block per auto-level tier).  Unrecognized fields are preserved in
``data`` so nothing is lost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

_LINE_RE = re.compile(r'^(?P<keyword>[A-Za-z ]+?)\s+"(?P<args>.*)"\s*$')
_ARG_SPLIT_RE = re.compile(r'"\s*,\s*"|"\s+"')
_NUMBERED_RE = re.compile(r"^(?P<key>[A-Za-z ]+?)\s*(?P<index>\d+)$")


@dataclass
class Ingredient:
    object: str = ""
    type: str = ""
    combine: str = ""
    transform: str = ""


@dataclass
class ResultItem:
    object: str = ""
    amount: str = "1"
    boost: str = ""


@dataclass
class Recipe:
    name: str
    ingredients: list[Ingredient] = field(default_factory=list)
    results: list[ResultItem] = field(default_factory=list)
    data: dict[str, str] = field(default_factory=dict)  # unnumbered fields
    source: str | None = None


_INGREDIENT_FIELDS = {
    "type": "type",
    "object": "object",
    "combine": "combine",
    "transform": "transform",
    "ingredienttype": "type",
    "ingredient": "object",
}
_RESULT_FIELDS = {
    "result": "object",
    "resultamount": "amount",
    "result amount": "amount",
    "boost": "boost",
}


def _numbered(mapping: dict, cls, fields: dict, key: str, value: str) -> bool:
    match = _NUMBERED_RE.match(key)
    if not match:
        return False
    attr = fields.get(match.group("key").strip().lower().replace(" ", ""))
    if attr is None:
        return False
    index = int(match.group("index"))
    item = mapping.setdefault(index, cls())
    setattr(item, attr, value)
    return True


def parse_item_combos(text: str, source: str | None = None) -> list[Recipe]:
    recipes: dict[str, Recipe] = {}
    # Result blocks are linked to combinations after the whole document
    # is parsed, since either may appear first.
    pending_results: list[tuple[str, dict[int, ResultItem]]] = []
    current: Recipe | None = None
    current_results: dict[int, ResultItem] | None = None
    current_ingredients: dict[int, Ingredient] = {}

    def flush() -> None:
        nonlocal current, current_ingredients
        if current is not None:
            current.ingredients = [
                current_ingredients[i] for i in sorted(current_ingredients)
            ]
        current, current_ingredients = None, {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        keyword = match.group("keyword")
        args = _ARG_SPLIT_RE.split(match.group("args"))
        if keyword == "new ItemCombination":
            flush()
            current_results = None
            current = recipes.setdefault(args[0], Recipe(name=args[0], source=source))
            current.source = source
        elif keyword == "new ItemCombinationResult":
            flush()
            current_results = {}
            pending_results.append((args[0], current_results))
        elif keyword.startswith("new "):
            flush()
            current_results = None  # some other block kind
        elif keyword == "data" and len(args) >= 2:
            key, value = args[0], args[1]
            if current is not None:
                if not _numbered(current_ingredients, Ingredient,
                                 _INGREDIENT_FIELDS, key, value):
                    current.data[key] = value
            elif current_results is not None:
                _numbered(current_results, ResultItem, _RESULT_FIELDS, key, value)
    flush()

    for result_name, items in pending_results:
        recipe = _owner(recipes, result_name)
        if recipe is not None:
            recipe.results.extend(items[i] for i in sorted(items) if items[i].object)
    return list(recipes.values())


def _owner(recipes: dict[str, Recipe], result_name: str) -> Recipe | None:
    """The combination a result block belongs to (exact or ``_<n>``-suffixed)."""
    if result_name in recipes:
        return recipes[result_name]
    stem = result_name.rsplit("_", 1)[0]
    return recipes.get(stem)


class RecipeIndex:
    """Recipes with by-ingredient / by-result lookups (later adds win)."""

    def __init__(self, recipes: Iterable[Recipe] = ()):
        self.by_name: dict[str, Recipe] = {}
        for recipe in recipes:
            self.add(recipe)

    def add(self, recipe: Recipe) -> None:
        self.by_name[recipe.name] = recipe

    def __len__(self) -> int:
        return len(self.by_name)

    def __iter__(self):
        return iter(self.by_name.values())

    def using_ingredient(self, object_id: str) -> list[Recipe]:
        return [
            r for r in self
            if any(i.object == object_id for i in r.ingredients)
        ]

    def producing(self, object_id: str) -> list[Recipe]:
        return [
            r for r in self
            if any(item.object == object_id for item in r.results)
        ]
