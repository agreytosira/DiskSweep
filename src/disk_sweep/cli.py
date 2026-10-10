"""Read-only CLI reports and the interactive launcher."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from disk_sweep import __version__
from disk_sweep.scanner import MIB, ScanOptions, default_roots, human_size, scan


def nonnegative_number(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Enter a number.") from exc
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("Enter a finite, nonnegative number.")
    return number


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Enter a positive integer.") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("Enter a positive integer.")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="disk-sweep",
        description="Scan, select, and review files before cleaning disk space.",
        epilog="Run disk-sweep with no arguments for the TUI. Nothing is selected automatically.",
    )
    parser.add_argument("--version", action="version", version=f"Disk Sweep {__version__}")
    commands = parser.add_subparsers(dest="command")
    for name, help_text in (
        ("tui", "Interactive cleanup (default)"),
        ("scan", "Read-only scan report; never deletes files"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "paths", nargs="*", type=Path, help="Folders (default: Temp + Downloads)"
        )
        command.add_argument(
            "--mode",
            choices=("junk", "all"),
            default="junk",
            help="Cleanup candidates, or browse all regular files",
        )
        command.add_argument(
            "--age-days",
            type=nonnegative_number,
            default=7,
            help="Minimum modification age; 0 includes recent files (default: 7)",
        )
        command.add_argument(
            "--min-size-mb", type=nonnegative_number, default=0, help="Minimum file size in MiB"
        )
        command.add_argument(
            "--large-mb",
            type=positive_int,
            default=100,
            help="Large-file category threshold in MiB (default: 100)",
        )
        command.add_argument(
            "--exclude",
            type=Path,
            action="append",
            default=[],
            help="Exclude a file or folder; repeat as needed",
        )
        command.add_argument(
            "--limit",
            type=positive_int,
            default=50_000,
            help="Stop at this number of candidates (default: 50000)",
        )
        if name == "scan":
            command.add_argument("--json", action="store_true", help="Machine-readable report")
        else:
            command.add_argument(
                "--demo", action="store_true", help="Try the TUI with disposable sample files"
            )
    return parser


@contextmanager
def demo_folder():
    with tempfile.TemporaryDirectory(prefix="disk-sweep-demo-") as folder:
        root = Path(folder).resolve()
        samples = (
            ("old-download.tmp", 3 * MIB, 34),
            ("app/cache/render.bin", 8 * MIB, 14),
            ("python/__pycache__/module.pyc", 2 * MIB, 9),
            ("logs/debug.log", MIB, 60),
            ("backup/settings.bak", 8192, 90),
            ("keep-me.txt", 64, 0),
        )
        for name, size, age in samples:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as stream:
                stream.truncate(size)
            modified = time.time() - age * 86400
            os.utime(path, (modified, modified))
        yield root


def options_from_args(args: argparse.Namespace, roots: tuple[Path, ...]) -> ScanOptions:
    return ScanOptions(
        roots=roots,
        mode=args.mode,
        min_age_days=args.age_days,
        min_size_bytes=int(args.min_size_mb * MIB),
        large_file_bytes=args.large_mb * MIB,
        max_candidates=args.limit,
        excludes=tuple(args.exclude),
    )


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if not values or values[0] not in {"scan", "tui", "-h", "--help", "--version"}:
        values.insert(0, "tui")
    parser = build_parser()
    args = parser.parse_args(values)
    roots = tuple(args.paths) or default_roots()
    try:
        options = options_from_args(args, roots)
        if args.command == "scan":
            result = scan(options)
            if args.json:
                print(
                    json.dumps(
                        {
                            "candidates": [item.as_dict() for item in result.candidates],
                            "total_bytes": result.total_bytes,
                            "examined": result.examined,
                            "skipped": result.skipped,
                            "errors": result.errors,
                            "truncated": result.truncated,
                            "elapsed_seconds": round(result.elapsed, 3),
                        },
                        indent=2,
                        ensure_ascii=True,
                    )
                )
            else:
                console = Console()
                table = Table(title="Disk Sweep · read-only scan")
                for column in ("Size", "Age", "Category", "Full path"):
                    table.add_column(column)
                for item in result.candidates:
                    table.add_row(
                        human_size(item.size),
                        f"{item.age_days:.0f}d",
                        item.category,
                        Text(str(item.path)),
                    )
                console.print(table)
                console.print(
                    Text(
                        f"{len(result.candidates):,} files · {human_size(result.total_bytes)} · "
                        f"{result.examined:,} checked · {result.skipped:,} skipped. "
                        "No files removed."
                    )
                )
                if result.truncated:
                    console.print("Candidate limit reached; narrow your scan or increase --limit.")
                for error in result.errors:
                    console.print(Text(error, style="yellow"))
            return 0
        from disk_sweep.app import SweepApp

        if args.demo:
            with demo_folder() as root:
                SweepApp(options_from_args(args, (root,)), demo=True).run()
        else:
            SweepApp(options).run()
        return 0
    except (OSError, ValueError, OverflowError) as exc:
        print(f"disk-sweep: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
