"""Scaffold a Script Extender (ositools) mod project.

Generates the file layout the game and ositools actually load, verified
against Norbyte's ositools documentation and shipping SE mods:

    Mods/<Name_UUID>/meta.lsx                          module metadata
    Mods/<Name_UUID>/OsiToolsConfig.json               SE activation config
    Mods/<Name_UUID>/Story/RawFiles/Lua/BootstrapServer.lua
    Mods/<Name_UUID>/Story/RawFiles/Lua/BootstrapClient.lua
    Mods/<Name_UUID>/Story/RawFiles/Lua/Server/Main.lua
    Mods/<Name_UUID>/Story/RawFiles/Lua/Client/Main.lua

``BootstrapServer.lua``/``BootstrapClient.lua`` are the only files SE
loads by itself; everything else is pulled in with ``Ext.Require``,
whose paths resolve relative to ``Story/RawFiles/Lua/``.

The meta.lsx is built through :mod:`dos2forge.parsers.lsx`, so it round
trips through the same reader the rest of the toolkit uses.
"""

from __future__ import annotations

import json
import re
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path

from .parsers.lsx import LsxAttribute, LsxDocument, LsxNode, write_lsx

#: FeatureFlags ositools understands for DOS2 (Docs/LuaAPIDocs.md).  An
#: unknown flag is silently ignored by SE, so a typo like "LUA" would
#: just leave the mod's scripting dead — validate up front instead.
FEATURE_FLAGS = frozenset(
    {
        "Lua",
        "OsirisExtensions",
        "Preprocessor",
        "DisableFolding",
        "CustomStats",
        "CustomStatsPane",
    }
)

#: v58 is what current SE mods (LeaderLib among them) declare.
DEFAULT_EXTENSION_VERSION = 58

_CONFIG_NAME = "OsiToolsConfig.json"


def pack_version32(major: int, minor: int = 0, revision: int = 0, build: int = 0) -> int:
    """DOS2's packed int32 module version (major.minor.revision.build)."""
    limits = ((major, 15), (minor, 15), (revision, 255), (build, 65535))
    for value, maximum in limits:
        if not 0 <= value <= maximum:
            raise ValueError(
                f"version {major}.{minor}.{revision}.{build} does not fit the "
                f"4/4/8/16-bit packing (component {value} > {maximum})"
            )
    return (major << 28) | (minor << 24) | (revision << 16) | build


def unpack_version32(packed: int) -> tuple[int, int, int, int]:
    return (packed >> 28) & 0xF, (packed >> 24) & 0xF, (packed >> 16) & 0xFF, packed & 0xFFFF


@dataclass(frozen=True)
class ScaffoldResult:
    mod_dir: Path
    folder: str
    uuid: str
    files: tuple[Path, ...]


def scaffold_mod(
    root: str | Path,
    name: str,
    *,
    author: str = "",
    description: str = "",
    mod_uuid: str | None = None,
    mod_table: str | None = None,
    feature_flags: tuple[str, ...] = ("Lua", "OsirisExtensions"),
    extension_version: int = DEFAULT_EXTENSION_VERSION,
    version: tuple[int, int, int, int] = (1, 0, 0, 0),
) -> ScaffoldResult:
    """Create a new SE mod skeleton under ``root`` and return what was made."""
    safe = re.sub(r"[^0-9A-Za-z_]+", "", name)
    if not safe:
        raise ValueError(f"mod name {name!r} has no usable identifier characters")
    if mod_uuid is None:
        mod_uuid = str(_uuid.uuid4())
    else:
        try:
            mod_uuid = str(_uuid.UUID(mod_uuid))
        except ValueError:
            raise ValueError(f"{mod_uuid!r} is not a valid UUID") from None
    unknown = sorted(set(feature_flags) - FEATURE_FLAGS)
    if unknown:
        raise ValueError(
            f"unknown FeatureFlags {unknown} — ositools understands "
            f"{sorted(FEATURE_FLAGS)}"
        )
    if mod_table is None:
        mod_table = safe
    packed = pack_version32(*version)

    folder = f"{safe}_{mod_uuid}"
    mod_dir = Path(root) / "Mods" / folder
    if mod_dir.exists():
        raise ValueError(f"{mod_dir} already exists")

    lua_dir = mod_dir / "Story" / "RawFiles" / "Lua"
    (lua_dir / "Server").mkdir(parents=True)
    (lua_dir / "Client").mkdir(parents=True)

    files: list[Path] = []

    def write(path: Path, text: str) -> None:
        path.write_text(text, "utf-8")
        files.append(path)

    write(
        mod_dir / "meta.lsx",
        write_lsx(_meta_document(name, folder, mod_uuid, author, description, packed)),
    )
    config = {
        "RequiredExtensionVersion": extension_version,
        "ModTable": mod_table,
        "FeatureFlags": list(feature_flags),
    }
    write(mod_dir / _CONFIG_NAME, json.dumps(config, indent=4) + "\n")

    for side in ("Server", "Client"):
        write(
            lua_dir / f"Bootstrap{side}.lua",
            f"-- {name}: {side.lower()}-side entry point.  Script Extender loads\n"
            f"-- only Bootstrap{side}.lua by itself; require everything else from\n"
            f"-- here (paths are relative to Story/RawFiles/Lua/).\n"
            f'Ext.Require("{side}/Main.lua")\n',
        )
        write(
            lua_dir / side / "Main.lua",
            "local function onSessionLoaded()\n"
            f'    Ext.Print("[{mod_table}] {side.lower()} session loaded")\n'
            "end\n"
            "\n"
            'Ext.RegisterListener("SessionLoaded", onSessionLoaded)\n',
        )

    return ScaffoldResult(
        mod_dir=mod_dir, folder=folder, uuid=mod_uuid, files=tuple(files)
    )


def _meta_document(
    name: str, folder: str, mod_uuid: str, author: str, description: str, packed: int
) -> LsxDocument:
    """meta.lsx as shipped SE mods structure it: a Config region whose root
    holds Dependencies and a ModuleInfo with PublishVersion/Scripts/TargetModes."""
    info = LsxNode(id="ModuleInfo")

    def attr(attr_id: str, type_name: str, value: str) -> None:
        info.attributes[attr_id] = LsxAttribute(id=attr_id, type=type_name, value=value)

    attr("Author", "LSWString", author)
    attr("CharacterCreationLevelName", "FixedString", "")
    attr("Description", "LSWString", description)
    attr("Folder", "LSWString", folder)
    attr("GMTemplate", "FixedString", "")
    attr("LobbyLevelName", "FixedString", "")
    attr("MD5", "LSString", "")
    attr("MenuLevelName", "FixedString", "")
    attr("Name", "FixedString", name)
    attr("NumPlayers", "uint8", "4")
    attr("PhotoBooth", "FixedString", "")
    attr("StartupLevelName", "FixedString", "")
    attr("Tags", "LSWString", "")
    attr("Type", "FixedString", "Add-on")
    attr("UUID", "FixedString", mod_uuid)
    attr("Version", "int32", str(packed))

    publish = LsxNode(id="PublishVersion")
    publish.attributes["Version"] = LsxAttribute(
        id="Version", type="int32", value=str(packed)
    )
    target = LsxNode(id="Target")
    target.attributes["Object"] = LsxAttribute(
        id="Object", type="FixedString", value="Story"
    )
    target_modes = LsxNode(id="TargetModes")
    target_modes.children.append(target)
    info.children.extend([publish, LsxNode(id="Scripts"), target_modes])

    root = LsxNode(id="root")
    root.children.extend([LsxNode(id="Dependencies"), info])
    document = LsxDocument()
    document.regions["Config"] = root
    return document
