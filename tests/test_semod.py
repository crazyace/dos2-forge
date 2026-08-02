"""SE mod scaffolding tests."""

from __future__ import annotations

import json

import pytest

from dos2forge.cli.main import main
from dos2forge.lualint import lint_path
from dos2forge.parsers.lsx import parse_lsx
from dos2forge.semod import pack_version32, scaffold_mod, unpack_version32

MOD_UUID = "11111111-2222-4333-8444-555555555555"


def test_pack_version32_round_trip():
    assert pack_version32(1) == 268435456  # 1.0.0.0, the usual first release
    assert pack_version32(3, 6, 6, 0) == 906362880
    assert unpack_version32(pack_version32(3, 6, 6, 12)) == (3, 6, 6, 12)
    with pytest.raises(ValueError):
        pack_version32(16)  # major only has 4 bits
    with pytest.raises(ValueError):
        pack_version32(1, 0, 256)


def test_scaffold_creates_se_layout(tmp_path):
    result = scaffold_mod(
        tmp_path, "My Mod!", author="A", description="D", mod_uuid=MOD_UUID
    )
    assert result.folder == f"MyMod_{MOD_UUID}"
    mod = tmp_path / "Mods" / result.folder
    assert result.mod_dir == mod
    lua = mod / "Story" / "RawFiles" / "Lua"
    for expected in (
        mod / "meta.lsx",
        mod / "OsiToolsConfig.json",
        lua / "BootstrapServer.lua",
        lua / "BootstrapClient.lua",
        lua / "Server" / "Main.lua",
        lua / "Client" / "Main.lua",
    ):
        assert expected.is_file()
        assert expected in result.files

    # meta.lsx must round-trip through our own parser with the structure
    # shipping SE mods use.
    document = parse_lsx((mod / "meta.lsx").read_bytes())
    info = next(document.find_all("ModuleInfo"))
    assert info.get("UUID") == MOD_UUID
    assert info.get("Folder") == result.folder
    assert info.get("Name") == "My Mod!"
    assert info.get("Author") == "A"
    assert info.get("Type") == "Add-on"
    assert int(info.get("Version")) == pack_version32(1, 0, 0, 0)
    assert next(document.find_all("PublishVersion")).get("Version") == info.get("Version")
    assert next(document.find_all("Target")).get("Object") == "Story"
    # DOS2-native LSX spells types numerically (FixedString = 22).
    assert 'type="22"' in (mod / "meta.lsx").read_text("utf-8")

    config = json.loads((mod / "OsiToolsConfig.json").read_text("utf-8"))
    assert config == {
        "RequiredExtensionVersion": 58,
        "ModTable": "MyMod",
        "FeatureFlags": ["Lua", "OsirisExtensions"],
    }

    bootstrap = (lua / "BootstrapServer.lua").read_text("utf-8")
    assert 'Ext.Require("Server/Main.lua")' in bootstrap


def test_scaffold_output_passes_lint(tmp_path):
    scaffold_mod(tmp_path, "CleanMod", mod_uuid=MOD_UUID)
    assert lint_path(tmp_path) == []


def test_scaffold_rejections(tmp_path):
    scaffold_mod(tmp_path, "TakenMod", mod_uuid=MOD_UUID)
    with pytest.raises(ValueError, match="already exists"):
        scaffold_mod(tmp_path, "TakenMod", mod_uuid=MOD_UUID)
    with pytest.raises(ValueError, match="not a valid UUID"):
        scaffold_mod(tmp_path, "Other", mod_uuid="not-a-uuid")
    with pytest.raises(ValueError, match="unknown FeatureFlags"):
        scaffold_mod(tmp_path, "Other", feature_flags=("Lua", "LUA"))
    with pytest.raises(ValueError, match="no usable identifier"):
        scaffold_mod(tmp_path, "!!!")


def test_cli_new_and_lint(tmp_path, capsys):
    root = tmp_path / "project"
    assert main(["new", "TestMod", "-o", str(root), "--uuid", MOD_UUID]) == 0
    out = capsys.readouterr().out
    assert f"scaffolded TestMod_{MOD_UUID} (module UUID {MOD_UUID})" in out
    assert (root / "Mods" / f"TestMod_{MOD_UUID}" / "meta.lsx").is_file()

    assert main(["lint", str(root), "--no-game"]) == 0
    assert "0 error(s), 0 warning(s)" in capsys.readouterr().out

    # Same folder again must fail cleanly, not stack traces.
    assert main(["new", "TestMod", "-o", str(root), "--uuid", MOD_UUID]) == 1
