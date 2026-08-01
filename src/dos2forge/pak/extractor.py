"""Incremental extraction of .pak archives to a directory tree."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .._paths import UnsafePathError, safe_output_path
from .format import PakEntry
from .reader import PakError, PakReader

MANIFEST_NAME = ".dos2forge-manifest.json"


@dataclass
class ExtractionResult:
    extracted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.extracted) + len(self.skipped)


class Extractor:
    """Extract pak contents incrementally.

    A manifest recording each extracted file's source checksum is kept in
    the output directory, so re-running extraction after a game patch only
    rewrites files whose archived bytes actually changed.
    """

    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self._manifest_path = self.output_dir / MANIFEST_NAME
        self._manifest = self._load_manifest()

    def extract(
        self,
        pak: PakReader | str | Path,
        patterns: Sequence[str] | None = None,
        force: bool = False,
        progress: Callable[[str, bool], None] | None = None,
    ) -> ExtractionResult:
        """Extract matching entries from ``pak``.

        ``patterns`` are shell-style globs matched case-insensitively
        against the archived path (e.g. ``"*/Stats/*"``); ``None`` means
        everything.  Unchanged files are skipped unless ``force`` is set.
        """
        owns_reader = not isinstance(pak, PakReader)
        reader = PakReader(pak) if owns_reader else pak
        result = ExtractionResult()
        try:
            for entry in _select(reader, patterns):
                try:
                    target = safe_output_path(self.output_dir, entry.name)
                except UnsafePathError as exc:
                    raise PakError(
                        f"unsafe archive entry path {entry.name!r}: {exc}"
                    ) from None
                data = reader.read(entry)
                digest = hashlib.sha256(data).hexdigest()
                if (
                    not force
                    and self._manifest.get(entry.name) == digest
                    and target.exists()
                ):
                    result.skipped.append(entry.name)
                    if progress:
                        progress(entry.name, False)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                self._manifest[entry.name] = digest
                result.extracted.append(entry.name)
                if progress:
                    progress(entry.name, True)
        finally:
            if owns_reader:
                reader.close()
            # Persist progress even when the loop aborts mid-pak (disk
            # full, a truncated entry, KeyboardInterrupt): the manifest
            # records only files already written, so a re-run resumes
            # instead of re-extracting everything.  Best-effort so a save
            # failure never masks the original error.
            try:
                self._save_manifest()
            except OSError:
                if sys.exc_info()[0] is None:
                    raise
        return result

    # -- manifest ------------------------------------------------------------

    def _load_manifest(self) -> dict[str, str]:
        if self._manifest_path.exists():
            try:
                return json.loads(self._manifest_path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
        return {}

    def _save_manifest(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text(
            json.dumps(self._manifest, indent=1, sort_keys=True), "utf-8"
        )


def _select(reader: PakReader, patterns: Sequence[str] | None) -> Iterable[PakEntry]:
    if not patterns:
        yield from reader
        return
    lowered = [p.lower() for p in patterns]
    for entry in reader:
        name = entry.name.lower()
        if any(fnmatch.fnmatch(name, p) for p in lowered):
            yield entry
