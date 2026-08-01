"""Locate an installed copy of Divinity: Original Sin 2.

Checks the ``DOS2_PATH`` environment variable first, then well-known
Steam/GOG install locations for the current platform.  A directory counts
as an install if it contains a data folder with at least one ``.pak`` —
``DefEd/Data`` for the Definitive Edition, ``Data`` for classic.  When
both editions are present the Definitive Edition wins.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Steam and GOG have used both spellings for the install folder.
_GAME_DIRS = ("Divinity Original Sin 2", "Divinity - Original Sin 2")

#: Relative data directories, most-preferred first.
_DATA_SUBDIRS = (Path("DefEd") / "Data", Path("Data"))


def _candidate_paths() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []
    for game_dir in _GAME_DIRS:
        steam_suffix = Path("steamapps") / "common" / game_dir
        if sys.platform.startswith("win"):
            for drive in ("C:", "D:", "E:"):
                candidates += [
                    Path(drive + "\\Program Files (x86)\\Steam") / steam_suffix,
                    Path(drive + "\\Program Files\\Steam") / steam_suffix,
                    Path(drive + "\\SteamLibrary") / steam_suffix,
                    Path(drive + "\\GOG Games") / game_dir,
                    Path(drive + "\\Program Files (x86)\\GOG Galaxy\\Games") / game_dir,
                ]
        elif sys.platform == "darwin":
            candidates.append(
                home / "Library" / "Application Support" / "Steam" / steam_suffix
            )
        else:
            candidates += [
                home / ".steam" / "steam" / steam_suffix,
                home / ".local" / "share" / "Steam" / steam_suffix,
                home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share"
                / "Steam" / steam_suffix,
            ]
    return candidates


def find_data_dir(root: str | Path) -> Path | None:
    """The pak directory beneath an install root (DefEd preferred), or None."""
    root = Path(root)
    for subdir in _DATA_SUBDIRS:
        data = root / subdir
        if data.is_dir() and any(data.glob("*.pak")):
            return data
    return None


def is_game_dir(path: str | Path) -> bool:
    return find_data_dir(path) is not None


def find_game(path: str | Path | None = None) -> Path | None:
    """Return the game's install root, or None if it cannot be found."""
    if path is not None:
        path = Path(path)
        return path if is_game_dir(path) else None
    env = os.environ.get("DOS2_PATH")
    if env and is_game_dir(env):
        return Path(env)
    for candidate in _candidate_paths():
        if is_game_dir(candidate):
            return candidate
    return None
