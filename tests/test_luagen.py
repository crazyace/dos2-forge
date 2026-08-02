"""Lua constant module generation tests."""

from __future__ import annotations

from dos2forge.cli.main import main
from dos2forge.game import Game
from dos2forge.luagen import generate_lua

from test_game import RING_INSTANCE_KEY, SWORD_KEY, game_dir  # noqa: F401


def test_generate_lua_modules(game_dir):
    with Game(data_dir=game_dir) as game:
        modules = generate_lua(game)
    assert set(modules) == {
        "Templates.lua", "Instances.lua", "Skills.lua", "Statuses.lua",
    }
    templates = modules["Templates.lua"]
    assert f'["WPN_Sword_1H_B"] = "{SWORD_KEY}",' in templates
    assert templates.startswith("-- Root template name")
    assert "return {" in templates
    instances = modules["Instances.lua"]
    assert f'["S_FTJ_MigoRing"] = "{RING_INSTANCE_KEY}",' in instances
    skills = modules["Skills.lua"]
    assert '["Projectile_Fireball"] = "Projectile_Fireball",' in skills


def test_ambiguous_names_are_omitted_with_note():
    from dos2forge.game import RootTemplate, RootTemplateIndex
    from dos2forge.luagen import _module, _uuid_map

    index = RootTemplateIndex()
    index.add(RootTemplate(map_key="uuid-1", name="Dup"))
    index.add(RootTemplate(map_key="uuid-2", name="Dup"))
    pairs, ambiguous = _uuid_map(index)
    assert pairs == {}
    assert ambiguous == {"Dup"}
    assert "-- omitted (1 ambiguous names):" in _module("t", pairs, ambiguous)


def test_cli_lua(game_dir, tmp_path, capsys):
    out = tmp_path / "lua"
    assert main(["--data-dir", str(game_dir), "lua", "-o", str(out)]) == 0
    assert (out / "Templates.lua").exists()
    assert SWORD_KEY in (out / "Templates.lua").read_text("utf-8")
