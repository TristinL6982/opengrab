r"""Generate src/opengrab/addons_lock.json from the versions installed in this venv.

For each optional add-on (currently only "ocr"), walk the installed dependency
closure of its root package, skip packages the base app already ships, and
record the matching Windows x64 / CPython 3.12 wheel with its PyPI SHA-256.
The app downloads exactly these files and refuses any whose hash differs.

    .venv\Scripts\python.exe tools\lock_addons.py
"""
from __future__ import annotations

import importlib.metadata as md
import json
import re
import sys
import urllib.request
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.tags import sys_tags
from packaging.utils import parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "opengrab" / "addons_lock.json"
ADDONS = {"ocr": {"root": "ddddocr", "provided": {"pillow"}}}
PLATFORM = "win_amd64"  # the shipped exe is Windows x64, CPython 3.12


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def closure(root: str) -> dict[str, str]:
    env = default_environment()
    env["sys_platform"], env["platform_system"] = "win32", "Windows"
    seen: dict[str, str] = {}

    def walk(name: str) -> None:
        key = norm(name)
        if key in seen:
            return
        dist = md.distribution(name)
        seen[key] = dist.version
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker and not req.marker.evaluate({**env, "extra": ""}):
                continue
            walk(req.name)

    walk(root)
    return seen


def pick_wheel(name: str, version: str) -> dict:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
        files = json.load(r)["urls"]
    wanted = [t for t in sys_tags() if t.platform in (PLATFORM, "any")
              and t.interpreter in ("cp312", "py3", "py312") or t.abi == "abi3" and t.platform == PLATFORM]
    rank = {str(t): i for i, t in enumerate(wanted)}
    best = None
    for f in files:
        if f["packagetype"] != "bdist_wheel":
            continue
        _, _, _, tags = parse_wheel_filename(f["filename"])
        score = min((rank.get(str(t), 10**6) for t in tags), default=10**6)
        if score < 10**6 and (best is None or score < best[0]):
            best = (score, f)
    if best is None:
        raise SystemExit(f"no {PLATFORM} cp312 wheel for {name}=={version}")
    f = best[1]
    return {"name": norm(name), "version": version, "filename": f["filename"],
            "url": f["url"], "sha256": f["digests"]["sha256"], "size": f["size"]}


def main() -> int:
    lock = {"python": "cp312", "platform": PLATFORM, "addons": {}}
    for addon, spec in ADDONS.items():
        pkgs = closure(spec["root"])
        wheels = [pick_wheel(n, v) for n, v in sorted(pkgs.items()) if n not in spec["provided"]]
        lock["addons"][addon] = {"root": spec["root"], "wheels": wheels,
                                 "download_bytes": sum(w["size"] for w in wheels)}
    OUT.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    for addon, data in lock["addons"].items():
        print(addon, len(data["wheels"]), "wheels,", round(data["download_bytes"] / 1e6, 1), "MB")
        for w in data["wheels"]:
            print("  ", w["filename"], round(w["size"] / 1e6, 1), "MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
