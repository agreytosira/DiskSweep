import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from disk_sweep import scanner
from disk_sweep.scanner import ScanOptions, human_size, is_link, normalize_roots, scan


def test_read_only_scan_sorted_by_size(tmp_path, make_file):
    small = make_file("small.tmp", b"x")
    big = make_file("big.tmp", b"x" * 200)
    before = {path: path.read_bytes() for path in (small, big)}
    result = scan(ScanOptions((tmp_path,)))
    assert [item.path for item in result.candidates] == [big, small]
    assert result.total_bytes == 201
    assert {path: path.read_bytes() for path in before} == before


def test_categories_and_all_mode(tmp_path, make_file, monkeypatch):
    monkeypatch.setattr(scanner.tempfile, "gettempdir", lambda: str(tmp_path / "elsewhere"))
    paths = [
        make_file("app/.cache/cached.bin"),
        make_file("old.tmp"),
        make_file("app.log"),
        make_file("important.txt"),
        make_file("big.iso", b"x" * 200),
    ]
    options = ScanOptions((tmp_path,), large_file_bytes=100)
    result = scan(options)
    assert {item.path: item.category for item in result.candidates} == {
        paths[0]: "Cache",
        paths[1]: "Temporary",
        paths[2]: "Logs / backups",
        paths[4]: "Large files",
    }
    assert len(scan(replace(options, mode="all")).candidates) == 5


def test_age_size_and_recent_files(tmp_path, make_file):
    make_file("recent.tmp", b"x" * 100, days=0)
    make_file("small.tmp", b"x", days=30)
    big = make_file("big.tmp", b"x" * 100, days=30)
    assert [c.path for c in scan(ScanOptions((tmp_path,), min_size_bytes=50)).candidates] == [big]
    assert len(scan(ScanOptions((tmp_path,), min_age_days=0)).candidates) == 3


def test_protected_and_excluded_paths_stay_hidden(tmp_path, make_file):
    for name in (".git", ".ssh", ".venv", "node_modules"):
        make_file(f"{name}/secret.tmp")
    excluded = make_file("skip/data.tmp")
    shown = make_file("show/data.tmp")
    result = scan(ScanOptions((tmp_path,), mode="all", excludes=(excluded.parent,)))
    assert [item.path for item in result.candidates] == [shown]
    assert result.skipped == 5


def test_roots_are_deduplicated(tmp_path):
    child = tmp_path / "child"
    child.mkdir()
    assert normalize_roots((child, tmp_path, child)) == (tmp_path,)


def test_candidate_limit_and_cancellation(tmp_path, make_file):
    for i in range(4):
        make_file(f"{i}.tmp")
    result = scan(ScanOptions((tmp_path,), max_candidates=2))
    assert result.truncated and len(result.candidates) == 2
    event = threading.Event()
    event.set()
    result = scan(ScanOptions((tmp_path,)), event)
    assert result.cancelled and result.examined == 0


def test_unreadable_folder_does_not_stop_scan(tmp_path, make_file, monkeypatch):
    good = make_file("good.tmp")
    blocked = make_file("blocked/hidden.tmp")
    real_scandir = scanner.os.scandir

    def guarded(path):
        if Path(path) == blocked.parent:
            raise PermissionError("locked fixture")
        return real_scandir(path)

    monkeypatch.setattr(scanner.os, "scandir", guarded)
    result = scan(ScanOptions((tmp_path,)))
    assert [item.path for item in result.candidates] == [good]
    assert len(result.errors) == 1 and "locked fixture" in result.errors[0]


def test_reparse_points_are_links():
    assert is_link(SimpleNamespace(st_mode=0, st_file_attributes=0x400))


def test_linked_root_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "has_link_ancestor", lambda path: True)
    with pytest.raises(ValueError, match="symlink or junction"):
        normalize_roots((tmp_path,))


def test_invalid_settings_and_missing_root(tmp_path):
    for kwargs in ({"min_age_days": -1}, {"mode": "unknown"}, {"max_candidates": 0}):
        with pytest.raises(ValueError):
            ScanOptions((tmp_path,), **kwargs)
    with pytest.raises(OSError):
        normalize_roots((tmp_path / "missing",))
    with pytest.raises(ValueError):
        normalize_roots(())


@pytest.mark.parametrize(("size", "text"), [(0, "0 B"), (1024, "1.0 KiB"), (1024**3, "1.0 GiB")])
def test_human_size(size, text):
    assert human_size(size) == text
