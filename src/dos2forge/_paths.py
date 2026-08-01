"""Internal helpers for writing files beneath a caller-selected directory."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


class UnsafePathError(ValueError):
    """Raised when untrusted input would select a path outside its root."""


#: Windows reserved device names (case-insensitive, extension ignored):
#: a file called ``CON`` or ``NUL`` names a device, not a file on disk.
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def safe_output_path(root: str | Path, untrusted_name: str) -> Path:
    """Return an absolute output path guaranteed to remain beneath ``root``.

    Archive paths use forward slashes, but accepting data produced on other
    platforms means both POSIX and Windows absolute/drive syntax must be
    rejected explicitly.  Resolving the final path also catches an existing
    symlink inside ``root`` that points elsewhere.
    """
    if not untrusted_name or "\x00" in untrusted_name:
        raise UnsafePathError("path is empty or contains a null byte")

    normalized = untrusted_name.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise UnsafePathError("absolute and drive-qualified paths are not allowed")
    if any(part == ".." for part in posix_path.parts):
        raise UnsafePathError("parent-directory components are not allowed")
    if not posix_path.parts or posix_path == PurePosixPath("."):
        raise UnsafePathError("path does not name a file")

    # Windows-specific hazards that survive the POSIX checks above.  A
    # legitimate archive path never trips these, but a hostile one could
    # otherwise open an NTFS alternate data stream or a device.
    for part in posix_path.parts:
        if ":" in part:
            raise UnsafePathError(
                f"colon in path component {part!r} (NTFS alternate data stream)"
            )
        if part.split(".", 1)[0].lower() in _WINDOWS_RESERVED:
            raise UnsafePathError(f"reserved device name in component {part!r}")

    resolved_root = Path(root).resolve()
    target = (resolved_root / Path(*posix_path.parts)).resolve()
    if not target.is_relative_to(resolved_root):
        raise UnsafePathError("resolved path escapes the output directory")
    return target
