"""Best-effort disk cache for parsed game indexes.

Parsing every root template and level instance across a retail install
takes minutes; the resulting indexes are plain records that gzip to a
few megabytes.  Cache entries are keyed by a fingerprint of every pak's
name/size/mtime (plus language and cache format), so any game patch or
mod change invalidates naturally.  All cache I/O is best-effort: a
missing, corrupt, or unwritable cache only means re-parsing.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

#: Bump when the cached record shapes change.
CACHE_FORMAT = 1


def cache_root() -> Path:
    env = os.environ.get("DOS2FORGE_CACHE")
    if env:
        return Path(env)
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "dos2forge" / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "dos2forge"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "dos2forge"


def data_fingerprint(data_dir: str | Path, language: str) -> str:
    """Key covering every pak the Game would read."""
    data_dir = Path(data_dir)
    digest = hashlib.sha256(f"v{CACHE_FORMAT}|{language.lower()}".encode())
    paks = sorted(data_dir.glob("*.pak"))
    localization = data_dir / "Localization"
    if localization.is_dir():
        paks += sorted(localization.rglob("*.pak"))
    for pak in paks:
        try:
            stat = pak.stat()
        except OSError:
            continue
        digest.update(f"{pak.name}|{stat.st_size}|{stat.st_mtime_ns}|".encode())
    return digest.hexdigest()[:24]


def load(key: str, dataset: str):
    """The cached payload, or None on any miss/failure."""
    path = cache_root() / key / f"{dataset}.json.gz"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, EOFError):
        return None


def save(key: str, dataset: str, payload) -> None:
    directory = cache_root() / key
    try:
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / f"{dataset}.json.gz.tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"), ensure_ascii=False)
        tmp.replace(directory / f"{dataset}.json.gz")
        _prune(keep=key)
    except OSError:
        pass  # best-effort


def _prune(keep: str, limit: int = 3) -> None:
    """Drop all but the newest cache directories (stale game versions)."""
    root = cache_root()
    try:
        entries = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return
    entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in entries[limit:]:
        if stale.name != keep:
            shutil.rmtree(stale, ignore_errors=True)


def clear() -> int:
    """Delete the whole cache; returns how many entry directories went."""
    root = cache_root()
    try:
        entries = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return 0
    for entry in entries:
        shutil.rmtree(entry, ignore_errors=True)
    return len(entries)


def size_bytes() -> int:
    root = cache_root()
    total = 0
    if root.is_dir():
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
    return total
