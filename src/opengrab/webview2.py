"""WebView2 runtime check for OpenGrab."""

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    import winreg
    
    def webview2_version() -> str | None:
        """Get the installed WebView2 version on Windows.
        
        Returns:
            The version string, or None if not found or not on Windows.
        """
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

else:
    def webview2_version() -> str | None:
        """Not on Windows."""
        return None


DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def ensure_webview2() -> bool:
    """Ensure WebView2 is available.
    
    Returns:
        True if WebView2 is available, False otherwise.
    """
    if os.name != "nt":
        return True
    
    version = webview2_version()
    if version is not None:
        return True

    # WebView2 is missing
    import logging
    import ctypes
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
