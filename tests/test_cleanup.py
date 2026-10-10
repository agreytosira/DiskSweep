import threading
from dataclasses import replace

import pytest

from disk_sweep import cleanup
from disk_sweep.cleanup import remove_selected


def test_only_selected_files_are_deleted(make_file, candidate):
    selected = make_file("picked.tmp")
    kept = make_file("keep.tmp")
    result = remove_selected([candidate(selected)], "permanent", confirmation="DELETE")
    assert not selected.exists() and kept.exists()
    assert result.removed_bytes == 4 and not result.failed
    assert selected.parent.exists()


def test_confirmation_is_required(make_file, candidate):
    path = make_file()
    for mode in ("trash", "permanent"):
        with pytest.raises(ValueError, match="explicit"):
            remove_selected([candidate(path)], mode, confirmation="")
    assert path.exists()


def test_changed_file_is_kept(make_file, candidate):
    path = make_file()
    item = candidate(path)
    path.write_bytes(b"new contents")
    result = remove_selected([item], "permanent", confirmation="DELETE")
    assert path.exists() and not result.removed
    assert "changed" in result.failed[0][1]


def test_replaced_and_missing_files_are_kept(make_file, candidate):
    path = make_file()
    item = candidate(path)
    path.unlink()
    result = remove_selected([item], "permanent", confirmation="DELETE")
    assert len(result.failed) == 1
    path.write_bytes(b"replacement")
    result = remove_selected([item], "permanent", confirmation="DELETE")
    assert len(result.failed) == 1 and path.exists()


def test_outside_root_is_kept(tmp_path, make_file, candidate):
    path = make_file()
    other = tmp_path / "other"
    other.mkdir()
    result = remove_selected(
        [replace(candidate(path), root=other)], "permanent", confirmation="DELETE"
    )
    assert path.exists() and "outside" in result.failed[0][1]


def test_directory_is_kept(tmp_path, candidate):
    folder = tmp_path / "folder"
    folder.mkdir()
    result = remove_selected([candidate(folder)], "permanent", confirmation="DELETE")
    assert folder.exists() and "regular files" in result.failed[0][1]


def test_new_link_or_junction_is_kept(make_file, candidate, monkeypatch):
    path = make_file()
    item = candidate(path)
    monkeypatch.setattr(cleanup, "has_link_ancestor", lambda path: True)
    result = remove_selected([item], "permanent", confirmation="DELETE")
    assert path.exists() and "reparse point" in result.failed[0][1]


def test_trash_failure_does_not_fall_back_to_deletion(make_file, candidate):
    path = make_file()

    def failed_trash(_):
        raise PermissionError("Trash unavailable")

    result = remove_selected([candidate(path)], "trash", confirmation="TRASH", trash=failed_trash)
    assert path.exists() and not result.removed
    assert "Trash unavailable" in result.failed[0][1]


def test_trash_uses_only_selected_path(make_file, candidate):
    path = make_file()
    calls = []
    result = remove_selected([candidate(path)], "trash", confirmation="TRASH", trash=calls.append)
    assert calls == [str(path)] and len(result.removed) == 1


def test_duplicate_selection_and_cancellation(make_file, candidate):
    path = make_file()
    item = candidate(path)
    event = threading.Event()
    event.set()
    result = remove_selected([item], "permanent", confirmation="DELETE", cancel=event)
    assert result.cancelled and path.exists()
    result = remove_selected([item, item], "permanent", confirmation="DELETE")
    assert len(result.removed) == 1 and not result.failed


def test_one_failure_does_not_block_other_files(make_file, candidate, monkeypatch):
    bad = make_file("bad.tmp")
    good = make_file("good.tmp")
    original = cleanup.validate_candidate

    def guarded(item):
        if item.path == bad:
            raise PermissionError("locked fixture")
        original(item)

    monkeypatch.setattr(cleanup, "validate_candidate", guarded)
    result = remove_selected([candidate(bad), candidate(good)], "permanent", confirmation="DELETE")
    assert bad.exists() and not good.exists()
    assert len(result.removed) == len(result.failed) == 1


def test_native_trash_adapter_errors_are_reported(monkeypatch):
    import send2trash

    def broken(_):
        raise RuntimeError("native adapter fixture failure")

    broken.__module__ = "send2trash.win.modern"
    monkeypatch.setattr(send2trash, "send2trash", broken)
    with pytest.raises(OSError, match="OS Trash failed"):
        cleanup.native_trash("unused-fixture-path")


def test_windows_legacy_recycling_is_refused(monkeypatch):
    import os

    import send2trash

    if os.name != "nt":
        pytest.skip("Windows-only backend guard")
    calls = []

    def legacy(path):
        calls.append(path)

    legacy.__module__ = "send2trash.win.legacy"
    monkeypatch.setattr(send2trash, "send2trash", legacy)
    with pytest.raises(OSError, match="no permanent fallback"):
        cleanup.native_trash("unused-fixture-path")
    assert not calls
