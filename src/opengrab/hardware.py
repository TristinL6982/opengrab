"""Weak-hardware detection (spec section 2).

The baseline machine is a 2014 dual-core laptop with 4 GB RAM. Local AI
captcha solving is skipped when the machine has less than about 3.5 GB of
RAM or fewer than 2 logical cores, or when a solve took longer than
SLOW_INFERENCE_MS. The user can override the hardware check in Settings.
"""
from __future__ import annotations

import os
import sys

MIN_RAM_BYTES = int(3.5 * 1024 ** 3)
MIN_CORES = 2
SLOW_INFERENCE_MS = 4000


def total_ram_bytes() -> int:
    """Physical RAM in bytes, or 0 when it cannot be determined."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys)
            return 0
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, AttributeError):
        pass
    return 0


def profile() -> dict:
    ram = total_ram_bytes()
    cores = os.cpu_count() or 1
    reasons = []
    if ram and ram < MIN_RAM_BYTES:
        reasons.append(f"{ram / 1024 ** 3:.1f} GB RAM")
    if cores < MIN_CORES:
        reasons.append(f"{cores} CPU core")
    return {"ram_bytes": ram, "cores": cores, "weak": bool(reasons), "reasons": reasons}


def is_weak() -> bool:
    return profile()["weak"]
