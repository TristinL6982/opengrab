"""WebView2 runtime check for OpenGrab."""

import ctypes
import logging
import os

try:
    import winreg  # Windows only
except ImportError:  # pragma: no cover - non-Windows
    winreg = None

_log = logging.getLogger(__name__)

_CLIENT = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def _registry_keys():
    return [
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + _CLIENT),
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + _CLIENT),
        (winreg.HKEY_CURRENT_USER, "Software\\" + _CLIENT),
    ]


def webview2_version() -> str | None:
    """Return the installed WebView2 Runtime version, or None if missing or not on Windows."""
    if os.name != "nt" or winreg is None:
        return None
    for hive, key in _registry_keys():
        try:
            with winreg.OpenKey(hive, key) as reg_key:
                pv, _ = winreg.QueryValueEx(reg_key, "pv")
        except OSError:
            continue
        if pv and pv != "0.0.0.0":
            return str(pv)
    return None


def _message_box(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "OpenGrab", 0x10)


def ensure_webview2() -> bool:
    """True if WebView2 is available (or not needed); otherwise tell the user and return False."""
    if os.name != "nt":
        return True
    if webview2_version() is not None:
        return True
    _log.error("Microsoft Edge WebView2 Runtime is missing; download it from %s", DOWNLOAD_URL)
    try:
        _message_box(
            "The Microsoft Edge WebView2 Runtime is missing, so OpenGrab can't open its window.\n\n"
            f"Download and install it from:\n{DOWNLOAD_URL}"
        )
    except Exception:
        _log.error("Could not show the WebView2 message box", exc_info=True)
    return False
