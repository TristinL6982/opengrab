import hashlib
import json
from pathlib import Path

import pytest

from opengrab.plugins.sdk import validate_template


def test_version_tuple():
    from opengrab.plugins.updates import version_tuple
    
    assert version_tuple("1.2.10") == (1, 2, 10)
    assert version_tuple("2.x") == (2, 0)
    assert version_tuple("1.10") > version_tuple("1.9")


def test_plan_updates():
    from opengrab.plugins.updates import plan_updates
    
    installed = {"host-a": "1.0", "host-b": "2.0"}
    index = {
        "templates": [
            {"id": "host-a", "version": "1.1", "url": "a.json", "sha256": "0" * 64},
            {"id": "host-b", "version": "2.0", "url": "b.json", "sha256": "0" * 64},
            {"id": "host-c", "version": "0.1", "url": "c.json", "sha256": "0" * 64},
            {"id": "host-d", "version": "0.1", "url": "d.json"}  # missing sha256
        ]
    }
    
    result = plan_updates(installed, index)
    assert len(result) == 2
    assert [r["id"] for r in result] == ["host-a", "host-c"]


def test_apply_update():
    from opengrab.plugins.updates import apply_update
    
    # Build a valid template dict by loading example_host.json and changing its "id" to "host-a"
    tpl = json.loads(Path("src/opengrab/plugins/templates/example_host.json").read_text(encoding="utf-8"))
    tpl["id"] = "host-a"
    data = json.dumps(tpl).encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    
    # This should work
    with pytest.raises(ValueError, match="sha256 mismatch"):
        apply_update({"id": "host-a", "version": "1.0", "url": "a.json", "sha256": "0" * 64}, data, "/tmp")
    
    # Valid case
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = apply_update({"id": "host-a", "version": "1.0", "url": "a.json", "sha256": sha}, data, tmp_dir)
        assert path.name == "host-a.json"
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["id"] == "host-a"


def test_apply_update_sha256_mismatch():
    from opengrab.plugins.updates import apply_update
    
    tpl = json.loads(Path("src/opengrab/plugins/templates/example_host.json").read_text(encoding="utf-8"))
    tpl["id"] = "host-a"
    data = json.dumps(tpl).encode("utf-8")
    
    with pytest.raises(ValueError, match="sha256 mismatch"):
        apply_update({"id": "host-a", "version": "1.0", "url": "a.json", "sha256": "0" * 64}, data, "/tmp")


def test_apply_update_bad_id():
    from opengrab.plugins.updates import apply_update
    
    tpl = json.loads(Path("src/opengrab/plugins/templates/example_host.json").read_text(encoding="utf-8"))
    tpl["id"] = "host-a"
    data = json.dumps(tpl).encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    
    with pytest.raises(ValueError, match="bad template id"):
        apply_update({"id": "../evil", "version": "1.0", "url": "a.json", "sha256": sha}, data, "/tmp")


def test_apply_update_id_mismatch():
    from opengrab.plugins.updates import apply_update
    
    tpl = json.loads(Path("src/opengrab/plugins/templates/example_host.json").read_text(encoding="utf-8"))
    tpl["id"] = "host-a"
    data = json.dumps(tpl).encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    
    with pytest.raises(ValueError, match="id mismatch"):
        apply_update({"id": "host-z", "version": "1.0", "url": "a.json", "sha256": sha}, data, "/tmp")


def test_apply_update_invalid_template():
    from opengrab.plugins.updates import apply_update
    
    # This will be a valid template with no "id" field
    bad_data = b'{"name": "Test"}'
    sha = hashlib.sha256(bad_data).hexdigest()
    
    with pytest.raises(ValueError, match="invalid template"):
        apply_update({"id": "host-a", "version": "1.0", "url": "a.json", "sha256": sha}, bad_data, "/tmp")


def test_check_and_update():
    from opengrab.plugins.updates import check_and_update, plan_updates
    
    # Create a simpler valid template for testing
    tpl_a = {
        "id": "host-a",
        "name": "Test Host A",
        "site": "test-a.invalid",
        "description": "Test template",
        "url_pattern": "^https?://test-a\\\\.invalid/",
        "direct_url_regex": "href=\\\"(https?://dl\\\\.test-a\\\\.invalid/[^\\\\\\\"]+)\\\"",
        "filename_regex": "<h1>([^<]+)</h1>",
        "offline_markers": ["File not found"]
    }

    data_a = json.dumps(tpl_a).encode("utf-8")
    sha_a = hashlib.sha256(data_a).hexdigest()

    index = {
        "templates": [
            {"id": "host-a", "version": "1.1", "url": "a.json", "sha256": sha_a}
        ]
    }

    index_bytes = json.dumps(index).encode("utf-8")

    def fake_fetch(url):
        if url == "https://idx/index.json":
            return index_bytes
        elif url == "https://idx/a.json":
            return data_a
        else:
            raise ValueError("Unknown URL")

    # Simple test with the key assertion that one item should be returned from plan_updates
    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Just make sure this works correctly
        installed = {"host-a": "1.0"}  # host-a is already installed at version 1.0

        # The critical check is that we should be finding a candidate to update
        plan_result = plan_updates(installed, index)
        assert len(plan_result) == 1  # Should want to update host-a
        assert plan_result[0]["id"] == "host-a"

        # Now test the full integration
        result = check_and_update(installed, "https://idx/index.json", fake_fetch, tmp_dir)

        # This should pass - it's updating successfully
        assert result["updated"] == ["host-a"]
        assert list(result["failed"]) == []
        
        # Check file was written correctly
        assert (Path(tmp_dir) / "host-a.json").exists()