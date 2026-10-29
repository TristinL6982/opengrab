"""Build store-ready OpenGrab browser extension zips (M12i).

Produces two archives from extension/:
  opengrab-chromium-<version>.zip  (Chrome, Edge, Opera, Brave, Vivaldi; MV3 service worker)
  opengrab-firefox-<version>.zip   (Firefox; MV3 background scripts + browser_specific_settings)

Usage: python tools/build_extension.py [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT_DIR = ROOT / "extension"
DEFAULT_OUT = ROOT / "dist" / "extension"
GECKO_ID = "opengrab@opengrab.app"
GECKO_MIN = "115.0"
IMPORT_LINE = 'importScripts("rules.js");'
FIREFOX_IMPORT_NOTE = "// rules.js is loaded first via manifest background.scripts in the Firefox build."
SKIP_DIRS = {"__pycache__", "node_modules", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".zip", ".map"}


def extension_files(ext_dir: Path = EXT_DIR) -> list[Path]:
    files = []
    for p in sorted(ext_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ext_dir)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.suffix.lower() in SKIP_SUFFIXES or p.name.startswith("."):
            continue
        files.append(p)
    return files


def firefox_manifest(manifest: dict) -> dict:
    m = json.loads(json.dumps(manifest))
    m.pop("minimum_chrome_version", None)
    m["background"] = {"scripts": ["rules.js", "background.js"]}
    page = m.pop("options_page", None)
    if page:
        m["options_ui"] = {"page": page, "open_in_tab": True}
    m["browser_specific_settings"] = {
        "gecko": {"id": GECKO_ID, "strict_min_version": GECKO_MIN}
    }
    return m


def firefox_background(text: str) -> str:
    return text.replace(IMPORT_LINE, FIREFOX_IMPORT_NOTE, 1)


def build(out_dir: Path | str = DEFAULT_OUT, ext_dir: Path = EXT_DIR) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
    version = manifest.get("version", "0.0.0")
    files = extension_files(ext_dir)
    chromium = out / f"opengrab-chromium-{version}.zip"
    firefox = out / f"opengrab-firefox-{version}.zip"
    with zipfile.ZipFile(chromium, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, p.relative_to(ext_dir).as_posix())
    with zipfile.ZipFile(firefox, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            name = p.relative_to(ext_dir).as_posix()
            if name == "manifest.json":
                z.writestr(name, json.dumps(firefox_manifest(manifest), indent=2) + "\n")
            elif name == "background.js":
                z.writestr(name, firefox_background(p.read_text(encoding="utf-8")))
            else:
                z.write(p, name)
    return {"version": version, "chromium": str(chromium), "firefox": str(firefox)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build OpenGrab extension zips")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    res = build(args.out)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())