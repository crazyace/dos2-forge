"""LSX parsing/writing tests, including DOS2's numeric type ids."""

from __future__ import annotations

import pytest

from dos2forge.parsers.lsx import LsxError, parse_lsx, write_lsx

from conftest import META_LSX, sample_document

DOS2_NUMERIC_TYPES = b"""\
<?xml version="1.0" encoding="utf-8"?>
<save>
  <version major="3" minor="6" revision="4" build="0" />
  <region id="Templates">
    <node id="Templates">
      <children>
        <node id="GameObjects">
          <attribute id="MapKey" type="22" value="abc" />
          <attribute id="Level" type="4" value="7" />
          <attribute id="DisplayName" type="28" handle="h1g2g3" version="1" value="Sword" />
        </node>
      </children>
    </node>
  </region>
</save>
"""


def test_numeric_type_ids_resolve_to_names():
    document = parse_lsx(DOS2_NUMERIC_TYPES)
    node = next(document.find_all("GameObjects"))
    assert node.attributes["MapKey"].type == "FixedString"
    assert node.attributes["Level"].type == "int32"
    display = node.attributes["DisplayName"]
    assert display.type == "TranslatedString"
    assert display.handle == "h1g2g3"
    assert display.value == "Sword"


def test_type_names_still_parse():
    document = parse_lsx(
        b'<save><region id="r"><node id="n">'
        b'<attribute id="A" type="FixedString" value="x" />'
        b"</node></region></save>"
    )
    assert document.regions["r"].attributes["A"].type == "FixedString"


def test_write_emits_numeric_ids_by_default():
    text = write_lsx(sample_document())
    assert 'type="22"' in text  # FixedString
    assert 'type="28"' in text  # TranslatedString
    assert "FixedString" not in text
    reparsed = parse_lsx(text)
    node = next(reparsed.find_all("GameObjects"))
    assert node.attributes["MapKey"].type == "FixedString"


def test_write_can_emit_type_names():
    text = write_lsx(sample_document(), numeric_types=False)
    assert 'type="FixedString"' in text


def test_round_trip_preserves_structure():
    document = parse_lsx(META_LSX)
    reparsed = parse_lsx(write_lsx(document))
    module = next(reparsed.find_all("ModuleInfo"))
    assert module.get("Name") == "TestMod"
    assert module.get("UUID") == "00000000-0000-0000-0000-000000000001"


def test_malformed_xml_is_rejected():
    with pytest.raises(LsxError, match="malformed LSX"):
        parse_lsx(b"<save><region></save>")


def test_wrong_root_is_rejected():
    with pytest.raises(LsxError, match="expected <save>"):
        parse_lsx(b"<config />")


def test_control_characters_fail_loudly_on_write():
    document = parse_lsx(META_LSX)
    module = next(document.find_all("ModuleInfo"))
    module.attributes["Name"].value = "bad\x00value"
    with pytest.raises(LsxError, match="control character"):
        write_lsx(document)
