"""Parser for Larian LSX documents (XML-serialized node trees).

LSX is the XML form of Larian's resource format, used for RootTemplates,
meta.lsx, level data, and much more::

    <save>
      <header version="3.6.4.0" ... />
      <version major="3" minor="6" revision="4" build="0" />
      <region id="Templates">
        <node id="Templates">
          <children>
            <node id="GameObjects">
              <attribute id="MapKey" type="22" value="..." />
              <children>...</children>
            </node>
          </children>
        </node>
      </region>
    </save>

DOS2 references attribute types by numeric id (``type="22"``); BG3-era
files use type names (``type="FixedString"``).  Both parse to the same
canonical type names.  The binary sibling format (LSF) is handled by
:mod:`dos2forge.parsers.lsf`, which produces the same
:class:`LsxDocument` structure; :func:`dos2forge.parsers.resource.parse_resource`
sniffs the format and dispatches, so downstream code works with either
serialization.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .types import TYPE_IDS, type_name


@dataclass
class LsxAttribute:
    id: str
    type: str
    value: str | None
    handle: str | None = None  # TranslatedString handle
    version: int | None = None

    @property
    def text(self) -> str | None:
        """Best-effort display value (handle for translated strings)."""
        return self.value if self.value is not None else self.handle


@dataclass
class LsxNode:
    id: str
    attributes: dict[str, LsxAttribute] = field(default_factory=dict)
    children: list["LsxNode"] = field(default_factory=list)
    key: str | None = None  # key attribute name (BG3-era keyed nodes)

    def get(self, attribute_id: str, default: str | None = None) -> str | None:
        attr = self.attributes.get(attribute_id)
        if attr is None:
            return default
        return attr.text if attr.text is not None else default

    def find_all(self, node_id: str) -> Iterator["LsxNode"]:
        """Depth-first search for descendant nodes with the given id."""
        for child in self.children:
            if child.id == node_id:
                yield child
            yield from child.find_all(node_id)


@dataclass
class LsxDocument:
    regions: dict[str, LsxNode] = field(default_factory=dict)

    def region(self, region_id: str) -> LsxNode | None:
        return self.regions.get(region_id)

    def find_all(self, node_id: str) -> Iterator[LsxNode]:
        for root in self.regions.values():
            if root.id == node_id:
                yield root
            yield from root.find_all(node_id)


class LsxError(ValueError):
    pass


#: C0 control characters other than tab/newline/carriage return are not
#: representable in XML 1.0 at all — not even as character references —
#: but ElementTree writes them through verbatim, producing a document
#: that no conforming parser (the game's included, and our own
#: ``parse_lsx``) can read back.  Writing them must fail loudly.
_ILLEGAL_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _check_xml_writable(value: str, what: str) -> str:
    match = _ILLEGAL_XML_RE.search(value)
    if match:
        raise LsxError(
            f"{what} contains control character {match.group()!r}, "
            "which XML 1.0 cannot represent"
        )
    return value


def parse_lsx(source: str | bytes) -> LsxDocument:
    if isinstance(source, bytes):
        source = source.decode("utf-8-sig", errors="replace")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise LsxError(f"malformed LSX document: {exc}") from exc
    if root.tag != "save":
        raise LsxError(f"expected <save> root, found <{root.tag}>")
    document = LsxDocument()
    for region in root.findall("region"):
        region_id = region.get("id", "")
        node_el = region.find("node")
        if node_el is not None:
            document.regions[region_id] = _parse_node(node_el)
    return document


def load_lsx(path: str | Path) -> LsxDocument:
    return parse_lsx(Path(path).read_bytes())


def write_lsx(document: LsxDocument, numeric_types: bool = True) -> str:
    """Serialize a document back to LSX XML (regions in insertion order).

    ``numeric_types`` (the default) writes DOS2-native numeric type ids;
    pass False for BG3-style type names.  Types without a numeric id
    (``unknown_N``) always fall back to their name.
    """
    save = ET.Element("save")
    ET.SubElement(
        save, "version", major="3", minor="6", revision="4", build="0"
    )
    for region_id, root in document.regions.items():
        region_el = ET.SubElement(
            save, "region", id=_check_xml_writable(region_id, "region id")
        )
        _write_node(region_el, root, numeric_types)
    ET.indent(save)
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(
        save, encoding="unicode"
    ) + "\n"


def _type_spelling(name: str, numeric_types: bool) -> str:
    if numeric_types and name in TYPE_IDS:
        return str(TYPE_IDS[name])
    return name


def _write_node(parent_el: ET.Element, node: LsxNode, numeric_types: bool) -> None:
    node_el = ET.SubElement(
        parent_el, "node", id=_check_xml_writable(node.id, "node id")
    )
    if node.key:
        node_el.set("key", _check_xml_writable(node.key, "node key"))
    for attr in node.attributes.values():
        attr_el = ET.SubElement(
            node_el,
            "attribute",
            id=_check_xml_writable(attr.id, "attribute id"),
            type=_check_xml_writable(
                _type_spelling(attr.type, numeric_types),
                f"type of attribute {attr.id!r}",
            ),
        )
        if attr.handle is not None:
            attr_el.set(
                "handle",
                _check_xml_writable(attr.handle, f"handle of attribute {attr.id!r}"),
            )
            if attr.version is not None:
                attr_el.set("version", str(attr.version))
        if attr.value is not None:
            attr_el.set(
                "value",
                _check_xml_writable(attr.value, f"value of attribute {attr.id!r}"),
            )
    if node.children:
        children_el = ET.SubElement(node_el, "children")
        for child in node.children:
            _write_node(children_el, child, numeric_types)


def _parse_node(element: ET.Element) -> LsxNode:
    node = LsxNode(id=element.get("id", ""), key=element.get("key"))
    for attr_el in element.findall("attribute"):
        attr = LsxAttribute(
            id=attr_el.get("id", ""),
            type=type_name(attr_el.get("type")),
            value=attr_el.get("value"),
            handle=attr_el.get("handle"),
            version=int(attr_el.get("version")) if attr_el.get("version") else None,
        )
        node.attributes[attr.id] = attr
    for children_el in element.findall("children"):
        for child_el in children_el.findall("node"):
            node.children.append(_parse_node(child_el))
    return node
