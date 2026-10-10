"""Keyboard and mouse cleanup interface."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Select, Static, TextArea

from disk_sweep.cleanup import CleanupResult, RemovalMode, remove_selected
from disk_sweep.scanner import Candidate, ScanOptions, ScanResult, default_roots, human_size, scan

PAGE_SIZE = 500


def file_details(item: Candidate) -> str:
    return (
        f"FILE\n{item.path}\n\nSize: {human_size(item.size)} ({item.size:,} bytes)\n"
        f"Modified: {item.age_days:.1f} days ago\nCategory: {item.category}\n\n"
        f"{item.reason}\n\nScanned folder: {item.root}"
    )


class MessageScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self.heading = title
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.heading, classes="dialog-title", markup=False)
            yield TextArea(self.message, read_only=True, id="message-body")
            yield Button("Close", id="close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#close", Button).focus()

    @on(Button.Pressed, "#close")
    def close_message(self) -> None:
        self.dismiss(None)


class ReviewScreen(ModalScreen[tuple[RemovalMode, str] | None]):
    BINDINGS = [("escape", "cancel", "Cancel"), ("f2", "details", "Full file details")]

    def __init__(self, items: list[Candidate]) -> None:
        super().__init__()
        self.items = items

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(
                f"Review {len(self.items):,} selected files · "
                f"{human_size(sum(item.size for item in self.items))}",
                classes="dialog-title",
                markup=False,
            )
            yield Static(
                "This list includes selections hidden by search, category filters, or paging.",
                markup=False,
            )
            yield DataTable(id="review-table", cursor_type="row")
            yield Select(
                [
                    ("Recycle Bin / Trash (recoverable)", "trash"),
                    ("Permanently delete (free space now)", "permanent"),
                ],
                value="trash",
                allow_blank=False,
                id="removal-mode",
            )
            yield Static(id="removal-note", markup=False)
            yield Input(placeholder="Type DELETE to allow permanent deletion", id="confirmation")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Cancel", id="cancel-review")
                yield Button("Move selected to Trash", id="confirm-review", variant="warning")

    def on_mount(self) -> None:
        table = self.query_one("#review-table", DataTable)
        table.add_columns("Size", "File", "Full path")
        table.add_rows(
            (human_size(item.size), Text(item.path.name), Text(str(item.path)))
            for item in self.items
        )
        self.update_mode()
        self.query_one("#cancel-review", Button).focus()

    def action_details(self) -> None:
        table = self.query_one("#review-table", DataTable)
        item = self.items[min(table.cursor_row, len(self.items) - 1)]
        self.app.push_screen(MessageScreen("Full file details", file_details(item)))

    @on(Select.Changed, "#removal-mode")
    @on(Input.Changed, "#confirmation")
    def update_mode(self) -> None:
        permanent = self.query_one("#removal-mode", Select).value == "permanent"
        confirmation = self.query_one("#confirmation", Input)
        confirmation.display = permanent
        note = (
            "Permanent deletion cannot be undone. Close apps using these files. Type DELETE below."
            if permanent
            else "Recover files from your OS Trash. Space is freed after you empty it yourself."
        )
        self.query_one("#removal-note", Static).update(note)
        button = self.query_one("#confirm-review", Button)
        button.label = "Permanently delete selected" if permanent else "Move selected to Trash"
        button.variant = "error" if permanent else "warning"
        button.disabled = permanent and confirmation.value != "DELETE"

    @on(Button.Pressed, "#cancel-review")
    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#confirm-review")
    def confirm(self) -> None:
        mode = self.query_one("#removal-mode", Select).value
        if mode == "permanent":
            phrase = self.query_one("#confirmation", Input).value
            if phrase != "DELETE":
                return
            self.dismiss(("permanent", phrase))
        else:
            self.dismiss(("trash", "TRASH"))


class SweepApp(App[None]):
    TITLE = "Disk Sweep"
    SUB_TITLE = "Scan · Select · Review"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("space", "toggle", "Select file"),
        Binding("s", "scan", "Scan"),
        Binding("d", "review", "Review selected"),
        Binding("slash", "search", "Search"),
        Binding("ctrl+a", "select_page", "Select page", show=False),
        Binding("ctrl+l", "clear_selection", "Clear selection", show=False),
        Binding("escape", "escape", "Cancel / focus list", show=False),
        Binding("f1", "help", "Help"),
        Binding("f2", "details", "File details", show=False),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, options: ScanOptions, *, demo: bool = False) -> None:
        super().__init__()
        self.options = options
        self.demo = demo
        self.initial_root = str(options.roots[0]) if options.roots else ""
        self.candidates: dict[str, Candidate] = {}
        self.selected: set[str] = set()
        self.page_items: list[Candidate] = []
        self.page = 0
        self.busy = False
        self.cancel_event = threading.Event()
        self.last_scan: ScanResult | None = None
        self.search_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "1  Scan a folder    2  Space / click to select    3  Review removal",
            id="intro",
            markup=False,
        )
        with Horizontal(id="scan-controls"):
            yield Select(
                [("Chosen folder(s)", "custom"), ("Temp + Downloads", "defaults")],
                value="custom",
                allow_blank=False,
                id="target",
            )
            yield Input(
                value=self.initial_root,
                placeholder="Folder to scan",
                id="root",
            )
            yield Button("Scan", id="scan", variant="primary")
            yield Button("Stop", id="stop", disabled=True)
        with Horizontal(id="options"):
            yield Select(
                [("Cleanup candidates", "junk"), ("Browse all files", "all")],
                value=self.options.mode,
                allow_blank=False,
                id="scan-mode",
            )
            yield Static("Older than (days)", classes="field-label", markup=False)
            yield Input(value=f"{self.options.min_age_days:g}", id="age", type="number")
            yield Static("Min size (MiB)", classes="field-label", markup=False)
            yield Input(
                value=f"{self.options.min_size_bytes / 1048576:g}", id="min-size", type="number"
            )
        yield Static(
            "DEMO · Only disposable sample files are scanned. Press Scan to start."
            if self.demo
            else "Ready. Scans only read files; nothing is selected automatically.",
            id="status",
            markup=False,
        )
        yield Static("0 files · 0 B found   |   0 selected · 0 B", id="totals", markup=False)
        with Horizontal(id="filters"):
            yield Input(placeholder="Search full paths…  /", id="search")
            yield Select(
                [
                    ("All categories", "all"),
                    ("Temporary", "Temporary"),
                    ("Cache", "Cache"),
                    ("Logs / backups", "Logs / backups"),
                    ("Large files", "Large files"),
                    ("Other files", "Other files"),
                    ("Selected only", "selected"),
                ],
                value="all",
                allow_blank=False,
                id="category",
            )
            yield Select(
                [("Largest", "size"), ("Oldest", "age"), ("Path A–Z", "path")],
                value="size",
                allow_blank=False,
                id="sort",
            )
        yield DataTable(id="files", cursor_type="row", zebra_stripes=True)
        yield Static("Scan a folder to see files here.", id="details", markup=False)
        with Horizontal(id="actions"):
            yield Button("Select page", id="select-page")
            yield Button("Clear all", id="clear")
            yield Button("Prev", id="previous", disabled=True)
            yield Static("No results", id="page-label", markup=False)
            yield Button("Next", id="next", disabled=True)
            yield Button("Review selected", id="review", variant="warning", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "textual-dark"
        table = self.query_one("#files", DataTable)
        for label, key, width in (
            ("Pick", "pick", 5),
            ("Size", "size", 12),
            ("Age", "age", 8),
            ("Category", "category", 16),
            ("File · folder", "path", None),
        ):
            table.add_column(label, key=key, width=width)
        self.query_one("#scan", Button).focus()
        if len(self.options.roots) > 1:
            self.query_one("#status", Static).update(
                "Ready · folders: " + " | ".join(str(p) for p in self.options.roots)
            )

    def main_active(self) -> bool:
        return not isinstance(self.screen, ModalScreen)

    @on(Select.Changed, "#target")
    def target_changed(self, event: Select.Changed) -> None:
        self.query_one("#root", Input).disabled = event.value == "defaults"

    def set_busy(self, value: bool) -> None:
        self.busy = value
        for widget_id in ("scan", "target", "scan-mode", "age", "min-size"):
            self.query_one(f"#{widget_id}").disabled = value
        self.query_one("#root", Input).disabled = (
            value or self.query_one("#target", Select).value == "defaults"
        )
        self.query_one("#stop", Button).disabled = not value
        self.query_one("#review", Button).disabled = value or not self.selected

    @on(Button.Pressed, "#scan")
    def action_scan(self) -> None:
        if self.busy or not self.main_active():
            return
        try:
            root_input = self.query_one("#root", Input).value.strip().strip('"')
            if self.query_one("#target", Select).value == "defaults":
                roots = default_roots()
            elif root_input == self.initial_root and self.options.roots:
                roots = self.options.roots
            elif root_input:
                roots = (Path(root_input),)
            else:
                raise ValueError("Enter a folder to scan.")
            age = float(self.query_one("#age", Input).value)
            size = float(self.query_one("#min-size", Input).value)
            options = replace(
                self.options,
                roots=roots,
                min_age_days=age,
                min_size_bytes=int(size * 1048576),
                mode=str(self.query_one("#scan-mode", Select).value),
            )
        except (ValueError, OverflowError) as exc:
            self.notify(str(exc), title="Check scan settings", severity="error")
            return
        self.cancel_event = threading.Event()
        self.set_busy(True)
        self.query_one("#status", Static).update("Scanning… Press Stop to keep partial results.")
        self.scan_worker(options, self.cancel_event)

    @work(thread=True, group="scan")
    def scan_worker(self, options: ScanOptions, cancel: threading.Event) -> None:
        try:
            result = scan(
                options,
                cancel,
                lambda examined, found, folder: self.call_from_thread(
                    self.scan_progress, examined, found, folder
                ),
            )
        except (OSError, ValueError) as exc:
            self.call_from_thread(self.scan_failed, str(exc))
        else:
            self.call_from_thread(self.scan_finished, result)

    def scan_progress(self, examined: int, found: int, folder: Path) -> None:
        self.query_one("#status", Static).update(
            f"Scanning · {examined:,} files checked · {found:,} candidates · {folder}"
        )

    def scan_failed(self, error: str) -> None:
        self.set_busy(False)
        self.query_one("#status", Static).update(f"Scan failed: {error}")
        self.notify(error, severity="error", timeout=8)

    def scan_finished(self, result: ScanResult) -> None:
        self.last_scan = result
        self.candidates = {str(item.path): item for item in result.candidates}
        self.selected.clear()
        self.page = 0
        self.set_busy(False)
        prefix = "Stopped · partial results" if result.cancelled else "Scan complete"
        if result.truncated:
            prefix = "Candidate limit reached · narrow your scan to see more"
        self.query_one("#status", Static).update(
            f"{prefix} · {result.examined:,} checked · {result.skipped:,} skipped · "
            f"{result.elapsed:.1f}s"
            + (f" · {len(result.errors)} access errors (F1)" if result.errors else "")
        )
        self.render_files()
        self.query_one("#files", DataTable).focus()

    @on(Button.Pressed, "#stop")
    def stop(self) -> None:
        if self.busy:
            self.cancel_event.set()
            self.query_one("#status", Static).update("Stopping after the current file…")

    def action_escape(self) -> None:
        if not self.main_active():
            return
        if self.busy:
            self.stop()
        else:
            self.query_one("#files", DataTable).focus()

    @on(Input.Changed, "#search")
    def search_changed(self) -> None:
        if self.search_timer is not None:
            self.search_timer.stop()
        self.search_timer = self.set_timer(0.15, self.filters_changed)

    @on(Select.Changed, "#category")
    @on(Select.Changed, "#sort")
    def filters_changed(self) -> None:
        self.page = 0
        self.render_files()

    def render_files(self) -> None:
        table = self.query_one("#files", DataTable)
        if not table.columns:
            return
        query = self.query_one("#search", Input).value.casefold()
        category = self.query_one("#category", Select).value
        matches = [
            item
            for key, item in self.candidates.items()
            if query in key.casefold()
            and (
                category == "all"
                or item.category == category
                or (category == "selected" and key in self.selected)
            )
        ]
        sort = self.query_one("#sort", Select).value
        if sort == "size":
            matches.sort(key=lambda item: (-item.size, str(item.path)))
        elif sort == "age":
            matches.sort(key=lambda item: (-item.age_days, str(item.path)))
        else:
            matches.sort(key=lambda item: str(item.path).casefold())
        pages = max(1, (len(matches) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.page = min(self.page, pages - 1)
        self.page_items = matches[self.page * PAGE_SIZE : (self.page + 1) * PAGE_SIZE]
        row = table.cursor_row
        table.clear()
        for item in self.page_items:
            key = str(item.path)
            table.add_row(
                "[x]" if key in self.selected else "[ ]",
                human_size(item.size),
                f"{item.age_days:.0f}d",
                item.category,
                Text(f"{item.path.name}  ·  {item.path.parent}"),
                key=key,
            )
        if self.page_items:
            table.move_cursor(row=min(row, len(self.page_items) - 1))
        self.query_one("#page-label", Static).update(
            f"{len(matches):,} matches · {self.page + 1}/{pages}"
        )
        self.query_one("#previous", Button).disabled = self.page == 0
        self.query_one("#next", Button).disabled = self.page >= pages - 1
        self.query_one("#select-page", Button).disabled = not self.page_items or self.busy
        self.update_totals()
        self.update_details()

    def update_totals(self) -> None:
        selected_bytes = sum(self.candidates[key].size for key in self.selected)
        shown_keys = {str(item.path) for item in self.page_items}
        hidden = len(self.selected - shown_keys)
        text = (
            f"{len(self.candidates):,} files · "
            f"{human_size(sum(item.size for item in self.candidates.values()))} found   |   "
            f"{len(self.selected):,} selected · {human_size(selected_bytes)}"
        )
        if hidden:
            text += f" · {hidden:,} off this page"
        self.query_one("#totals", Static).update(text)
        self.query_one("#review", Button).disabled = self.busy or not self.selected

    def update_details(self) -> None:
        table = self.query_one("#files", DataTable)
        if not self.page_items:
            message = (
                "No matches. Try another category, clear search, or set age to 0 and rescan."
                if self.last_scan
                else "Scan a folder to see files here."
            )
        else:
            item = self.page_items[min(table.cursor_row, len(self.page_items) - 1)]
            message = f"{item.reason}\nF2: full path and details · {item.path}"
        self.query_one("#details", Static).update(message)

    @on(DataTable.RowHighlighted, "#files")
    def highlight(self) -> None:
        self.update_details()

    @on(DataTable.RowSelected, "#files")
    def row_selected(self) -> None:
        self.action_toggle()

    def action_toggle(self) -> None:
        if self.busy or not self.page_items or not self.main_active():
            return
        table = self.query_one("#files", DataTable)
        if not table.has_focus:
            return
        item = self.page_items[min(table.cursor_row, len(self.page_items) - 1)]
        key = str(item.path)
        if key in self.selected:
            self.selected.remove(key)
        else:
            self.selected.add(key)
        if self.query_one("#category", Select).value == "selected":
            self.render_files()
        else:
            table.update_cell(key, "pick", "[x]" if key in self.selected else "[ ]")
            self.update_totals()

    @on(Button.Pressed, "#select-page")
    def action_select_page(self) -> None:
        if not self.busy and self.main_active():
            self.selected.update(str(item.path) for item in self.page_items)
            self.render_files()

    @on(Button.Pressed, "#clear")
    def action_clear_selection(self) -> None:
        if not self.busy and self.main_active():
            self.selected.clear()
            self.render_files()

    @on(Button.Pressed, "#previous")
    def previous_page(self) -> None:
        self.page = max(0, self.page - 1)
        self.render_files()

    @on(Button.Pressed, "#next")
    def next_page(self) -> None:
        self.page += 1
        self.render_files()

    def action_search(self) -> None:
        if self.main_active():
            self.query_one("#search", Input).focus()

    @on(Button.Pressed, "#review")
    def action_review(self) -> None:
        if self.busy or not self.selected or not self.main_active():
            return
        items = sorted(
            (self.candidates[key] for key in self.selected), key=lambda item: str(item.path)
        )
        self.push_screen(ReviewScreen(items), lambda choice: self.start_cleanup(items, choice))

    def start_cleanup(self, items: list[Candidate], choice: tuple[RemovalMode, str] | None) -> None:
        if choice is None:
            return
        self.cancel_event = threading.Event()
        self.set_busy(True)
        self.query_one("#status", Static).update(
            "Removing selected files… Stop cancels remaining files."
        )
        self.cleanup_worker(items, choice, self.cancel_event)

    @work(thread=True, group="cleanup")
    def cleanup_worker(
        self, items: list[Candidate], choice: tuple[RemovalMode, str], cancel: threading.Event
    ) -> None:
        mode, phrase = choice
        result = remove_selected(
            items,
            mode,
            confirmation=phrase,
            cancel=cancel,
            progress=lambda done, total: self.call_from_thread(self.cleanup_progress, done, total),
        )
        self.call_from_thread(self.cleanup_finished, result, mode)

    def cleanup_progress(self, done: int, total: int) -> None:
        self.query_one("#status", Static).update(f"Removing selected files · {done:,}/{total:,}")

    def cleanup_finished(self, result: CleanupResult, mode: RemovalMode) -> None:
        for item in result.removed:
            key = str(item.path)
            self.candidates.pop(key, None)
            self.selected.discard(key)
        self.set_busy(False)
        amount = human_size(result.removed_bytes)
        message = (
            f"Permanently deleted {len(result.removed):,} files · {amount} logical size removed."
            if mode == "permanent"
            else (
                f"Moved {len(result.removed):,} files · {amount} to Trash. "
                "Empty Trash to free space."
            )
        )
        if result.cancelled:
            message += " Stopped; remaining files were kept."
        if result.failed:
            message += f" {len(result.failed):,} files could not be removed."
        self.query_one("#status", Static).update(message)
        self.render_files()
        if result.failed:
            details = "\n\n".join(f"{item.path}\n{reason}" for item, reason in result.failed)
            self.push_screen(MessageScreen("Files kept · removal errors", details))

    def action_details(self) -> None:
        if not self.page_items or not self.main_active():
            return
        table = self.query_one("#files", DataTable)
        item = self.page_items[min(table.cursor_row, len(self.page_items) - 1)]
        self.push_screen(MessageScreen("Full file details", file_details(item)))

    def action_help(self) -> None:
        if not self.main_active():
            return
        message = (
            "QUICK START\n"
            "1. Choose a folder or the Temp + Downloads preset; press Scan.\n"
            "2. Use arrows to move. Space / Enter / click toggles a file.\n"
            "3. Search full paths and filter by category. Selections stay selected.\n"
            "4. Review selected shows ALL selected files, including hidden selections.\n"
            "5. Choose Trash or permanent deletion, then explicitly confirm.\n\n"
            "CONTROLS\n"
            "Tab / Shift+Tab: move between controls. /: search. Escape: focus list / stop.\n"
            "S: scan. D: review. Ctrl+A: select this page (up to 500 files).\n"
            "Ctrl+L: clear ALL selections. Ctrl+Q: quit. F1: help. F2: full file details.\n\n"
            "SCAN RULES\n"
            "Default: files modified at least 7 days ago. Large files: 100 MiB or more.\n"
            "Browse all: any regular file matching your age and size limits.\n"
            "Age is modification time, not last use. Candidates are suggestions.\n"
            "Links, junctions, system folders, credentials, repositories' .git folders,\n"
            "virtual environments, and node_modules are excluded in both modes.\n"
            "Folders are never deleted. Changed, locked, or unreadable files are kept.\n"
            "Sizes are logical bytes; sparse, compressed, and hard-linked files differ.\n"
            "Trash is recoverable but does not immediately free disk space.\n"
            "Stop keeps partial scan results / cancels remaining removals."
        )
        if self.last_scan and self.last_scan.errors:
            message += "\n\nSCAN ACCESS ERRORS (first 100)\n" + "\n".join(self.last_scan.errors)
        self.push_screen(MessageScreen("Disk Sweep · help", message))

    def action_quit(self) -> None:
        self.cancel_event.set()
        if self.busy:
            self.notify("Stopping work. Quit again after it finishes.", timeout=3)
        else:
            self.exit()
