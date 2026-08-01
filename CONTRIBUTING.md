# Contributing to DOS2 Forge

Thanks for your interest! DOS2 Forge aims to be the standard developer
library for Divinity: Original Sin 2 data, and contributions of every
size help.

## Setup

Python **3.10+** is required.

```console
git clone https://github.com/crazyace/dos2-forge
cd dos2-forge
pip install -e ".[all,dev]"
pytest
```

The test suite builds real LSPK/LSF fixtures in memory, so it runs in
well under a second **without a game install**. Please keep it that way:
new parser tests should construct fixtures with the library's own writers
(`PakWriter`, `write_lsf`, `write_localization`, …) rather than depending
on game files.

If you do have the game installed, `DOS2_PATH=/path/to/game pytest` runs
the same suite — integration tests against a real install are welcome as
long as they skip cleanly when the game is absent
(`pytest.mark.skipif(find_game() is None, ...)`).

## Design principles

These mirror [BG3 Forge](https://github.com/crazyace/bg3-forge), the
sibling project this codebase was ported from:

1. **Library first, CLI second.** Features live in importable modules;
   `dos2forge.cli` is a thin argparse layer. If a CLI subcommand needs
   more than a few lines of glue, the logic belongs in the library.
2. **Zero required dependencies for the core.** Native speedups (lz4)
   are optional extras with graceful fallbacks or clear error messages.
3. **Deterministic output.** Identical inputs must produce byte-identical
   exports — no timestamps, no dict-ordering surprises.
4. **Follow the reference.** Binary format code (`pak`, `lsf`) follows
   Norbyte's [LSLib](https://github.com/Norbyte/lslib) struct layouts;
   cite the relevant structure in comments/docstrings when implementing a
   new one. DOS2 uses *older* format generations than BG3 (LSPK v10/v13,
   LSF v1–v3, numeric LSX type ids) — when in doubt, check LSLib's
   DOS2-era releases (v1.15.x), not just current master.
5. **Pay for complexity only when the data demands it.** Straightforward,
   deterministic implementations first; optimizations need a measurement
   from real game data.

## Porting from bg3-forge

Much of this codebase is a port. When bringing over another module,
adapt — don't just copy: DOS2's stats use different entry types and
fields, localization is XML rather than `.loca`, and translated strings
carry inline text. Keep the public API shape aligned with bg3-forge where
the concepts match, so users of one tool feel at home in the other.
