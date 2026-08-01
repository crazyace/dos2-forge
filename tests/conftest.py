"""Shared fixtures: in-memory game-data fixtures built with dos2forge's
own writers, so the suite runs without a game install."""

from __future__ import annotations

import pytest

from dos2forge.pak import CompressionMethod, PakWriter
from dos2forge.parsers.lsx import LsxAttribute, LsxDocument, LsxNode

WEAPON_TXT = b"""\
new entry "_BaseWeapon"
data "Durability" "100"
data "Value" "1"

new entry "WPN_Sword_1H"
type "Weapon"
using "_BaseWeapon"
data "Damage Type" "Slashing"
data "Damage Range" "4"
"""

META_LSX = b"""\
<?xml version="1.0" encoding="utf-8"?>
<save>
  <version major="3" minor="6" revision="4" build="0" />
  <region id="Config">
    <node id="root">
      <children>
        <node id="ModuleInfo">
          <attribute id="Name" type="22" value="TestMod" />
          <attribute id="UUID" type="22" value="00000000-0000-0000-0000-000000000001" />
        </node>
      </children>
    </node>
  </region>
</save>
"""

SAMPLE_FILES = {
    "Public/Shared/Stats/Generated/Data/Weapon.txt": WEAPON_TXT,
    "Mods/Shared/meta.lsx": META_LSX,
    "Public/Shared/Assets/empty.bin": b"",
}


@pytest.fixture
def make_pak(tmp_path):
    """Build a .pak on disk and return its path."""

    def build(
        files: dict[str, bytes] | None = None,
        version: int = 13,
        compression: CompressionMethod = CompressionMethod.LZ4,
        name: str = "Test.pak",
    ):
        writer = PakWriter(version=version, compression=compression)
        for entry_name, data in (files or SAMPLE_FILES).items():
            writer.add(entry_name, data)
        return writer.write(tmp_path / name)

    return build


def sample_document() -> LsxDocument:
    """A small node tree exercising the common attribute types."""
    root = LsxNode(id="Templates")
    child = LsxNode(id="GameObjects")
    child.attributes["MapKey"] = LsxAttribute(
        id="MapKey", type="FixedString",
        value="123e4567-e89b-42d3-a456-426614174000",
    )
    child.attributes["Name"] = LsxAttribute(id="Name", type="LSString", value="Longsword")
    child.attributes["Level"] = LsxAttribute(id="Level", type="int32", value="3")
    child.attributes["Weight"] = LsxAttribute(id="Weight", type="float", value="1.5")
    child.attributes["IsUnique"] = LsxAttribute(id="IsUnique", type="bool", value="True")
    child.attributes["Rotation"] = LsxAttribute(
        id="Rotation", type="fvec3", value="0.0 1.0 0.0"
    )
    child.attributes["Parent"] = LsxAttribute(
        id="Parent", type="guid", value="123e4567-e89b-42d3-a456-426614174000"
    )
    child.attributes["Big"] = LsxAttribute(id="Big", type="uint64", value="9007199254740993")
    child.attributes["DisplayName"] = LsxAttribute(
        id="DisplayName", type="TranslatedString",
        value="Longsword", handle="h11111111g2222g3333g4444g555555555555",
    )
    root.children.append(child)
    document = LsxDocument()
    document.regions["Templates"] = root
    return document
