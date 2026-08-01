"""Game index, patch layering, and lookup tests over synthetic paks."""

from __future__ import annotations

import json

import pytest

from dos2forge.cli.main import main
from dos2forge.game import Game
from dos2forge.lookup import format_report, lookup
from dos2forge.pak import CompressionMethod, PakWriter
from dos2forge.parsers.localization import LocalizationEntry, write_localization
from dos2forge.parsers.lsf import write_lsf
from dos2forge.parsers.lsx import LsxAttribute, LsxDocument, LsxNode

SWORD_KEY = "123e4567-e89b-42d3-a456-426614174000"
RING_KEY = "deadbeef-0000-4000-8000-000000000001"
SWORD_HANDLE = "h11111111g2222g3333g4444g555555555555"


def _template_node(map_key, name, handle, stats, type_="item"):
    node = LsxNode(id="GameObjects")
    node.attributes["MapKey"] = LsxAttribute(id="MapKey", type="FixedString", value=map_key)
    node.attributes["Name"] = LsxAttribute(id="Name", type="LSString", value=name)
    node.attributes["Type"] = LsxAttribute(id="Type", type="FixedString", value=type_)
    node.attributes["Stats"] = LsxAttribute(id="Stats", type="FixedString", value=stats)
    node.attributes["DisplayName"] = LsxAttribute(
        id="DisplayName", type="TranslatedString", value="Inline Name", handle=handle
    )
    return node


def _templates_lsf(*nodes) -> bytes:
    root = LsxNode(id="Templates")
    root.children.extend(nodes)
    document = LsxDocument()
    document.regions["Templates"] = root
    return write_lsf(document)


@pytest.fixture
def game_dir(tmp_path):
    shared = PakWriter(compression=CompressionMethod.LZ4)
    shared.add(
        "Public/Shared/RootTemplates/_merged.lsf",
        _templates_lsf(
            _template_node(SWORD_KEY, "WPN_Sword_1H_A", SWORD_HANDLE, "WPN_Sword_1H"),
            _template_node(RING_KEY, "LOOT_Migo_Ring", "h9g9g9g9g9", "LOOT_MigosRing"),
        ),
    )
    shared.add(
        "Public/Shared/Stats/Generated/Data/Weapon.txt",
        b'new entry "_BaseWeapon"\ndata "Durability" "20"\n\n'
        b'new entry "WPN_Sword_1H"\ntype "Weapon"\nusing "_BaseWeapon"\n'
        b'data "Damage Range" "4"\ndata "Damage Type" "Slashing"\n',
    )
    shared.add(
        "Localization/English/english.xml",
        write_localization(
            [LocalizationEntry(handle=SWORD_HANDLE, version=1, text="Iron Sword")]
        ),
    )
    shared.write(tmp_path / "Shared.pak")

    # The patch redefines the sword template (same MapKey, new name) and
    # layers a stats delta over the base Weapon.txt.
    patch = PakWriter(compression=CompressionMethod.LZ4)
    patch.add(
        "Public/Shared/RootTemplates/_merged.lsf",
        _templates_lsf(
            _template_node(SWORD_KEY, "WPN_Sword_1H_B", SWORD_HANDLE, "WPN_Sword_1H"),
        ),
    )
    patch.add(
        "Public/Shared/Stats/Generated/Data/Weapon.txt",
        b'new entry "WPN_Sword_1H"\nusing "WPN_Sword_1H"\ndata "Damage Range" "6"\n',
    )
    patch.write(tmp_path / "Patch2.pak")
    return tmp_path


def test_patch_template_shadows_base(game_dir):
    with Game(data_dir=game_dir) as game:
        assert len(game.templates) == 2
        sword = game.templates.by_map_key[SWORD_KEY]
        assert sword.name == "WPN_Sword_1H_B"  # patch definition wins
        assert not game.templates.by_name.get("wpn_sword_1h_a")
        assert game.templates.by_name["wpn_sword_1h_b"] == [sword]


def test_stats_layering_across_paks(game_dir):
    with Game(data_dir=game_dir) as game:
        resolved = game.stats.resolved("WPN_Sword_1H")
        assert resolved["Damage Range"] == "6"  # patch delta
        assert resolved["Damage Type"] == "Slashing"  # base definition
        assert resolved["Durability"] == "20"  # inherited from _BaseWeapon


def test_display_name_resolves_through_localization(game_dir):
    with Game(data_dir=game_dir) as game:
        sword = game.templates.by_map_key[SWORD_KEY]
        assert sword.display_name == "Iron Sword"
        # No localization entry for the ring: inline text is the fallback.
        assert game.templates.by_map_key[RING_KEY].display_name == "Inline Name"


def test_lookup_by_uuid_stats_name_and_handle(game_dir):
    with Game(data_dir=game_dir) as game:
        by_uuid = lookup(game, SWORD_KEY)
        assert by_uuid.found
        report = format_report(by_uuid)
        assert "WPN_Sword_1H_B" in report
        assert "Damage Range: 6" in report

        by_stats = lookup(game, "WPN_Sword_1H")
        assert by_stats.found
        assert "template " + SWORD_KEY in format_report(by_stats)

        by_handle = lookup(game, SWORD_HANDLE)
        assert by_handle.found
        assert "Iron Sword" in format_report(by_handle)


def test_lookup_falls_back_to_suggestions(game_dir):
    with Game(data_dir=game_dir) as game:
        result = lookup(game, "migo")
        assert not result.found
        assert any("LOOT_Migo_Ring" in s for s in result.suggestions)
        assert not lookup(game, "zzz_nothing").suggestions


def test_read_prefers_patch_copy(game_dir):
    with Game(data_dir=game_dir) as game:
        data = game.read("Public/Shared/Stats/Generated/Data/Weapon.txt")
        assert b'using "WPN_Sword_1H"' in data  # the patch's delta file


def test_cli_lookup_and_templates_export(game_dir, tmp_path, capsys):
    assert main(["--data-dir", str(game_dir), "lookup", "WPN_Sword_1H"]) == 0
    assert "Damage Range: 6" in capsys.readouterr().out

    out = tmp_path / "templates.json"
    assert main(["--data-dir", str(game_dir), "templates", "-o", str(out)]) == 0
    records = json.loads(out.read_text("utf-8"))
    assert len(records) == 2
    by_key = {r["map_key"]: r for r in records}
    assert by_key[SWORD_KEY]["display_name"] == "Iron Sword"
    assert by_key[SWORD_KEY]["stats"] == "WPN_Sword_1H"

    assert main(["--data-dir", str(game_dir), "lookup", "zzz_nothing"]) == 1
