"""Crafting recipe (ItemCombos) parser tests."""

from __future__ import annotations

from dos2forge.parsers.itemcombos import RecipeIndex, parse_item_combos

SAMPLE = """\
new ItemCombination "BoletusWaterFlask"
data "Type 1" "Object"
data "Object 1" "CON_Herb_Boletus_A"
data "Combine 1" "Base"
data "Type 2" "Object"
data "Object 2" "CON_Flask_Water"
data "Combine 2" "Base"
data "CraftingStation" "None"

new ItemCombinationResult "BoletusWaterFlask_1"
data "Result 1" "CON_Potion_Poison_Minor"
data "ResultAmount 1" "2"

new CraftingStationsItemComboPreviewData "BoletusWaterFlask"
data "Icon" "some_icon"
"""


def test_parse_links_results_to_combination():
    recipes = parse_item_combos(SAMPLE, source="ItemCombos.txt")
    assert len(recipes) == 1
    recipe = recipes[0]
    assert recipe.name == "BoletusWaterFlask"
    assert [(i.object, i.type) for i in recipe.ingredients] == [
        ("CON_Herb_Boletus_A", "Object"), ("CON_Flask_Water", "Object"),
    ]
    assert [(r.object, r.amount) for r in recipe.results] == [
        ("CON_Potion_Poison_Minor", "2"),
    ]
    assert recipe.data["CraftingStation"] == "None"  # unnumbered preserved
    assert recipe.source == "ItemCombos.txt"


def test_result_block_before_combination_still_links():
    reordered = SAMPLE.split("\n\n")
    text = "\n\n".join([reordered[1], reordered[0], reordered[2]])
    (recipe,) = parse_item_combos(text)
    assert recipe.results[0].object == "CON_Potion_Poison_Minor"


def test_unlinked_result_and_preview_blocks_are_ignored():
    recipes = parse_item_combos(
        'new ItemCombinationResult "Orphan_1"\ndata "Result 1" "X"\n'
        'new ItemComboPreviewData "Whatever"\ndata "Icon" "y"\n'
    )
    assert recipes == []


def test_index_lookups_and_layering():
    index = RecipeIndex(parse_item_combos(SAMPLE))
    assert index.using_ingredient("CON_Flask_Water")[0].name == "BoletusWaterFlask"
    assert index.producing("CON_Potion_Poison_Minor")[0].name == "BoletusWaterFlask"
    assert index.using_ingredient("nope") == []
    # A later definition of the same name replaces the earlier one.
    (patched,) = parse_item_combos(
        'new ItemCombination "BoletusWaterFlask"\n'
        'data "Type 1" "Object"\ndata "Object 1" "CON_Mushroom_B"\n'
    )
    index.add(patched)
    assert len(index) == 1
    assert index.by_name["BoletusWaterFlask"].ingredients[0].object == "CON_Mushroom_B"
