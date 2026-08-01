"""Diagnose the DOS2 installation and dos2forge environment."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .locate import find_data_dir, find_game
from .pak import lz4compat
from .pak.reader import PakError, PakReader, file_is_lspk


@dataclass
class PakInfo:
    name: str
    version: int
    entries: int
    solid: bool
    error: str | None = None


@dataclass
class DoctorReport:
    game_path: Path | None
    data_dir: Path | None
    native_lz4: bool
    paks: list[PakInfo] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.data_dir is not None and not any(p.error for p in self.paks)


def run_doctor(
    path: str | Path | None = None, data_dir: str | Path | None = None
) -> DoctorReport:
    game_path = None if data_dir else find_game(path)
    resolved_data = Path(data_dir) if data_dir else (
        find_data_dir(game_path) if game_path else None
    )
    report = DoctorReport(
        game_path=game_path,
        data_dir=resolved_data,
        native_lz4=lz4compat.HAVE_NATIVE_LZ4,
    )
    if resolved_data is None:
        return report
    for pak_path in sorted(resolved_data.glob("*.pak")):
        if not file_is_lspk(pak_path):
            continue  # secondary parts (Textures_1.pak) carry no header
        try:
            with PakReader(pak_path) as pak:
                report.paks.append(
                    PakInfo(
                        name=pak_path.name,
                        version=pak.header.version,
                        entries=len(pak),
                        solid=pak.header.solid,
                    )
                )
        except PakError as exc:
            report.paks.append(
                PakInfo(name=pak_path.name, version=0, entries=0,
                        solid=False, error=str(exc))
            )
    return report


def format_report(report: DoctorReport) -> str:
    lines: list[str] = []
    if report.game_path:
        lines.append(f"install:  {report.game_path}")
    if report.data_dir:
        lines.append(f"data dir: {report.data_dir}")
    else:
        lines.append(
            "data dir: NOT FOUND — set DOS2_PATH, or pass --game-path / --data-dir"
        )
    lines.append(
        "lz4:      native" if report.native_lz4
        else "lz4:      pure-Python fallback (pip install 'dos2forge[lz4]' for speed)"
    )
    for pak in report.paks:
        if pak.error:
            lines.append(f"  {pak.name}: ERROR {pak.error}")
        else:
            solid = ", solid" if pak.solid else ""
            lines.append(
                f"  {pak.name}: v{pak.version}, {pak.entries} files{solid}"
            )
    if report.data_dir and not report.paks:
        lines.append("  (no readable .pak archives found)")
    return "\n".join(lines)
