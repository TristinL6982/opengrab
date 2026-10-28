"""WebView2 runtime check for OpenGrab."""

import os
import sys
import ctypes
from pathlib import Path


def _get_winreg():
    """Safely get winreg module if available."""
    try:
        return __import__('winreg')
    except ImportError:
        return None


def webview2_version() -> str | None:
    """Get the installed WebView2 version.
    
    Returns:
        The version string, or None if not found or not on Windows.
    """
    # Early return if not on Windows
    if os.name != "nt":
        return None

    # Import only when needed on Windows
    winreg = _get_winreg()
    if not winreg:
        return None

    keys = [
        r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        r"Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    ]
    
    for key in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as reg_key:
                pv, _ = winreg.QueryValueEx(reg_key, "pv")
                if pv and pv != "0.0.0.0":
                    return pv
        except OSError:
            continue
    return None


DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def ensure_webview2() -> bool:
    """Ensure WebView2 is available.
    
    Returns:
        True if WebView2 is available, False otherwise.
    """
    # Early return if not on Windows
    if os.name != "nt":
        return True
    
    version = webview2_version()
    if version is not None:
        return True

    # WebView2 is missing
    import logging
    
    _log = logging.getLogger(__name__)
    
    _log.error("Microsoft Edge WebView2 Runtime is missing.")
    try:
        ctypes.windll.user32.MessageBoxW(None, 
            f"Microsoft Edge WebView2 Runtime is missing.\n\n"
            f"Please download and install it from:\n\n{DOWNLOAD_URL}",
            "OpenGrab", 0x10)
    except Exception:
        # If MessageBox fails, just log it
        _log.error("Failed to show WebView2 Runtime message box.", exc_info=True)
    
    return False
