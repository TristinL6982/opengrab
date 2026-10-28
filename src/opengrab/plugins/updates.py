"""
Plugin template updates: compare a remote index with installed versions, verify and install new templates.
"""
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin
from .sdk import validate_template

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def version_tuple(v: str) -> tuple:
    parts = str(v).split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts)


def plan_updates(installed: dict, index: dict) -> list:
    result = []
    for entry in index.get("templates", []):
        # Skip entries missing any of the four keys
        if not all(k in entry for k in ("id", "version", "url", "sha256")):
            continue

        if entry["id"] not in installed:
            result.append(entry)
        elif version_tuple(entry["version"]) > version_tuple(installed[entry["id"]]):
            result.append(entry)
    return sorted(result, key=lambda x: x["id"])


def apply_update(entry: dict, data: bytes, dest_dir) -> Path:
    if not ID_RE.match(entry["id"]):
        raise ValueError("bad template id")

    if hashlib.sha256(data).hexdigest() != entry["sha256"].lower():
        raise ValueError("sha256 mismatch")

    tpl = json.loads(data.decode("utf-8"))
    errs = validate_template(tpl)
    if errs:
        raise ValueError("invalid template: " + "; ".join(errs))

    if tpl.get("id") != entry["id"]:
        raise ValueError("id mismatch")

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / (entry["id"] + ".json")
    path.write_text(json.dumps(tpl, indent=2), encoding="utf-8")
    return path


def check_and_update(installed: dict, index_url: str, fetch, dest_dir) -> dict:
    index = json.loads(fetch(index_url).decode("utf-8"))
    updated = []
    failed = {}

    for entry in plan_updates(installed, index):
        try:
            apply_update(entry, fetch(urljoin(index_url, entry["url"])), dest_dir)
            updated.append(entry["id"])
        except Exception as e:
            failed[entry["id"]] = str(e)

    return {"updated": updated, "failed": failed}
