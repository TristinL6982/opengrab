"""Pure, deterministic Packagizer rule validation, preview, and application."""
from __future__ import annotations

import fnmatch
import re
import string
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator


class PackagizerRule(BaseModel):
    id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex)
    name: str = ""
    pattern: str
    pattern_type: str = "glob"  # glob | regex
    target_subdir: str = ""
    filename_template: str = ""
    enabled: bool = True

    @field_validator("pattern_type")
    @classmethod
    def check_pattern_type(cls, value: str) -> str:
        if value not in {"glob", "regex"}:
            raise ValueError("pattern_type must be glob or regex")
        return value

    @field_validator("pattern")
    @classmethod
    def check_pattern(cls, value: str, info) -> str:
        if not value or len(value) > 1024:
            raise ValueError("pattern must contain 1 to 1024 characters")
        if info.data.get("pattern_type") == "regex":
            try:
                re.compile(value, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from exc
        return value

    @model_validator(mode="after")
    def check_action(self):
        safe_subdir(self.target_subdir)
        if not self.target_subdir and not self.filename_template:
            raise ValueError("a rule must rename the file or set a target subfolder")
        if len(self.filename_template) > 200:
            raise ValueError("filename template must be at most 200 characters")
        _validate_template(self.filename_template)
        _validate_template(self.target_subdir)
        return self


def safe_subdir(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    if raw.startswith("/") or re.match(r"^[a-zA-Z]:", raw):
        raise ValueError("target subfolder must be relative to the download directory")
    normalized = raw.strip(" /")
    pure = PurePosixPath(normalized) if normalized else PurePosixPath()
    devices = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
               *(f"LPT{i}" for i in range(1, 10))}
    if any(part in {"", ".", ".."} or ":" in part or part.rstrip(" .") != part
           or part.split(".")[0].upper() in devices for part in pure.parts):
        raise ValueError("target subfolder must be a safe relative path")
    return pure.as_posix() if normalized else ""


def safe_filename(value: str) -> str:
    name = str(value or "download").replace("\\", "/").split("/")[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:200]
    if not name or name.split(".")[0].upper() in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}:
        return "download"
    return name


def _validate_template(template: str) -> None:
    allowed = {"name", "stem", "ext", "host"}
    try:
        for _, field_name, format_spec, conversion in string.Formatter().parse(template):
            if field_name is not None and (
                    field_name not in allowed or format_spec or conversion):
                raise ValueError("template fields: {name}, {stem}, {ext}, {host}")
    except ValueError as exc:
        if "template fields" in str(exc):
            raise
        raise ValueError("invalid filename or folder template") from exc


def matching_rule(rules: list[dict], url: str, filename: str) -> PackagizerRule | None:
    for raw in rules:
        try:
            rule = PackagizerRule.model_validate(raw)
        except Exception:
            continue
        if not rule.enabled:
            continue
        haystack = f"{url}\n{filename}"
        if rule.pattern_type == "glob":
            matched = (fnmatch.fnmatchcase(filename.casefold(), rule.pattern.casefold())
                       or fnmatch.fnmatchcase(url.casefold(), rule.pattern.casefold()))
        else:
            matched = re.search(rule.pattern, haystack, re.IGNORECASE) is not None
        if matched:
            return rule
    return None


def plan_packagizer(rules: list[dict], url: str, filename: str,
                    download_dir: str | Path) -> dict:
    original = safe_filename(filename or Path(urlparse(url).path).name)
    rule = matching_rule(rules, url, original)
    target_name, target_subdir = original, ""
    if rule:
        parsed = Path(original)
        host = safe_filename(urlparse(url).hostname or "")
        values = {"name": original, "stem": parsed.stem,
                  "ext": parsed.suffix.lstrip("."), "host": host}
        try:
            target_subdir = safe_subdir(rule.target_subdir.format_map(values))
        except (KeyError, ValueError) as exc:
            raise ValueError("invalid target subfolder template") from exc
        if rule.filename_template:
            try:
                rendered = rule.filename_template.format_map(values)
            except (KeyError, ValueError) as exc:
                raise ValueError("invalid filename template") from exc
            target_name = safe_filename(rendered)

    root = Path(download_dir).expanduser().resolve()
    relative = Path(target_subdir) / target_name if target_subdir else Path(target_name)
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("packagizer target escapes the download directory") from exc
    planned = target
    collision = target.exists()
    index = 1
    while planned.exists():
        planned = target.with_name(f"{target.stem}_{index}{target.suffix}")
        index += 1
        if index > 10000:
            raise ValueError("too many filename collisions")
    return {
        "matched": rule is not None,
        "rule_id": rule.id if rule else "",
        "rule_name": rule.name if rule else "",
        "original_filename": original,
        "filename": planned.name,
        "subdir": target_subdir,
        "relative_path": planned.relative_to(root).as_posix(),
        "collision_exists": collision,
        "collision_note": "An existing file will be preserved; a numbered name is planned."
        if collision else "",
    }
