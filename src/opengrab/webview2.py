"""WebView2 runtime check for OpenGrab."""

import os
import sys
import ctypes
from pathlib import Path

# For platform detection, prefer os.name but also handle the case of sys.platform
# to make tests work more predictably on Windows environments
def _is_windows():
    """Check if we are running on Windows."""
    return os.name == "nt" or sys.platform == "win32"

if _is_windows():
    # Import only if running on Windows
    try:
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
    except ImportError:
        # We're on Windows but winreg is not available for some reason, treat as not found
        def webview2_version() -> str | None:
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
    if not _is_windows():
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
