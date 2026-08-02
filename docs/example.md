# Worked example: an SE mod that hands you a shovel

The full loop — find an item, scaffold a Script Extender mod, generate
Lua constants, write the script, lint it — using a real item. Commands
are shown for PowerShell; everything works the same on other shells.

> Terminology: **your terminal** runs `dos2forge` commands. **The SE
> console** (the `S >>` prompt attached to the running game) runs Lua.
> Lua pasted into PowerShell produces `ParserError`; `dos2forge` typed
> into the SE console produces nothing useful either.

## 1. Find the item

```console
$ dos2forge lookup shovel
template 41486dd2-3fd5-464e-870e-844120cf0517
  name: TOOL_Shovel_A
  display name: Shovel
  stats: TOOL_Shovel_A
  ...
```

The first run parses your whole install (minutes); afterwards it's
cached and near-instant.

## 2. Scaffold the mod

```console
$ dos2forge new "Vampiric Blades" --author You -o D:\Projects\VampiricBlades
$ cd D:\Projects\VampiricBlades
$ dir Mods    # note your real folder name — the UUID part is random
```

This creates `Mods\VampiricBlades_<uuid>\` with `meta.lsx`,
`OsiToolsConfig.json`, and the `BootstrapServer.lua` /
`BootstrapClient.lua` files Script Extender loads.

## 3. Generate the Lua constants

Use the folder name `dir Mods` printed (tab completion helps):

```console
$ dos2forge lua -o "Mods\VampiricBlades_<uuid>\Story\RawFiles\Lua\Generated"
```

`Generated\Templates.lua` now contains, among thousands of others:

```lua
["TOOL_Shovel_A"] = "41486dd2-3fd5-464e-870e-844120cf0517",
```

## 4. Write the script

`Ext.Require` only works during startup, so the bootstrap is the place
for it — not the SE console. Append to
`Story\RawFiles\Lua\BootstrapServer.lua`:

```lua
Templates = Ext.Require("Generated/Templates.lua")  -- deliberately global
```

Replace `Story\RawFiles\Lua\Server\Main.lua` with:

```lua
local function onSessionLoaded()
    Ext.Print("[VampiricBlades] server ready")
end

Ext.RegisterListener("SessionLoaded", onSessionLoaded)

-- SessionLoaded is too early for Osiris calls; the level is up at
-- GameStarted.
Ext.RegisterOsirisListener("GameStarted", 2, "after", function(level, isEditorMode)
    local host = Osi.CharacterGetHostCharacter()
    local shovel = Templates["TOOL_Shovel_A"]
    if Osi.ItemTemplateIsInCharacterInventory(host, shovel) == 0 then
        Osi.ItemTemplateAddTo(shovel, host, 1, 1)  -- 1 item, with notification
    end
end)
```

A typo like `Templates["TOOL_Shovle_A"]` is a visible `nil` in game
instead of a call that silently does nothing. Prefer hardcoding?
Osiris accepts both the bare UUID and the editor-style combined form
`"TOOL_Shovel_A_41486dd2-3fd5-464e-870e-844120cf0517"` — it resolves
the trailing UUID and ignores the prefix. The linter checks either
form.

## 5. Lint before running

From the project root (the directory containing `Mods\`):

```console
$ dos2forge lint .
0 error(s), 0 warning(s)
```

Structural problems — FeatureFlag typos in `OsiToolsConfig.json`,
missing bootstraps, `Ext.Require` targets that don't exist or differ in
case — are errors (exit 1). With a game install found, every UUID,
skill id, status id, and `Ext.GetStat` name is also checked against
game data plus your own `Public\` content; unknowns are warnings.

## 6. Run it

Copy (or junction) `Mods\VampiricBlades_<uuid>\` into the game's
`Data\Mods\` directory, enable the mod in game, and load a save. The
host character gets a shovel once. To poke at it live, use the SE
console:

```
S >> reset                       -- reload Lua after editing files
S >> Osi.ItemTemplateAddTo(Templates["TOOL_Shovel_A"], Osi.CharacterGetHostCharacter(), 1, 1)
```

Console gotchas, learned the hard way:

- each console line is its own chunk — `local` variables don't survive
  to the next line; use globals when experimenting;
- `Ext.Require` fails in the console (`ModuleUUID` is nil there) — it
  belongs in the bootstrap;
- if your ositools version sandboxes mod globals, your table lives at
  `Mods.VampiricBlades.Templates` instead of plain `Templates`.

This walkthrough is pinned by `tests/test_example.py`, which runs the
same steps against a synthetic game archive containing the real
shovel data.
