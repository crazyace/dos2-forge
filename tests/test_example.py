"""End-to-end test mirroring docs/example.md: the shovel mod walkthrough.

The synthetic game pak carries the real TOOL_Shovel_A data (UUID, stats,
localization handle) as reported by ``dos2forge lookup shovel`` on a
Definitive Edition install, so the doc's steps run verbatim.
"""

from __future__ import annotations

from dos2forge.cli.main import main
from dos2forge.game import Game
from dos2forge.lualint import lint_path
from dos2forge.pak import CompressionMethod, PakWriter
from dos2forge.parsers.localization import LocalizationEntry, write_localization

import pytest

from test_game import _template_node, _templates_lsf

SHOVEL_KEY = "41486dd2-3fd5-464e-870e-844120cf0517"
SHOVEL_HANDLE = "h8776bd07g9199g4a09ga00age3a3a055d207"
MOD_UUID = "cccccccc-dddd-4eee-8fff-000000000001"

BOOTSTRAP_EXTRA = 'Templates = Ext.Require("Generated/Templates.lua")\n'

MAIN_LUA = """\
local function onSessionLoaded()
    Ext.Print("[VampiricBlades] server ready")
end

Ext.RegisterListener("SessionLoaded", onSessionLoaded)

Ext.RegisterOsirisListener("GameStarted", 2, "after", function(level, isEditorMode)
    local host = Osi.CharacterGetHostCharacter()
    local shovel = Templates["TOOL_Shovel_A"]
    if Osi.ItemTemplateIsInCharacterInventory(host, shovel) == 0 then
        Osi.ItemTemplateAddTo(shovel, host, 1, 1)
    end
end)
"""

# The hardcoded alternative the doc mentions: Osiris' editor-style
# combined GUID string, resolved by its trailing UUID.
HARDCODED_LUA = f'local SHOVEL = "TOOL_Shovel_A_{SHOVEL_KEY}"\n'


@pytest.fixture
def shovel_game(tmp_path):
    game_dir = tmp_path / "game"
    game_dir.mkdir()
    shared = PakWriter(compression=CompressionMethod.LZ4)
    shared.add(
        "Public/Shared/RootTemplates/_merged.lsf",
        _templates_lsf(
            _template_node(SHOVEL_KEY, "TOOL_Shovel_A", SHOVEL_HANDLE, "TOOL_Shovel_A")
        ),
    )
    shared.add(
        "Public/Shared/Stats/Generated/Data/Object.txt",
        b'new entry "TOOL_Shovel_A"\ntype "Object"\n'
        b'data "Value" "2"\ndata "Weight" "500"\n',
    )
    shared.add(
        "Localization/English/english.xml",
        write_localization(
            [LocalizationEntry(handle=SHOVEL_HANDLE, version=1, text="Shovel")]
        ),
    )
    shared.write(game_dir / "Shared.pak")
    return game_dir


def test_shovel_walkthrough(shovel_game, tmp_path, capsys):
    root = tmp_path / "project"

    # Step 2: scaffold.
    assert main(["new", "Vampiric Blades", "-o", str(root), "--uuid", MOD_UUID]) == 0
    folder = f"VampiricBlades_{MOD_UUID}"
    lua_dir = root / "Mods" / folder / "Story" / "RawFiles" / "Lua"

    # Step 3: generate constants into the mod.
    generated = lua_dir / "Generated"
    assert main(
        ["--data-dir", str(shovel_game), "--no-cache", "lua", "-o", str(generated)]
    ) == 0
    templates_lua = (generated / "Templates.lua").read_text("utf-8")
    assert f'["TOOL_Shovel_A"] = "{SHOVEL_KEY}",' in templates_lua

    # Step 4: the doc's bootstrap addition and Main.lua verbatim.
    bootstrap = lua_dir / "BootstrapServer.lua"
    bootstrap.write_text(bootstrap.read_text("utf-8") + BOOTSTRAP_EXTRA, "utf-8")
    (lua_dir / "Server" / "Main.lua").write_text(MAIN_LUA, "utf-8")
    (lua_dir / "Server" / "Hardcoded.lua").write_text(HARDCODED_LUA, "utf-8")
    (lua_dir / "BootstrapServer.lua").write_text(
        bootstrap.read_text("utf-8") + 'Ext.Require("Server/Hardcoded.lua")\n', "utf-8"
    )

    # Step 5: lint is clean, including the combined-form GUID string.
    with Game(data_dir=shovel_game, use_cache=False) as game:
        assert lint_path(root, game) == []

    # A typo'd UUID inside the combined form is caught.
    (lua_dir / "Server" / "Hardcoded.lua").write_text(
        HARDCODED_LUA.replace("41486dd2", "99999999"), "utf-8"
    )
    with Game(data_dir=shovel_game, use_cache=False) as game:
        issues = lint_path(root, game)
    assert [(i.severity, i.code) for i in issues] == [("warning", "uuid-unknown")]

    # And the CLI agrees end to end (warnings don't fail the build).
    assert main(["--data-dir", str(shovel_game), "--no-cache", "lint", str(root)]) == 0
    out = capsys.readouterr().out
    assert "uuid-unknown" in out
    assert "0 error(s), 1 warning(s)" in out
