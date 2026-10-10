from textual.widgets import Button, DataTable, Input, Select

from disk_sweep.app import MessageScreen, ReviewScreen, SweepApp
from disk_sweep.scanner import ScanOptions, scan


async def wait_idle(pilot, app):
    for _ in range(100):
        await pilot.pause(0.03)
        if not app.busy:
            return
    raise AssertionError("Worker did not finish")


async def test_scan_toggle_filter_and_cancel_review(tmp_path, make_file):
    first = make_file("a.tmp", b"x" * 10)
    second = make_file("b.tmp", b"x")
    app = SweepApp(ScanOptions((tmp_path,)))
    async with app.run_test(size=(120, 36)) as pilot:
        assert not app.selected and app.last_scan is None
        await pilot.click("#scan")
        await wait_idle(pilot, app)
        assert len(app.page_items) == 2
        await pilot.press("space")
        assert app.selected == {str(first)}
        app.query_one("#search", Input).value = "b.tmp"
        await pilot.pause(0.3)
        assert [item.path for item in app.page_items] == [second]
        assert app.selected == {str(first)}
        await pilot.click("#review")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        assert [item.path for item in app.screen.items] == [first]
        await pilot.press("escape")
        assert first.exists() and second.exists()


async def test_permanent_confirmation_and_selected_only_removal(tmp_path, make_file):
    selected = make_file("picked.tmp", b"x" * 10)
    kept = make_file("keep.tmp", b"x")
    app = SweepApp(ScanOptions((tmp_path,)))
    async with app.run_test(size=(120, 36)) as pilot:
        app.scan_finished(scan(app.options))
        app.query_one("#files", DataTable).focus()
        await pilot.press("space", "d")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        app.screen.query_one("#removal-mode", Select).value = "permanent"
        await pilot.pause()
        button = app.screen.query_one("#confirm-review", Button)
        assert button.disabled
        app.screen.query_one("#confirmation", Input).value = "DELETE"
        await pilot.pause()
        assert not button.disabled
        await pilot.click("#confirm-review")
        await wait_idle(pilot, app)
        assert not selected.exists() and kept.exists()
        assert not app.selected and len(app.candidates) == 1


async def test_select_page_and_clear(tmp_path, make_file):
    for i in range(3):
        make_file(f"{i}.tmp")
    app = SweepApp(ScanOptions((tmp_path,)))
    async with app.run_test(size=(100, 32)) as pilot:
        app.scan_finished(scan(app.options))
        await pilot.click("#select-page")
        assert len(app.selected) == 3
        await pilot.click("#clear")
        assert not app.selected


async def test_invalid_folder_keeps_previous_results(tmp_path, make_file):
    make_file()
    app = SweepApp(ScanOptions((tmp_path,)))
    async with app.run_test(size=(120, 36)) as pilot:
        app.scan_finished(scan(app.options))
        app.query_one("#root", Input).value = str(tmp_path / "missing")
        await pilot.click("#scan")
        await wait_idle(pilot, app)
        assert len(app.candidates) == 1
        assert not app.busy


async def test_help_and_empty_results(tmp_path):
    app = SweepApp(ScanOptions((tmp_path,)))
    async with app.run_test(size=(100, 32)) as pilot:
        app.scan_finished(scan(app.options))
        assert app.query_one("#review", Button).disabled
        await pilot.press("f1")
        await pilot.pause()
        await pilot.press("escape")
        assert not app.page_items


async def test_small_terminal_keeps_controls_and_confirmation_visible(tmp_path, make_file):
    make_file()
    app = SweepApp(ScanOptions((tmp_path,)))
    async with app.run_test(size=(80, 24)) as pilot:
        app.scan_finished(scan(app.options))
        await pilot.pause()
        for identifier in ("scan", "files", "select-page", "clear", "review"):
            region = app.query_one(f"#{identifier}").region
            assert region.right <= 80 and region.bottom <= 23, (identifier, region)
        await pilot.press("space", "d")
        await pilot.pause()
        app.screen.query_one("#removal-mode", Select).value = "permanent"
        await pilot.pause()
        for identifier in ("review-table", "confirmation", "cancel-review", "confirm-review"):
            region = app.screen.query_one(f"#{identifier}").region
            assert region.right <= 80 and region.bottom <= 24, (identifier, region)
        await pilot.press("escape")


async def test_paging_preserves_hidden_selections(tmp_path, make_file):
    from disk_sweep.app import PAGE_SIZE

    for i in range(PAGE_SIZE + 1):
        make_file(f"{i}.tmp")
    app = SweepApp(ScanOptions((tmp_path,)))
    async with app.run_test(size=(120, 36)) as pilot:
        app.scan_finished(scan(app.options))
        await pilot.click("#select-page")
        assert len(app.selected) == PAGE_SIZE
        await pilot.click("#next")
        assert len(app.page_items) == 1 and len(app.selected) == PAGE_SIZE
        await pilot.click("#review")
        await pilot.pause()
        assert len(app.screen.items) == PAGE_SIZE
        await pilot.press("escape")
        await pilot.click("#clear")
        assert not app.selected


async def test_full_details_in_main_and_review(tmp_path, make_file):
    from textual.widgets import TextArea

    path = make_file("nested/sample.tmp")
    app = SweepApp(ScanOptions((tmp_path,)))
    async with app.run_test(size=(80, 24)) as pilot:
        app.scan_finished(scan(app.options))
        await pilot.press("f2")
        await pilot.pause()
        assert isinstance(app.screen, MessageScreen)
        assert str(path) in app.screen.query_one("#message-body", TextArea).text
        await pilot.press("escape", "space", "d")
        await pilot.pause()
        assert isinstance(app.screen, ReviewScreen)
        await pilot.press("f2")
        await pilot.pause()
        assert str(path) in app.screen.query_one("#message-body", TextArea).text
        await pilot.press("escape")
        assert isinstance(app.screen, ReviewScreen)
        await pilot.press("escape")
        assert path.exists() and app.selected == {str(path)}
