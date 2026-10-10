import json

import pytest

from disk_sweep.cli import main


def test_json_report_is_read_only(tmp_path, make_file, capsys):
    path = make_file("report.tmp")
    assert main(["scan", str(tmp_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["candidates"][0]["path"] == str(path)
    assert report["total_bytes"] == 4 and path.exists()


def test_invalid_path_reports_error(tmp_path, capsys):
    assert main(["scan", str(tmp_path / "missing"), "--json"]) == 1
    assert "disk-sweep:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args", [["--age-days", "nan"], ["--min-size-mb", "-1"], ["--limit", "0"], ["--large-mb", "0"]]
)
def test_invalid_options_are_rejected(args):
    with pytest.raises(SystemExit) as exc:
        main(["scan", *args])
    assert exc.value.code == 2
