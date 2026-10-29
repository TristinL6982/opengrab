"""Antivirus scan on completion (M11h): optional, off by default, never blocks a download.

Uses Windows Defender (MpCmdRun.exe -Scan -ScanType 3 -File <path>) when present, or a
user-configured scanner command line with a {path} placeholder. The process runner and the
file-existence check are injected so the module stays pure and testable.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Callable

DEFENDER_PATHS = [
    r"C:\Program Files\Windows Defender\MpCmdRun.exe",
    r"C:\ProgramData\Microsoft\Windows Defender\Platform\MpCmdRun.exe",
]

CLEAN = "clean"
INFECTED = "infected"
ERROR = "error"
SKIPPED = "skipped"

# runner(argv, timeout_s) -> (returncode, combined_output)
Runner = Callable[[list, float], "tuple[int, str]"]


@dataclass
class ScanConfig:
    enabled: bool = False
    scanner: str = "defender"  # "defender" or "custom"
    custom_command: str = ""   # e.g. 'C:\\eset\\ecls.exe /clean-mode=none "{path}"'
    infected_codes: list[int] = field(default_factory=lambda: [2])
    timeout_s: float = 300.0
    quarantine: bool = False

    def __post_init__(self) -> None:
        if self.scanner not in ("defender", "custom"):
            raise ValueError(f"unknown scanner: {self.scanner}")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "scanner": self.scanner,
                "custom_command": self.custom_command,
                "infected_codes": list(self.infected_codes),
                "timeout_s": self.timeout_s, "quarantine": self.quarantine}


def config_from_dict(d: dict | None) -> ScanConfig:
    d = d or {}
    return ScanConfig(enabled=bool(d.get("enabled", False)),
                      scanner=str(d.get("scanner", "defender")),
                      custom_command=str(d.get("custom_command", "")),
                      infected_codes=[int(c) for c in d.get("infected_codes", [2])],
                      timeout_s=float(d.get("timeout_s", 300.0)),
                      quarantine=bool(d.get("quarantine", False)))


@dataclass
class ScanResult:
    path: str
    status: str
    code: int | None = None
    detail: str = ""

    @property
    def flagged(self) -> bool:
        return self.status == INFECTED


def find_defender(exists: Callable[[str], bool]) -> str | None:
    for p in DEFENDER_PATHS:
        if exists(p):
            return p
    return None


def build_command(cfg: ScanConfig, path: str, exists: Callable[[str], bool]) -> list[str] | None:
    """Return argv for scanning path, or None when no scanner is available."""
    if cfg.scanner == "custom":
        if not cfg.custom_command.strip():
            return None
        parts = shlex.split(cfg.custom_command, posix=False)
        parts = [p.strip('"') for p in parts]
        if not any("{path}" in p for p in parts):
            parts.append("{path}")
        return [p.replace("{path}", path) for p in parts]
    exe = find_defender(exists)
    if exe is None:
        return None
    return [exe, "-Scan", "-ScanType", "3", "-File", path, "-DisableRemediation"]


def interpret(cfg: ScanConfig, path: str, code: int, output: str) -> ScanResult:
    text = (output or "").strip()
    if code == 0:
        return ScanResult(path, CLEAN, code, text)
    if code in cfg.infected_codes:
        return ScanResult(path, INFECTED, code, text)
    return ScanResult(path, ERROR, code, text)


class Scanner:
    def __init__(self, cfg: ScanConfig, runner: Runner,
                 exists: Callable[[str], bool]) -> None:
        self.cfg = cfg
        self.runner = runner
        self.exists = exists
        self.history: list[ScanResult] = []

    def scan(self, path: str) -> ScanResult:
        """Scan one finished file. Never raises; failures become ERROR/SKIPPED results."""
        if not self.cfg.enabled:
            res = ScanResult(path, SKIPPED, None, "scanning disabled")
        elif not self.exists(path):
            res = ScanResult(path, ERROR, None, "file not found")
        else:
            argv = build_command(self.cfg, path, self.exists)
            if argv is None:
                res = ScanResult(path, SKIPPED, None, "no scanner available")
            else:
                try:
                    code, out = self.runner(argv, self.cfg.timeout_s)
                    res = interpret(self.cfg, path, code, out)
                except Exception as exc:  # timeouts or missing exe must not block the download
                    res = ScanResult(path, ERROR, None, f"{type(exc).__name__}: {exc}")
        self.history.append(res)
        return res

    def flagged(self) -> list[ScanResult]:
        return [r for r in self.history if r.flagged]