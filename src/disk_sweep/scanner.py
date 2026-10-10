"""Read-only scanning and shared filesystem safety checks."""

from __future__ import annotations

import math
import os
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

MIB = 1024 * 1024
CACHE_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    "cache",
    "caches",
    "code cache",
    "gpucache",
    "_cacache",
}
EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "$recycle.bin",
    "system volume information",
    ".trash",
    ".trashes",
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".codex",
    ".agents",
}


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if value < 1024 or unit == "PiB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return "0 B"


def is_link(info: os.stat_result) -> bool:
    # Windows junctions / cloud placeholders are reparse points, not always symlinks.
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def has_link_ancestor(path: Path) -> bool:
    for part in (path, *path.parents):
        if is_link(part.lstat()):
            return True
    return False


def protected_roots() -> tuple[Path, ...]:
    if os.name == "nt":
        paths = [
            os.environ.get(key)
            for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
        ]
        return tuple(Path(p).absolute() for p in paths if p)
    return tuple(
        Path(p)
        for p in (
            "/etc",
            "/usr",
            "/bin",
            "/sbin",
            "/lib",
            "/lib64",
            "/boot",
            "/dev",
            "/proc",
            "/sys",
            "/run",
            "/System",
            "/Library",
        )
    )


def is_protected(path: Path) -> bool:
    return any(part.lower() in EXCLUDED_DIRS for part in path.parts) or any(
        path.is_relative_to(root) for root in protected_roots()
    )


def normalize_roots(paths: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    for raw in paths:
        path = Path(os.path.abspath(raw.expanduser()))
        if has_link_ancestor(path):
            raise ValueError(f"Choose a real folder, not a symlink or junction: {path}")
        if not path.is_dir():
            raise ValueError(f"Not a readable folder: {path}")
        if is_protected(path):
            raise ValueError(f"Protected folder: {path}")
        path = path.resolve(strict=True)
        if any(path.is_relative_to(root) for root in result):
            continue
        result = [root for root in result if not root.is_relative_to(path)]
        result.append(path)
    if not result:
        raise ValueError("Choose at least one folder to scan.")
    return tuple(result)


def default_roots() -> tuple[Path, ...]:
    paths = [Path(tempfile.gettempdir()), Path.home() / "Downloads"]
    return tuple(p for p in paths if p.is_dir() and not is_protected(p))


@dataclass(frozen=True)
class ScanOptions:
    roots: tuple[Path, ...]
    mode: str = "junk"
    min_age_days: float = 7
    min_size_bytes: int = 0
    large_file_bytes: int = 100 * MIB
    max_candidates: int = 50_000
    excludes: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in {"junk", "all"}:
            raise ValueError("Mode must be junk or all.")
        if not math.isfinite(self.min_age_days) or self.min_age_days < 0 or self.min_size_bytes < 0:
            raise ValueError("Age and size cannot be negative.")
        if self.max_candidates < 1 or self.large_file_bytes < 1:
            raise ValueError("Candidate limit and large-file threshold must be positive.")


@dataclass(frozen=True)
class Candidate:
    path: Path
    root: Path
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int
    category: str
    reason: str
    age_days: float

    @classmethod
    def from_stat(
        cls, path: Path, root: Path, info: os.stat_result, category: str, reason: str, now: float
    ) -> Candidate:
        return cls(
            path,
            root,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            info.st_dev,
            info.st_ino,
            category,
            reason,
            max(0, (now - info.st_mtime) / 86400),
        )

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "size_bytes": self.size,
            "category": self.category,
            "reason": self.reason,
            "age_days": round(self.age_days, 1),
        }


@dataclass
class ScanResult:
    candidates: list[Candidate] = field(default_factory=list)
    examined: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    truncated: bool = False
    elapsed: float = 0

    @property
    def total_bytes(self) -> int:
        return sum(item.size for item in self.candidates)


def classify(path: Path, options: ScanOptions, size: int) -> tuple[str, str] | None:
    parts = {part.lower() for part in path.parent.parts}
    suffix = path.suffix.lower()
    if parts & CACHE_DIRS:
        return "Cache", "Inside a cache folder; the app may rebuild it. Close the owning app first."
    if suffix in {".tmp", ".temp", ".part", ".crdownload"}:
        return "Temporary", "Temporary or incomplete download. Check that no app is using it."
    if suffix in {".log", ".old", ".bak"}:
        return "Logs / backups", "Log or backup file. Keep it if you need history or recovery."
    temp_root = Path(tempfile.gettempdir()).resolve()
    if path.is_relative_to(temp_root):
        return "Temporary", "Inside your temporary folder. Check that no app is using it."
    if size >= options.large_file_bytes:
        return "Large files", "Large file; size alone does not mean it is unused. Review carefully."
    if options.mode == "all":
        return "Other files", "Shown by Browse all mode. You decide whether to keep it."
    return None


def scan(
    options: ScanOptions,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int, Path], None] | None = None,
) -> ScanResult:
    started = time.monotonic()
    now = time.time()
    roots = normalize_roots(options.roots)
    exclusions = tuple(p.expanduser().resolve() for p in options.excludes)
    result = ScanResult()
    last_progress = started
    for root in roots:
        stack = [root]
        while stack:
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                break
            folder = stack.pop()
            try:
                if (
                    has_link_ancestor(folder)
                    or is_protected(folder)
                    or any(folder.is_relative_to(p) for p in exclusions)
                ):
                    result.skipped += 1
                    continue
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if cancel is not None and cancel.is_set():
                            result.cancelled = True
                            break
                        path = Path(entry.path)
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if (
                                is_link(info)
                                or is_protected(path)
                                or any(path.is_relative_to(p) for p in exclusions)
                            ):
                                result.skipped += 1
                                continue
                            if stat.S_ISDIR(info.st_mode):
                                stack.append(path)
                                continue
                            if not stat.S_ISREG(info.st_mode):
                                result.skipped += 1
                                continue
                            if os.name == "nt":
                                # DirEntry.stat() omits Windows device/inode identity.
                                # lstat() records the identity needed for removal checks.
                                info = path.lstat()
                                if is_link(info) or not stat.S_ISREG(info.st_mode):
                                    result.skipped += 1
                                    continue
                            result.examined += 1
                            if info.st_size < options.min_size_bytes:
                                continue
                            if now - info.st_mtime < options.min_age_days * 86400:
                                continue
                            kind = classify(path, options, info.st_size)
                            if kind:
                                result.candidates.append(
                                    Candidate.from_stat(path, root, info, *kind, now)
                                )
                                if len(result.candidates) >= options.max_candidates:
                                    result.truncated = True
                                    break
                        except OSError as exc:
                            result.skipped += 1
                            if len(result.errors) < 100:
                                result.errors.append(f"{path}: {exc}")
                        if progress and time.monotonic() - last_progress >= 0.15:
                            progress(result.examined, len(result.candidates), folder)
                            last_progress = time.monotonic()
            except OSError as exc:
                result.skipped += 1
                if len(result.errors) < 100:
                    result.errors.append(f"{folder}: {exc}")
            if result.truncated or result.cancelled:
                break
        if result.truncated or result.cancelled:
            break
    result.candidates.sort(key=lambda item: (-item.size, str(item.path)))
    result.elapsed = time.monotonic() - started
    return result
