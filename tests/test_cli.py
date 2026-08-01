"""CLI smoke tests, driven through main(argv)."""

from __future__ import annotations

from dos2forge.cli.main import main
from dos2forge.parsers.lsf import write_lsf

from conftest import SAMPLE_FILES, sample_document


def test_list(make_pak, capsys):
    assert main(["list", str(make_pak())]) == 0
    out = capsys.readouterr().out
    for name in SAMPLE_FILES:
        assert name in out


def test_cat(make_pak, capsys):
    name = "Public/Shared/Stats/Generated/Data/Weapon.txt"
    assert main(["cat", str(make_pak()), name]) == 0
    assert 'new entry "WPN_Sword_1H"' in capsys.readouterr().out


def test_unpack_with_pattern(make_pak, tmp_path, capsys):
    out_dir = tmp_path / "out"
    code = main(
        ["unpack", str(make_pak()), "-o", str(out_dir), "-p", "*/stats/*"]
    )
    assert code == 0
    extracted = out_dir / "Public/Shared/Stats/Generated/Data/Weapon.txt"
    assert extracted.read_bytes() == SAMPLE_FILES[
        "Public/Shared/Stats/Generated/Data/Weapon.txt"
    ]
    assert not (out_dir / "Mods/Shared/meta.lsx").exists()


def test_search_across_data_dir(make_pak, tmp_path, capsys):
    make_pak(name="Shared.pak")
    code = main(["--data-dir", str(tmp_path), "search", "*/stats/*.txt"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Shared.pak  Public/Shared/Stats/Generated/Data/Weapon.txt" in out
    assert "1 match(es)" in out


def test_convert_lsf_to_lsx_and_back(tmp_path, capsys):
    lsf_path = tmp_path / "in.lsf"
    lsf_path.write_bytes(write_lsf(sample_document()))
    lsx_path = tmp_path / "out.lsx"
    assert main(["convert", str(lsf_path), str(lsx_path)]) == 0
    assert 'value="Longsword"' in lsx_path.read_text("utf-8")

    lsf_again = tmp_path / "again.lsf"
    assert main(["convert", str(lsx_path), str(lsf_again)]) == 0
    assert lsf_again.read_bytes()[:4] == b"LSOF"


def test_convert_rejects_unknown_output(tmp_path, capsys):
    lsf_path = tmp_path / "in.lsf"
    lsf_path.write_bytes(write_lsf(sample_document()))
    assert main(["convert", str(lsf_path), str(tmp_path / "out.docx")]) == 1


def test_doctor_with_data_dir(make_pak, tmp_path, capsys):
    make_pak(name="Shared.pak")
    assert main(["--data-dir", str(tmp_path), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "Shared.pak: v13" in out


def test_doctor_without_install(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DOS2_PATH", raising=False)
    # An empty --data-dir means no readable paks; doctor still reports.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["--data-dir", str(empty), "doctor"]) == 0
    assert "no readable .pak archives" in capsys.readouterr().out


def test_missing_pak_reports_error(tmp_path, capsys):
    assert main(["list", str(tmp_path / "missing.pak")]) == 1
    assert "error:" in capsys.readouterr().err
