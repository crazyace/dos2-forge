"""Stats .txt parsing/writing tests."""

from __future__ import annotations

import pytest

from dos2forge.parsers.stats import (
    StatsCollection,
    StatsParseError,
    StatsWriteError,
    parse_stats,
    parse_stats_document,
    write_stats_document,
)

WEAPON_TXT = """\
new entry "_BaseWeapon"
data "Durability" "100"
data "Value" "1"

new entry "WPN_Sword_1H"
type "Weapon"
using "_BaseWeapon"
data "Damage Type" "Slashing"
data "Damage Range" "4"
"""


def test_parse_entries():
    entries = parse_stats(WEAPON_TXT)
    assert [e.name for e in entries] == ["_BaseWeapon", "WPN_Sword_1H"]
    sword = entries[1]
    assert sword.type == "Weapon"
    assert sword.using == "_BaseWeapon"
    assert sword.get("Damage Type") == "Slashing"


def test_inheritance_resolution():
    stats = StatsCollection()
    stats.load_text(WEAPON_TXT)
    resolved = stats.resolved("WPN_Sword_1H")
    assert resolved["Durability"] == "100"  # inherited
    assert resolved["Damage Range"] == "4"  # own


def test_self_using_layers_over_previous_definition():
    stats = StatsCollection()
    stats.load_text(WEAPON_TXT)
    stats.load_text(
        'new entry "WPN_Sword_1H"\nusing "WPN_Sword_1H"\ndata "Damage Range" "6"\n'
    )
    resolved = stats.resolved("WPN_Sword_1H")
    assert resolved["Damage Range"] == "6"  # patched
    assert resolved["Damage Type"] == "Slashing"  # from the earlier layer
    assert resolved["Durability"] == "100"  # still inherited


def test_globals():
    document = parse_stats_document('key "AttributeBaseValue","10"\n')
    assert document.globals == {"AttributeBaseValue": "10"}


def test_unmodeled_blocks_are_tolerated():
    text = (
        'new equipment "EQ_Test"\n'
        'add equipmentgroup\n'
        'add equipment entry "WPN_Sword_1H"\n'
        "\n" + WEAPON_TXT
    )
    entries = parse_stats(text)
    assert [e.name for e in entries] == ["_BaseWeapon", "WPN_Sword_1H"]


def test_mixed_case_block_kinds_are_tolerated():
    # Retail DOS2 grammar: block kinds are not all-lowercase, and their
    # type/data lines belong to the (unmodeled, discarded) block rather
    # than being orphans.  Seen in CraftingStationsItemComboPreviewData.txt.
    text = (
        'new CraftingStationsItemComboPreviewData "AB_Combo"\n'
        'type "Object"\n'
        'data "Icon" "some_icon"\n'
        'new DeltaMod "Boost_Weapon"\n'
        'data "Boost" "_Boost_Weapon_Damage"\n'
        "\n" + WEAPON_TXT
    )
    entries = parse_stats(text)
    assert [e.name for e in entries] == ["_BaseWeapon", "WPN_Sword_1H"]


def test_malformed_structural_line_is_rejected():
    with pytest.raises(StatsParseError, match="malformed"):
        parse_stats('new entry "A"\ndata "Key" trailing junk\n')


def test_data_outside_block_is_rejected():
    with pytest.raises(StatsParseError, match="outside any"):
        parse_stats('data "Key" "Value"\n')


def test_write_round_trip():
    document = parse_stats_document(WEAPON_TXT)
    reparsed = parse_stats_document(write_stats_document(document))
    assert [e.name for e in reparsed.entries] == ["_BaseWeapon", "WPN_Sword_1H"]
    assert reparsed.entries[1].data == document.entries[1].data


def test_unwritable_values_fail_loudly():
    document = parse_stats_document(WEAPON_TXT)
    document.entries[0].data["Comment"] = 'has "quotes"'
    with pytest.raises(StatsWriteError, match="no escape syntax"):
        write_stats_document(document)


def test_inheritance_cycle_is_rejected():
    stats = StatsCollection()
    stats.load_text('new entry "A"\nusing "B"\n\nnew entry "B"\nusing "A"\n')
    with pytest.raises(StatsParseError, match="cycle"):
        stats.resolved("A")
