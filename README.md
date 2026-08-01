# DOS2 Forge

DOS2 Forge is an open-source toolkit for reading Divinity: Original Sin 2
game data — finding the UUIDs, root templates, stats entries, and
localization handles a mod needs — and for building new content
programmatically.

> [!IMPORTANT]
> **DOS2 Forge is a development tool, not an in-game mod.** Mod authors run
> Forge from a terminal or Python; players install the `.pak` files those
> authors create.

Forge reads the original data from **your own installed copy** of the game
(Definitive Edition or classic) and turns it into parsed, typed structures.
It does not depend on a community wiki, does not modify the game
installation, and ships no Larian assets.

It is the sibling project of
[BG3 Forge](https://github.com/crazyace/bg3-forge), sharing its
architecture and much of its format-handling code, adapted for the older
file format generations DOS2 uses.

## Why?

UUIDs, root template names, and stats keys for DOS2 are hard to find
anywhere online. They are all in the game's `.pak` archives — this tool
gets them out:

```console
$ pip install dos2forge
$ dos2forge doctor                        # find the install, inventory the paks
$ dos2forge search "*/RootTemplates/*"    # where do the templates live?
$ dos2forge unpack -p "*/Stats/*" -p "*/RootTemplates/*" -p "*/Localization/*"
$ dos2forge convert extracted/Public/Shared/RootTemplates/Containers.lsf containers.lsx
```

## Status

Early scaffold. What works today:

| Area | Support |
| --- | --- |
| `.pak` archives | Read v10 (classic) and v13 (Definitive Edition), including multi-part and solid archives; write v10/v13; BG3-era v15/16/18 readable too |
| LSF binary resources | Parse v1–v7 (DOS2 DE ships v3); write DOS2-native v3 |
| LSX XML resources | Parse/write, with DOS2's numeric type ids and BG3's type names |
| LSJ JSON resources | Parse |
| Stats `.txt` | Parse/write, `using` inheritance resolved across files |
| Localization | DOS2 XML `contentList` parsing, handle → text lookup |
| Install discovery | Steam/GOG, `DOS2_PATH` env var, DefEd + classic data dirs |

On the roadmap (following bg3-forge's architecture): a typed `Game` object
model (items, skills, statuses, root templates with resolved names and
UUIDs), `lookup`, dataset exporters (JSON/CSV/SQLite), and mod authoring
helpers.

## Quick start

```console
pip install dos2forge          # pure Python, no required dependencies
pip install "dos2forge[all]"   # + native LZ4 (much faster full unpacks)
```

```console
$ dos2forge list "path/to/Divinity Original Sin 2/DefEd/Data/Shared.pak"
$ dos2forge cat Shared.pak "Public/Shared/Stats/Generated/Data/Weapon.txt"
$ dos2forge unpack Shared.pak -o extracted
$ dos2forge convert extracted/.../GlobalSwitches.lsf switches.lsx
```

`unpack` keeps a manifest of extracted file checksums, so re-running after
a game patch only rewrites files whose archived bytes actually changed.

## Python API

```python
from dos2forge.pak import PakReader
from dos2forge.parsers import parse_resource, StatsCollection, Localization

with PakReader("Shared.pak") as pak:
    doc = parse_resource(pak.read("Public/Shared/RootTemplates/Containers.lsf"))
    for node in doc.find_all("GameObjects"):
        print(node.get("MapKey"), node.get("Name"))

stats = StatsCollection()
stats.load_text(weapon_txt)
print(stats.resolved("WPN_Sword_1H"))   # inheritance applied

loc = Localization()
loc.load_bytes(english_xml)
print(loc.resolve("h1234abcdg5678g..."))
```

All parsers accept raw bytes, so they work equally on files inside a pak,
an extracted tree, or your own mod sources.

## Format notes

Binary format support follows Norbyte's
[LSLib](https://github.com/Norbyte/lslib), the reference implementation:

- **LSPK v13** (Definitive Edition) stores its header at the *end* of the
  archive; v10 (classic) at the start with an uncompressed file table.
- **Solid archives** (flag `0x04`) compress all file data into one LZ4
  frame; Forge decompresses the frame transparently.
- **LSF v3** (DE resources) uses compact node/attribute tables, a 32-bit
  engine version, and chunked LZ4 section compression. Translated strings
  carry their text inline (unlike BG3, which only stores a handle) — Forge
  preserves both the inline text and the handle.
- **LSX** in DOS2 uses numeric attribute type ids (`type="22"`); Forge
  maps them to the same type names BG3 uses so downstream code sees one
  vocabulary.

## Development

```console
git clone https://github.com/crazyace/dos2-forge
cd dos2-forge
pip install -e ".[all,dev]"
pytest
```

The test suite builds real LSPK/LSF fixtures in memory, so it runs fast
and **without a game install**. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. Not affiliated with Larian Studios. Divinity: Original Sin 2 is a
trademark of Larian Studios.
