"""M5c: rotating log file and WebView2 runtime check."""

import logging
import os
import shutil
import tempfile
from pathlib import Path

import opengrab.logs as logs_mod
import opengrab.webview2 as wv


def _file_handlers(path):
    target = str(Path(path).resolve())
    return [h for h in logging.getLogger().handlers
            if getattr(h, "baseFilename", None) and str(Path(h.baseFilename).resolve()) == target]


def test_setup_logging(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(logs_mod, "app_data_dir", lambda: tmp)
    log_file = None
    try:
        log_file = logs_mod.setup_logging()
        assert Path(log_file) == Path(tmp) / "logs" / "opengrab.log"
        assert Path(log_file).exists()
        logging.getLogger("opengrab.test").warning("hello-m5c")
        for h in _file_handlers(log_file):
            h.flush()
        assert "hello-m5c" in Path(log_file).read_text(encoding="utf-8")
        logs_mod.setup_logging()
        assert len(_file_handlers(log_file)) == 1
    finally:
        if log_file is not None:
            for h in _file_handlers(log_file):
                logging.getLogger().removeHandler(h)
                h.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_webview2_version_none_when_registry_missing(monkeypatch):
    if os.name != "nt":
        assert wv.webview2_version() is None
        return

    def boom(*a, **k):
        raise OSError("missing")

    monkeypatch.setattr(wv.winreg, "OpenKey", boom)
    assert wv.webview2_version() is None


def test_webview2_version_none_off_windows(monkeypatch):
    monkeypatch.setattr(wv.os, "name", "posix")
    assert wv.webview2_version() is None


def test_ensure_webview2_missing_shows_box_once(monkeypatch):
    calls = []
    monkeypatch.setattr(wv.os, "name", "nt")
    monkeypatch.setattr(wv, "webview2_version", lambda: None)
    monkeypatch.setattr(wv, "_message_box", lambda text: calls.append(text))
    assert wv.ensure_webview2() is False
    assert len(calls) == 1 and wv.DOWNLOAD_URL in calls[0]


def test_ensure_webview2_present(monkeypatch):
    calls = []
    monkeypatch.setattr(wv.os, "name", "nt")
    monkeypatch.setattr(wv, "webview2_version", lambda: "120.0.0.0")
    monkeypatch.setattr(wv, "_message_box", lambda text: calls.append(text))
    assert wv.ensure_webview2() is True
    assert calls == []
