"""Format-agnostic loading of Larian node-tree resources.

DOS2 ships the same logical documents as XML (``.lsx``), binary
(``.lsf``), or JSON (``.lsj``); :func:`parse_resource` sniffs the
content and dispatches, so callers never need to care which one they
got.
"""

from __future__ import annotations

from pathlib import Path

from .lsf import is_lsf, parse_lsf
from .lsj import is_lsj, parse_lsj
from .lsx import LsxDocument, parse_lsx


def parse_resource(data: bytes) -> LsxDocument:
    if is_lsf(data):
        return parse_lsf(data)
    if is_lsj(data):
        return parse_lsj(data)
    return parse_lsx(data)


def load_resource(path: str | Path) -> LsxDocument:
    return parse_resource(Path(path).read_bytes())
