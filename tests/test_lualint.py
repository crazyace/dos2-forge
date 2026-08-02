"""Lua lint tests: setup files, requires, block balance, data references."""

from __future__ import annotations

import json

import pytest

from dos2forge.cli.main import main
from dos2forge.game import Game
from dos2forge.lualint import format_issues, lint_path
from dos2forge.parsers.lsx import write_lsx
from dos2forge.semod import scaffold_mod

from test_game import SWORD_KEY, game_dir  # noqa: F401
from test_game import _template_node

MOD_UUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    result = scaffold_mod(root, "LintMod", mod_uuid=MOD_UUID)
    return root, result


def _codes(issues):
    return [(i.severity, i.code) for i in issues]


def _lua_dir(result):
    return result.mod_dir / "Story" / "RawFiles" / "Lua"


def test_clean_scaffold_has_no_issues(project):
    root, _ = project
    assert lint_path(root) == []


def test_require_missing_and_case(project):
    root, result = project
    lua = _lua_dir(result)
    (lua / "Server" / "Extra.lua").write_text("local x = 1\n", "utf-8")
    bootstrap = lua / "BootstrapServer.lua"
    bootstrap.write_text(
        bootstrap.read_text("utf-8")
        + 'Ext.Require("Server/Nope.lua")\n'
        + 'Ext.Require("server/extra.lua")\n'
        + 'Ext.Require("Server\\\\Extra.lua")\n',
        "utf-8",
    )
    issues = lint_path(root)
    codes = _codes(issues)
    assert ("error", "require-missing") in codes
    assert ("warning", "require-case") in codes
    assert ("warning", "require-path") in codes
    missing = next(i for i in issues if i.code == "require-missing")
    assert "Server/Nope.lua" in missing.message
    assert missing.line == 5  # bootstrap body is 4 lines long
    case = next(i for i in issues if i.code == "require-case")
    assert "Server/Extra.lua" in case.message


def test_block_balance(project):
    root, result = project
    lua = _lua_dir(result)
    # Balanced constructs that trip naive keyword counting must stay clean:
    # while/for share their `end` with `do`, strings and comments may
    # contain keywords.
    (lua / "Server" / "Fine.lua").write_text(
        "while true do break end\n"
        "for i = 1, 10 do print(i) end\n"
        "for k, v in pairs({}) do print(k, v) end\n"
        'local s = "if without end"\n'
        "-- end end end\n"
        "--[[ function if do ]]\n"
        "local body = [[\n"
        "if x then\n"
        "]]\n"
        "if s then print(s) elseif body then print(body) else print() end\n"
        "repeat s = nil until s == nil\n",
        "utf-8",
    )
    assert lint_path(root) == []

    (lua / "Server" / "Broken.lua").write_text(
        "local function f()\n"
        "    if x then\n"
        "        print(1)\n"
        "end\n",
        "utf-8",
    )
    issues = lint_path(root)
    assert _codes(issues) == [("error", "lua-blocks")]
    assert "never closed" in issues[0].message
    assert issues[0].line == 1  # the unmatched opener is the function

    (lua / "Server" / "Broken.lua").write_text("print(1)\nend\n", "utf-8")
    issues = lint_path(root)
    assert _codes(issues) == [("error", "lua-blocks")]
    assert "no open block" in issues[0].message
    assert issues[0].line == 2


def test_unterminated_string(project):
    root, result = project
    (_lua_dir(result) / "Server" / "Bad.lua").write_text(
        'local s = "no closing quote\n', "utf-8"
    )
    issues = lint_path(root)
    assert ("error", "lua-string") in _codes(issues)


def test_config_checks(project):
    root, result = project
    config_path = result.mod_dir / "OsiToolsConfig.json"

    config_path.write_text(
        json.dumps(
            {
                "RequiredExtensionVersion": "58",
                "ModTable": "not an identifier",
                "FeatureFlags": ["LUA"],
                "Extra": 1,
            }
        ),
        "utf-8",
    )
    codes = _codes(lint_path(root))
    assert ("error", "config-version") in codes
    assert ("error", "config-flag") in codes  # "LUA" is a dead typo
    assert ("error", "config-lua") in codes  # Lua files but no "Lua" flag
    assert ("warning", "config-modtable") in codes
    assert ("warning", "config-key") in codes

    config_path.write_text("{ not json", "utf-8")
    assert ("error", "config-json") in _codes(lint_path(root))

    config_path.unlink()
    assert ("error", "config-missing") in _codes(lint_path(root))


def test_meta_and_bootstrap_checks(project):
    root, result = project
    meta = result.mod_dir / "meta.lsx"
    meta.write_text(
        meta.read_text("utf-8").replace(result.folder, "SomethingElse_123"), "utf-8"
    )
    assert ("warning", "meta-folder") in _codes(lint_path(root))

    meta.unlink()
    assert ("warning", "meta-missing") in _codes(lint_path(root))

    lua = _lua_dir(result)
    for name in ("BootstrapServer.lua", "BootstrapClient.lua"):
        (lua / name).unlink()
    assert ("warning", "bootstrap-missing") in _codes(lint_path(root))


def test_game_data_references(project, game_dir):  # noqa: F811
    root, result = project
    (_lua_dir(result) / "Server" / "Data.lua").write_text(
        f'local sword = "{SWORD_KEY}"\n'
        'local ghost = "99999999-9999-4999-8999-999999999999"\n'
        f'local me = "{MOD_UUID}"\n'  # the mod's own meta.lsx UUID is known
        'CharacterAddSkill(sword, "Projectile_Fireball")\n'
        'Osi.CharacterAddSkill(sword, "Projectile_Fireball_Typo")\n'
        'ApplyStatus(sword, "BURNING")\n'
        'Ext.GetStat("WPN_Sword_1H")\n'
        'Ext.GetStat("WPN_Nothing")\n'
        '-- ApplyStatus(sword, "COMMENTED_OUT")\n',
        "utf-8",
    )
    with Game(data_dir=game_dir, use_cache=False) as game:
        issues = lint_path(root, game)
    codes = _codes(issues)
    assert codes.count(("warning", "uuid-unknown")) == 1
    ghost = next(i for i in issues if i.code == "uuid-unknown")
    assert "99999999" in ghost.message and ghost.line == 2
    assert codes.count(("warning", "skill-unknown")) == 1
    assert "Projectile_Fireball_Typo" in next(
        i.message for i in issues if i.code == "skill-unknown"
    )
    # No StatusData at all in the fixture game: BURNING is unknown, and
    # the commented-out call must not add a second hit.
    assert codes.count(("warning", "status-unknown")) == 1
    assert codes.count(("warning", "stat-unknown")) == 1
    assert "WPN_Nothing" in next(i.message for i in issues if i.code == "stat-unknown")

    # Data checks are warnings: the CLI exit code stays 0.
    assert all(i.severity == "warning" for i in issues)


def test_mod_own_content_counts_as_known(project, game_dir):  # noqa: F811
    root, result = project
    public = root / "Public" / result.folder
    (public / "Stats" / "Generated" / "Data").mkdir(parents=True)
    (public / "Stats" / "Generated" / "Data" / "Status.txt").write_text(
        'new entry "MY_STATUS"\ntype "StatusData"\n\n'
        'new entry "MY_DELTA"\ndata "Damage" "1"\n',
        "utf-8",
    )
    (public / "RootTemplates").mkdir()
    template_key = "0badf00d-0000-4000-8000-000000000042"
    from dos2forge.parsers.lsx import LsxDocument, LsxNode

    node = _template_node(template_key, "MY_Item", "h1g1g1g1g1", "MY_DELTA")
    template_root = LsxNode(id="Templates")
    template_root.children.append(node)
    document = LsxDocument()
    document.regions["Templates"] = template_root
    (public / "RootTemplates" / "MyItem.lsx").write_text(write_lsx(document), "utf-8")

    (_lua_dir(result) / "Server" / "Data.lua").write_text(
        f'local mine = "{template_key}"\n'
        'ApplyStatus(mine, "MY_STATUS")\n'
        'ApplyStatus(mine, "MY_DELTA")\n'  # typeless delta: benefit of the doubt
        'Ext.GetStat("MY_STATUS")\n',
        "utf-8",
    )
    with Game(data_dir=game_dir, use_cache=False) as game:
        assert lint_path(root, game) == []


def test_lint_path_rejects_non_mods(tmp_path):
    with pytest.raises(ValueError, match="no mod found"):
        lint_path(tmp_path)


def test_format_issues_and_cli_exit_codes(project, capsys):
    root, result = project
    assert main(["lint", str(root), "--no-game"]) == 0
    assert "0 error(s), 0 warning(s)" in capsys.readouterr().out

    bootstrap = _lua_dir(result) / "BootstrapServer.lua"
    bootstrap.write_text(
        bootstrap.read_text("utf-8") + 'Ext.Require("Server/Nope.lua")\n', "utf-8"
    )
    assert main(["lint", str(root), "--no-game"]) == 1
    out = capsys.readouterr().out
    assert "error: Ext.Require target" in out
    assert "[require-missing]" in out
    assert "1 error(s), 0 warning(s)" in out
    # Paths print relative to the linted root, with the line number.
    assert f"Mods/{result.folder}/Story/RawFiles/Lua/BootstrapServer.lua:5:" in out
