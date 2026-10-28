import json
import pytest
from pathlib import Path

from opengrab.plugins.sdk import validate_template, validate_template_file, check_url
from opengrab.plugins.declarative import TemplateError


VALID = {"id": "t1", "name": "T1", "site": "t1.invalid", "url_pattern": "^https?://t1\\.invalid/f/", "direct_url_regex": "href=\"(https?://dl\\.t1\\.invalid/[^\"]+)\""}


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


def test_validate_template_file_valid():
    # Use the correct path to the test file we created
    test_file_path = Path(__file__).parent / "test_plugin_sdk_m7_valid.json"
    result = validate_template_file(test_file_path)
    assert result == []


def test_validate_template_file_invalid_json():
    # Create a temporary file with invalid JSON
    import tempfile
    import os
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("{not json")
        temp_path = f.name
    
    try:
        result = validate_template_file(temp_path)
        assert len(result) == 1
        assert result[0].startswith("invalid JSON")
    finally:
        os.unlink(temp_path)


def test_validate_template_file_missing():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "missing.json"
        result = validate_template_file(path)
        expected = f"file not found: {path}"
        assert result == [expected]


def test_validate_template_file_example():
    # Use the correct path to the example template
    example_path = Path("src/opengrab/plugins/templates/example_host.json")
    result = validate_template_file(example_path)
    assert result == []


def test_check_url():
    assert check_url(VALID, "https://t1.invalid/f/abc") is True
    assert check_url(VALID, "https://other.invalid/f/abc") is False


def test_check_url_template_error():
    with pytest.raises(TemplateError):
        check_url({"id": "x"}, "https://t1.invalid/f/abc")