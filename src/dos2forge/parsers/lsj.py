"""Parser for Larian LSJ resources (JSON-serialized node trees).

The third serialization of the same node tree as LSX (XML) and LSF
(binary); Larian games ship editor-side data in
this form.  Structure (per LSLib's LSJResourceConverter)::

    {
      "save": {
        "header":  { "version": "4.0.9.330", ... },
        "regions": {
          "dialog": {                       <- region name = root node id
            "UUID":  { "type": "FixedString", "value": "..." },
            "nodes": [ { ...child node... }, ... ]
          }
        }
      }
    }

Node keys are either attribute objects (with ``type`` as a type name or
numeric id, plus ``value`` and/or ``handle``/``version`` for translated
strings) or arrays of child nodes whose node id is the key.  Parses into
the same :class:`~dos2forge.parsers.lsx.LsxDocument` as the other two
formats.
"""

from __future__ import annotations

import json

from .types import TYPE_NAMES
from .lsx import LsxAttribute, LsxDocument, LsxNode


class LsjError(ValueError):
    pass


def is_lsj(data: bytes) -> bool:
    # A UTF-8 BOM is common on retail .lsj (parse_lsj itself decodes
    # with utf-8-sig); bytes.lstrip() does not remove it, which used to
    # misroute BOM'd files to the XML parser.
    i = 3 if data.startswith(b"\xef\xbb\xbf") else 0
    while i < len(data) and data[i] in b" \t\r\n":
        i += 1
    return data[i : i + 1] == b"{"


def parse_lsj(data: bytes | str) -> LsxDocument:
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
    try:
        root = json.loads(data)
    except json.JSONDecodeError as exc:
        raise LsjError(f"malformed LSJ document: {exc}") from exc
    if not isinstance(root, dict) or "save" not in root:
        raise LsjError("no 'save' object — not an LSJ resource")
    regions = root["save"].get("regions", {})
    if not isinstance(regions, dict):
        raise LsjError("'regions' is not an object")
    document = LsxDocument()
    for region_name, node_obj in regions.items():
        if isinstance(node_obj, dict):
            document.regions[region_name] = _parse_node(region_name, node_obj)
    return document


def _parse_node(name: str, obj: dict) -> LsxNode:
    node = LsxNode(id=name)
    for key, value in obj.items():
        if isinstance(value, dict):
            node.attributes[key] = _parse_attribute(key, value)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, dict):
                    node.children.append(_parse_node(key, child))
    return node


def _parse_attribute(name: str, obj: dict) -> LsxAttribute:
    type_name = _type_name(obj.get("type"))
    handle = obj.get("handle")
    version = obj.get("version")
    value = obj.get("value")
    return LsxAttribute(
        id=name,
        type=type_name,
        value=_format_value(value) if value is not None else None,
        handle=str(handle) if handle is not None else None,
        version=int(version) if version is not None else None,
    )


def _type_name(raw) -> str:
    if raw is None:
        return "None"
    if isinstance(raw, int):
        return TYPE_NAMES.get(raw, f"unknown_{raw}")
    text = str(raw)
    if text.isdigit():
        return TYPE_NAMES.get(int(text), f"unknown_{text}")
    return text


def _format_value(value) -> str:
    """Render a JSON value the way the LSX form would spell it."""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, list):
        return " ".join(_format_value(part) for part in value)
    if isinstance(value, float):
        return repr(value)
    return str(value)
