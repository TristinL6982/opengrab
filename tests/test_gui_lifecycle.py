"""Exercise GUI/tray lifecycle without opening a real desktop window."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pystray
import webview

from opengrab.app import run_gui_mode
from opengrab.settings import Settings


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, callback):
        self.handlers.append(callback)
        return self

    def fire(self):
        return self.handlers[-1]() if self.handlers else None


class FakeWindow:
    def __init__(self):
        self.events = SimpleNamespace(closing=Event())
        self.hidden = False
        self.destroyed = False

    def hide(self):
        self.hidden = True

    def show(self):
        self.hidden = False

    def restore(self):
        pass

    def destroy(self):
        self.events.closing.fire()
        self.destroyed = True


class FakeMenuItem:
    def __init__(self, text, action, **kwargs):
        self.text = text
        self.action = action
        self.kwargs = kwargs


class FakeIcon:
    instance = None

    def __init__(self, *args, menu=None, **kwargs):
        self.menu = menu
        self.stopped = False
        FakeIcon.instance = self

    def run_detached(self):
        pass

    def update_menu(self):
        pass

    def stop(self):
        self.stopped = True


def main() -> int:
    temp = Path(tempfile.mkdtemp(prefix="opengrab-gui-test-"))
    old_data = os.environ.get("OPENGRAB_DATA_DIR")
    os.environ["OPENGRAB_DATA_DIR"] = str(temp / "data")
    settings = Settings(download_dir=str(temp / "downloads"), api_port=0)
    window = FakeWindow()

    def create_window(*args, **kwargs):
        return window

    def start_webview():
        assert window.events.closing.fire() is False
        open_item = next(item for item in FakeIcon.instance.menu
                         if item.text == "Open OpenGrab")
        open_item.action(FakeIcon.instance, open_item)
        assert not window.hidden
        assert window.events.closing.fire() is False
        monitor_item = next(item for item in FakeIcon.instance.menu
                            if item.text == "Monitor copied links")
        monitor_item.action(FakeIcon.instance, monitor_item)
        assert settings.clipboard_monitor_enabled
        assert monitor_item.kwargs["checked"](monitor_item)
        exit_item = next(item for item in FakeIcon.instance.menu
                         if item.text == "Exit")
        exit_item.action(FakeIcon.instance, exit_item)

    try:
        with patch.object(webview, "create_window", create_window), \
                patch.object(webview, "start", start_webview), \
                patch.object(pystray, "Icon", FakeIcon), \
                patch.object(pystray, "Menu", lambda *items: items), \
                patch.object(pystray, "MenuItem", FakeMenuItem):
            run_gui_mode(settings, host="127.0.0.1", port=0)
        assert window.hidden and window.destroyed
        assert FakeIcon.instance.stopped
        print("GUI close-to-tray and tray-exit lifecycle: PASS")
        return 0
    finally:
        if old_data is None:
            os.environ.pop("OPENGRAB_DATA_DIR", None)
        else:
            os.environ["OPENGRAB_DATA_DIR"] = old_data


if __name__ == "__main__":
    raise SystemExit(main())
