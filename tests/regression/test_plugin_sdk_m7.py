import json
from pathlib import Path

import pytest

import opengrab.plugins as plugins_pkg
from opengrab.plugins.declarative import TemplateError
from opengrab.plugins.sdk import check_url, validate_template, validate_template_file

VALID = {
    "id": "t1",
    "name": "T1",
    "site": "t1.invalid",
    "url_pattern": "^https?://t1\\.invalid/f/",
    "direct_url_regex": "href=\"(https?://dl\\.t1\\.invalid/[^\"]+)\"",
}


def test_validate_template_valid():
    assert validate_template(VALID) == []


def test_validate_template_unknown_key():
    result = validate_template({**VALID, "bogus": 1})
    assert len(result) == 1
    assert "unknown key(s): bogus" in result[0]


def test_validate_template_invalid_url_pattern():
    result = validate_template({**VALID, "url_pattern": "("})
    assert len(result) == 1
    assert result[0].startswith("invalid url_pattern regex")


def test_validate_template_file_valid(tmp_path):
    p = tmp_path / "valid.json"
    p.write_text(json.dumps(VALID), encoding="utf-8")
    assert validate_template_file(p) == []
    assert validate_template_file(str(p)) == []


def test_validate_template_file_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    result = validate_template_file(p)
    assert len(result) == 1
    assert result[0].startswith("invalid JSON")


def test_validate_template_file_invalid_template(tmp_path):
    p = tmp_path / "bad_tpl.json"
    p.write_text(json.dumps({**VALID, "bogus": 1}), encoding="utf-8")
    result = validate_template_file(p)
    assert len(result) == 1
    assert "unknown key(s): bogus" in result[0]


def test_validate_template_file_missing(tmp_path):
    path = tmp_path / "missing.json"
    assert validate_template_file(path) == [f"file not found: {path}"]


def test_validate_bundled_templates():
    tdir = Path(plugins_pkg.__file__).parent / "templates"
    files = sorted(tdir.glob("*.json"))
    assert files, "no bundled templates found"
    for f in files:
        assert validate_template_file(f) == [], f.name


def test_check_url():
    assert check_url(VALID, "https://t1.invalid/f/abc") is True
    assert check_url(VALID, "https://other.invalid/f/abc") is False


def test_check_url_template_error():
    with pytest.raises(TemplateError):
        check_url({"id": "x"}, "https://t1.invalid/f/abc")