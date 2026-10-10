import os
import sys
from types import SimpleNamespace

import pytest

from disk_sweep.clipboard import read_clipboard_text


@pytest.mark.skipif(os.name != "nt", reason="Windows clipboard adapter")
@pytest.mark.parametrize("has_text", [True, False])
def test_windows_clipboard_is_read_and_closed(monkeypatch, has_text):
    calls = []
    adapter = SimpleNamespace(
        CF_UNICODETEXT=13,
        OpenClipboard=lambda: calls.append("open"),
        CloseClipboard=lambda: calls.append("close"),
        IsClipboardFormatAvailable=lambda _: has_text,
        GetClipboardData=lambda _: "D:/folder with spaces",
    )
    monkeypatch.setitem(sys.modules, "win32clipboard", adapter)
    assert read_clipboard_text() == ("D:/folder with spaces" if has_text else "")
    assert calls == ["open", "close"]


@pytest.mark.skipif(os.name != "nt", reason="Windows clipboard adapter")
def test_windows_clipboard_is_closed_after_read_failure(monkeypatch):
    calls = []

    def fail(_):
        raise OSError("Clipboard fixture unavailable")

    adapter = SimpleNamespace(
        CF_UNICODETEXT=13,
        OpenClipboard=lambda: calls.append("open"),
        CloseClipboard=lambda: calls.append("close"),
        IsClipboardFormatAvailable=lambda _: True,
        GetClipboardData=fail,
    )
    monkeypatch.setitem(sys.modules, "win32clipboard", adapter)
    with pytest.raises(RuntimeError, match="Clipboard unavailable"):
        read_clipboard_text()
    assert calls == ["open", "close"]
