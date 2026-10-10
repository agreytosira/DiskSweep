"""Read plain text from the local OS clipboard without changing it."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def read_clipboard_text() -> str:
    try:
        if os.name == "nt":
            import win32clipboard

            win32clipboard.OpenClipboard()
            try:
                if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                    return ""
                return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
        if sys.platform == "darwin":
            command = ["pbpaste"]
        elif os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
            command = ["wl-paste", "--no-newline"]
        elif shutil.which("xclip"):
            command = ["xclip", "-selection", "clipboard", "-out"]
        elif shutil.which("xsel"):
            command = ["xsel", "--clipboard", "--output"]
        else:
            raise RuntimeError("No clipboard reader is available.")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1,
            check=True,
        )
        return result.stdout
    except Exception as exc:
        raise RuntimeError("Clipboard unavailable. Use your terminal's Paste shortcut.") from exc
