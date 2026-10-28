"""
Helpers for plugin authors: validate declarative templates.
"""

import json
from pathlib import Path

from .declarative import DeclarativePlugin, TemplateError


def validate_template(tpl) -> list[str]:
    try:
        DeclarativePlugin(tpl)
        return []
    except TemplateError as e:
        return [str(e)]


def validate_template_file(path) -> list[str]:
    p = Path(path)
    if not p.is_file():
        return [f"file not found: {p}"]
    
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"invalid JSON: {e.msg} (line {e.lineno})"]
    
    return validate_template(data)


def check_url(tpl: dict, url: str) -> bool:
    return DeclarativePlugin(tpl).supports(url)