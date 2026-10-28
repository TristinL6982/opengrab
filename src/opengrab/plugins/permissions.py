"""
Plugin permissions: what a plugin may access, and checks the sandbox enforces.
"""

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


class PermissionError_(Exception):
    pass


@dataclass
class Permissions:
    hosts: list = field(default_factory=list)
    files: bool = False
    cookies: bool = False

    def allows_url(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return True if any(fnmatch.fnmatch(host, p.lower()) for p in self.hosts) else False

    def check_url(self, url: str) -> None:
        if not self.allows_url(url):
            raise PermissionError_("host not allowed: " + (urlparse(url).hostname or ""))

    def check_files(self) -> None:
        if not self.files:
            raise PermissionError_("file access not allowed")

    def check_cookies(self) -> None:
        if not self.cookies:
            raise PermissionError_("cookie access not allowed")

    def to_dict(self) -> dict:
        return {"hosts": list(self.hosts), "files": self.files, "cookies": self.cookies}


def parse_permissions(data: dict) -> Permissions:
    hosts = data.get("hosts", [])
    if not isinstance(hosts, list) or not all(isinstance(h, str) and h.strip() for h in hosts):
        raise ValueError("hosts must be a list of non-empty strings")
    for key in ("files", "cookies"):
        if key in data and not isinstance(data[key], bool):
            raise ValueError(key + " must be a boolean")
    return Permissions(hosts=[h.strip() for h in hosts], files=data.get("files", False), cookies=data.get("cookies", False))


def requested_beyond(requested: Permissions, granted: Permissions) -> list:
    result = []
    for h in requested.hosts:
        if h not in granted.hosts:
            result.append("host:" + h)
    if requested.files and not granted.files:
        result.append("files")
    if requested.cookies and not granted.cookies:
        result.append("cookies")
    return sorted(result)


def load_grants(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {pid: parse_permissions(d) for pid, d in raw.items()}


def save_grants(path, grants: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({pid: g.to_dict() for pid, g in sorted(grants.items())}, indent=2), encoding="utf-8")