"""Parsers for DOS2 data formats."""

from .stats import (
    StatsCollection,
    StatsDocument,
    StatsEntry,
    StatsParseError,
    StatsWriteError,
    parse_stats,
    parse_stats_document,
    write_stats,
    write_stats_document,
)
from .localization import (
    Localization,
    LocalizationEntry,
    LocalizationError,
    parse_localization,
    write_localization,
)
from .lsx import (
    LsxAttribute,
    LsxDocument,
    LsxError,
    LsxNode,
    load_lsx,
    parse_lsx,
    write_lsx,
)
from .lsf import (
    LsfError,
    is_lsf,
    load_lsf,
    pack_version32,
    parse_lsf,
    unpack_version32,
    unpack_version64,
    write_lsf,
)
from .lsj import LsjError, is_lsj, parse_lsj
from .resource import load_resource, parse_resource
from .types import TYPE_IDS, TYPE_NAMES, type_name

__all__ = [
    "StatsCollection",
    "StatsDocument",
    "StatsEntry",
    "StatsParseError",
    "StatsWriteError",
    "parse_stats",
    "parse_stats_document",
    "write_stats",
    "write_stats_document",
    "Localization",
    "LocalizationEntry",
    "LocalizationError",
    "parse_localization",
    "write_localization",
    "LsxAttribute",
    "LsxDocument",
    "LsxError",
    "LsxNode",
    "load_lsx",
    "parse_lsx",
    "write_lsx",
    "LsfError",
    "is_lsf",
    "load_lsf",
    "pack_version32",
    "parse_lsf",
    "unpack_version32",
    "unpack_version64",
    "write_lsf",
    "LsjError",
    "is_lsj",
    "parse_lsj",
    "load_resource",
    "parse_resource",
    "TYPE_IDS",
    "TYPE_NAMES",
    "type_name",
]
