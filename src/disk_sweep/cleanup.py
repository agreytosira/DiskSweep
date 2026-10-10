"""Only selected, unchanged regular files may be removed. Never recurse."""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from disk_sweep.scanner import Candidate, has_link_ancestor, is_link, is_protected

RemovalMode = Literal["trash", "permanent"]


@dataclass
class CleanupResult:
    removed: list[Candidate] = field(default_factory=list)
    failed: list[tuple[Candidate, str]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def removed_bytes(self) -> int:
        return sum(item.size for item in self.removed)


def validate_candidate(item: Candidate) -> None:
    path = item.path
    if not path.is_absolute() or not item.root.is_absolute():
        raise ValueError("Paths must be absolute.")
    if has_link_ancestor(path):
        raise ValueError("File or parent became a symlink, junction, or reparse point.")
    resolved = path.resolve(strict=True)
    if resolved != path or not resolved.is_relative_to(item.root.resolve(strict=True)):
        raise ValueError("File is outside the scanned folder.")
    if is_protected(resolved):
        raise ValueError("File is in a protected folder.")
    info = path.lstat()
    if is_link(info) or not stat.S_ISREG(info.st_mode):
        raise ValueError("Only regular files may be removed.")
    actual = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_dev, info.st_ino)
    expected = (item.size, item.mtime_ns, item.ctime_ns, item.device, item.inode)
    if actual != expected:
        raise ValueError("File changed since the scan. Rescan before selecting it again.")


def native_trash(path: str) -> None:
    from send2trash import send2trash

    if os.name == "nt" and send2trash.__module__ != "send2trash.win.modern":
        raise OSError("Native recycling requires pywin32. File kept; no permanent fallback.")
    try:
        send2trash(path)
    except Exception as exc:
        # Native adapters can raise platform-specific exceptions, including COM errors.
        raise OSError(f"OS Trash failed: {exc}") from exc


def remove_selected(
    items: Iterable[Candidate],
    mode: RemovalMode,
    *,
    confirmation: str,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
    trash: Callable[[str], None] | None = None,
) -> CleanupResult:
    if mode not in {"trash", "permanent"}:
        raise ValueError("Choose trash or permanent removal.")
    required = "DELETE" if mode == "permanent" else "TRASH"
    if confirmation != required:
        raise ValueError(f"Removal requires explicit {required} confirmation.")
    if trash is None and mode == "trash":
        trash = native_trash
    selected = list({item.path: item for item in items}.values())
    result = CleanupResult()
    for index, item in enumerate(selected):
        if cancel is not None and cancel.is_set():
            result.cancelled = True
            break
        try:
            validate_candidate(item)
            if mode == "trash":
                assert trash is not None
                trash(str(item.path))
            else:
                if os.unlink in os.supports_dir_fd:
                    fd = os.open(item.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        validate_candidate(item)
                        current = os.stat(item.path.name, dir_fd=fd, follow_symlinks=False)
                        if (current.st_dev, current.st_ino) != (item.device, item.inode):
                            raise ValueError("File was replaced during cleanup.")
                        os.unlink(item.path.name, dir_fd=fd)
                    finally:
                        os.close(fd)
                else:
                    item.path.unlink()
            result.removed.append(item)
        except (OSError, ValueError) as exc:
            result.failed.append((item, str(exc)))
        if progress:
            progress(index + 1, len(selected))
    return result
