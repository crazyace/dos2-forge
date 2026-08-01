# Changelog

## 0.1.0 (unreleased)

Initial scaffold, ported from [BG3 Forge](https://github.com/crazyace/bg3-forge)
and adapted for Divinity: Original Sin 2's older file format generations.

- LSPK `.pak` reading for v10 (DOS2 classic) and v13 (Definitive Edition)
  archives, including multi-part and solid (single LZ4 frame) archives;
  v15/16/18 (BG3-era) layouts remain readable for cross-checking.
- LSPK writing for v10 and v13 single-part archives (mod paks, test
  fixtures).
- LSF binary resource parsing for versions 1-7 — DOS2 DE ships v3 with
  compact node/attribute tables and chunked LZ4 sections — plus LSF
  writing in DOS2-native v3.
- LSX parsing/writing with DOS2's numeric attribute type ids
  (`type="22"`) as well as BG3-style type names.
- LSJ (JSON) resource parsing.
- Stats `.txt` parsing/writing with `using` inheritance resolution.
- Localization: DOS2 XML `contentList` parsing with handle lookup.
- Incremental pak extraction with a change-tracking manifest.
- Install discovery for Steam/GOG (Definitive Edition and classic data
  directories).
- CLI: `list`, `unpack`, `cat`, `search`, `convert`, `doctor`.
- `Game` index over all paks with patch layering (base archives first,
  `PatchN` numerically after), merging stats/templates/localization
  layers; `lookup` resolving names, MapKey UUIDs, and localization
  handles with cross-references; `templates` JSON export of the full
  root template index.
