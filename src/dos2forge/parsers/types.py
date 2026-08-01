"""Larian attribute type ids shared by the LSX/LSF/LSJ serializations.

Ids follow LSLib's ``AttributeType`` enum.  DOS2's LSX files reference
attributes by numeric id (``type="22"``); BG3-era files use the names.
Both spellings resolve to the same names here so downstream code sees one
vocabulary.
"""

from __future__ import annotations

TYPE_NAMES = {
    0: "None",
    1: "uint8",
    2: "int16",
    3: "uint16",
    4: "int32",
    5: "uint32",
    6: "float",
    7: "double",
    8: "ivec2",
    9: "ivec3",
    10: "ivec4",
    11: "fvec2",
    12: "fvec3",
    13: "fvec4",
    14: "mat2x2",
    15: "mat3x3",
    16: "mat3x4",
    17: "mat4x3",
    18: "mat4x4",
    19: "bool",
    20: "string",
    21: "path",
    22: "FixedString",
    23: "LSString",
    24: "uint64",
    25: "ScratchBuffer",
    26: "old_int64",
    27: "int8",
    28: "TranslatedString",
    29: "WString",
    30: "LSWString",
    31: "guid",
    32: "int64",
    33: "TranslatedFSString",
}
TYPE_IDS = {name: type_id for type_id, name in TYPE_NAMES.items()}


def type_name(raw: str | int | None) -> str:
    """Resolve a numeric id or type name to the canonical type name."""
    if raw is None:
        return "None"
    if isinstance(raw, int):
        return TYPE_NAMES.get(raw, f"unknown_{raw}")
    text = str(raw)
    if text.isdigit():
        return TYPE_NAMES.get(int(text), f"unknown_{text}")
    return text
