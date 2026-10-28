"""
Single instance lock and URL forwarding for OpenGrab.
"""

import json
import os
import tempfile
import urllib.request
import urllib.error
from typing import Optional

if os.name == "nt":  # Windows
    import ctypes
    from ctypes import wintypes
    
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    
    # Error code for ERROR_ALREADY_EXISTS
    ERROR_ALREADY_EXISTS = 183
else:
    import fcntl


def acquire(name: str = "OpenGrab-SingleInstance") -> Optional[object]:
    """
    Acquire a single instance lock.
    
    Args:
        name: Lock name (default: "OpenGrab-SingleInstance")
        
    Returns:
        Handle to the lock object, or None if already exists
    """
    if os.name == "nt":  # Windows
        handle = kernel32.CreateMutexW(None, False, f"Local\\{name}")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return None
        return handle
    else:  # Unix-like systems
        lock_path = os.path.join(tempfile.gettempdir(), f"{name}.lock")
        fh = open(lock_path, "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            fh.close()
            return None
        return fh


def release(handle: Optional[object]) -> None:
    """
    Release a single instance lock.
    
    Args:
        handle: The lock handle returned by acquire(), or None
    """
    if handle is None:
        return
    
    if os.name == "nt":  # Windows
        kernel32.CloseHandle(handle)
    else:  # Unix-like systems
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, AttributeError, ValueError):
            pass
        try:
            handle.close()
        except (OSError, AttributeError):
            pass


def forward_to_running(urls: list[str], host: str, port: int, token: str, base_path: str = "") -> bool:
    """
    Forward URLs to a running OpenGrab instance.
    
    Args:
        urls: List of URLs to add
        host: Host of the target instance (e.g., "127.0.0.1")
        port: Port of the target instance 
        token: API bearer token for authentication
        base_path: Optional base path prefix
        
    Returns:
        True if all URLs were added successfully, False otherwise
    """
    # First check if server is alive by pinging it
    try:
        ping_url = f"http://{host}:{port}{base_path}/api/ping"
        req = urllib.request.Request(ping_url)
        req.add_header("Authorization", f"Bearer {token}")
        response = urllib.request.urlopen(req, timeout=3)
        if response.getcode() != 200:
            return False
    except Exception:
        return False
    
    # Now add the URLs
    try:
        api_url = f"http://{host}:{port}{base_path}/api/urls"
        data = {"urls": urls}
        req = urllib.request.Request(api_url)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {token}")
        req.data = json.dumps(data).encode("utf-8")
        
        response = urllib.request.urlopen(req, timeout=3)
        return response.getcode() // 100 == 2  # Check if it's a 2xx response
    except Exception:
        return False