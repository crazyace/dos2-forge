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
        "Public/Shared/Stats/Generated/ItemCombos.txt",
        b'new ItemCombination "PoisonFlask"\n'
        b'data "Type 1" "Object"\ndata "Object 1" "CON_Herb_Boletus_A"\n'
        b'data "Combine 1" "Base"\n'
        b'data "Type 2" "Object"\ndata "Object 2" "CON_Flask_Water"\n'
        b'data "Combine 2" "Base"\n\n'
        b'new ItemCombinationResult "PoisonFlask_1"\n'
        b'data "Result 1" "WPN_Sword_1H"\ndata "ResultAmount 1" "1"\n',
    )
    shared.add(
        "Public/Shared/Stats/Generated/Data/Skill.txt",
        b'new entry "Projectile_Fireball"\ntype "SkillData"\n'
        b'data "DisplayName" "' + SWORD_HANDLE.encode() + b';1"\n'
        b'data "Damage" "8"\n',
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

    # A level-instance file: a unique ring placed in Fort Joy that
    # references the generic ring template and overrides its name.
    shared_globals = PakWriter(compression=CompressionMethod.LZ4)
    ring_node = _template_node(
        "0000f00d-0000-4000-8000-00000000cafe",
        "S_FTJ_MigoRing",
        "hffffffffg0000g0000g0000g0000000000ff",
        "",
        type_="",
    )
    ring_node.attributes["TemplateName"] = LsxAttribute(
        id="TemplateName", type="FixedString", value=RING_KEY
    )
    ring_node.attributes["LevelName"] = LsxAttribute(
        id="LevelName", type="FixedString", value="FJ_FortJoy_Main"
    )
    shared_globals.add(
        "Mods/Shared/Globals/FJ_FortJoy_Main/Items/_merged.lsf",
        _templates_lsf(ring_node),
    )
    shared_globals.write(tmp_path / "Origins.pak")
    return tmp_path


RING_INSTANCE_KEY = "0000f00d-0000-4000-8000-00000000cafe"
RING_INSTANCE_HANDLE = "hffffffffg0000g0000g0000g0000000000ff"


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


def test_language_pak_under_localization_dir(game_dir):
    # DE ships text as Data/Localization/<Language>.pak whose XML paths
    # do not necessarily mention the language; a pak named for the
    # language contributes wholesale.
    ring_handle = "h9g9g9g9g9"
    loc_dir = game_dir / "Localization"
    loc_dir.mkdir()
    english = PakWriter()
    english.add(
        "Localization/english.xml",
        write_localization(
            [LocalizationEntry(handle=ring_handle, version=1, text="Migo's Ring")]
        ),
    )
    english.write(loc_dir / "English.pak")
    with Game(data_dir=game_dir) as game:
        assert game.templates.by_map_key[RING_KEY].display_name == "Migo's Ring"
        result = lookup(game, "migo's ring")  # exact display name -> full match
        assert result.found
        assert RING_KEY in format_report(result)


def test_lookup_folds_typographic_apostrophes(game_dir):
    ring_handle = "h9g9g9g9g9"
    loc_dir = game_dir / "Localization"
    loc_dir.mkdir()
    english = PakWriter()
    english.add(
        "Localization/english.xml",
        write_localization(
            # Game text spells it with U+2019; queries type ASCII '.
            [LocalizationEntry(handle=ring_handle, version=1, text="Migo’s Ring")]
        ),
    )
    english.write(loc_dir / "English.pak")
    with Game(data_dir=game_dir) as game:
        result = lookup(game, "migo's ring")  # folds ' vs U+2019 -> full match
        assert result.found
        assert RING_KEY in format_report(result)


def test_stats_lookup_prints_shared_stats_once(game_dir):
    with Game(data_dir=game_dir) as game:
        report = format_report(lookup(game, "WPN_Sword_1H"))
    assert report.count("stats entry WPN_Sword_1H") == 1
    assert "template " + SWORD_KEY in report


def test_lookup_searches_localization_text(game_dir):
    loc_dir = game_dir / "Localization"
    loc_dir.mkdir()
    english = PakWriter()
    english.add(
        "Localization/english.xml",
        write_localization(
            # A string no template or stats entry references at all.
            [LocalizationEntry(handle="h7g7g7g7g7", version=1, text="Migo’s Journal")]
        ),
    )
    english.write(loc_dir / "English.pak")
    with Game(data_dir=game_dir) as game:
        result = lookup(game, "migo's journal")
        assert any("h7g7g7g7g7" in s for s in result.suggestions)


def test_level_instances_are_indexed(game_dir):
    loc_dir = game_dir / "Localization"
    loc_dir.mkdir()
    english = PakWriter()
    english.add(
        "Localization/english.xml",
        write_localization(
            [
                LocalizationEntry(
                    handle=RING_INSTANCE_HANDLE, version=1, text="Migo's Ring"
                )
            ]
        ),
    )
    english.write(loc_dir / "English.pak")
    with Game(data_dir=game_dir) as game:
        instance = game.level_instances.by_map_key[RING_INSTANCE_KEY]
        assert instance.type == "item instance"
        assert instance.level == "FJ_FortJoy_Main"
        assert instance.parent_template == RING_KEY
        assert instance.display_name == "Migo's Ring"

        # An exact localized name is a full match reaching the instance.
        result = lookup(game, "migo's ring")
        assert result.found
        assert RING_INSTANCE_KEY in format_report(result)

        # Direct UUID lookup shows the instance with its base template.
        report = format_report(lookup(game, RING_INSTANCE_KEY))
        assert "instance " + RING_INSTANCE_KEY in report
        assert "base template: " + RING_KEY in report
        assert "base name: LOOT_Migo_Ring" in report


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


def test_recipes_indexed_and_cross_referenced(game_dir):
    with Game(data_dir=game_dir) as game:
        assert len(game.recipes) == 1
        recipe = game.recipes.by_name["PoisonFlask"]
        assert [i.object for i in recipe.ingredients] == [
            "CON_Herb_Boletus_A", "CON_Flask_Water",
        ]
        assert recipe.results[0].object == "WPN_Sword_1H"
        assert game.recipes.using_ingredient("CON_Flask_Water") == [recipe]
        assert game.recipes.producing("WPN_Sword_1H") == [recipe]

        report = format_report(lookup(game, "WPN_Sword_1H"))
        assert (
            "crafted by: PoisonFlask: CON_Herb_Boletus_A + CON_Flask_Water "
            "-> WPN_Sword_1H x1" in report
        )


def test_exact_display_name_lookup_is_a_full_match(game_dir):
    with Game(data_dir=game_dir) as game:
        result = lookup(game, "iron sword")  # exact display name, folded
        assert result.found
        assert "template " + SWORD_KEY in format_report(result)


def test_typed_datasets(game_dir):
    from dos2forge.datasets import dataset

    with Game(data_dir=game_dir) as game:
        skills = dataset(game, "skills")
        assert len(skills) == 1
        skill = skills[0]
        assert skill["name"] == "Projectile_Fireball"
        assert skill["display_name"] == "Iron Sword"  # handle resolved
        assert skill["data"]["Damage"] == "8"
        weapons = dataset(game, "weapons")
        assert [w["name"] for w in weapons] == ["WPN_Sword_1H"]
        assert weapons[0]["data"]["Damage Range"] == "6"  # patch-layered


def test_cli_recipes_and_export(game_dir, tmp_path, capsys):
    out = tmp_path / "recipes.json"
    assert main(["--data-dir", str(game_dir), "recipes", "-o", str(out)]) == 0
    assert json.loads(out.read_text("utf-8"))[0]["name"] == "PoisonFlask"

    export_dir = tmp_path / "export"
    assert main(["--data-dir", str(game_dir), "export", "all", "-o", str(export_dir)]) == 0
    skills = json.loads((export_dir / "skills.json").read_text("utf-8"))
    assert skills[0]["name"] == "Projectile_Fireball"


def test_cache_round_trip_and_invalidation(game_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("DOS2FORGE_CACHE", str(tmp_path / "cache"))
    with Game(data_dir=game_dir) as game:
        first_templates = len(game.templates)
        first_resolved = game.stats.resolved("WPN_Sword_1H")
        assert len(game.level_instances) == 1
    cache_files = sorted(p.name for p in (tmp_path / "cache").rglob("*.json.gz"))
    assert cache_files == [
        "instances.json.gz", "localization.json.gz",
        "stats.json.gz", "templates.json.gz",
    ]

    # A fresh Game must serve identical data from the cache.
    with Game(data_dir=game_dir) as game:
        assert len(game.templates) == first_templates
        assert game.templates.by_map_key[SWORD_KEY].name == "WPN_Sword_1H_B"
        assert game.stats.resolved("WPN_Sword_1H") == first_resolved
        assert game.level_instances.by_map_key[RING_INSTANCE_KEY].level == "FJ_FortJoy_Main"

    # Touching a pak invalidates: a new cache key directory appears.
    pak = game_dir / "Patch2.pak"
    pak.write_bytes(pak.read_bytes() + b"\x00")
    keys_before = {p.name for p in (tmp_path / "cache").iterdir()}
    with Game(data_dir=game_dir) as game:
        assert len(game.templates) == first_templates  # rebuilt, then re-cached
    keys_after = {p.name for p in (tmp_path / "cache").iterdir()}
    assert keys_before < keys_after


def test_cache_can_be_disabled(game_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("DOS2FORGE_CACHE", str(tmp_path / "cache"))
    with Game(data_dir=game_dir, use_cache=False) as game:
        assert len(game.templates) == 2
    assert not (tmp_path / "cache").exists()


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
