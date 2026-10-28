import hashlib
import json
from pathlib import Path

def version_tuple(v: str) -> tuple:
    parts = str(v).split(".")
    return tuple(int(p) if p.isdigit() else 0 for p in parts)

def test_version_tuple():
    assert version_tuple("1.2.10") == (1, 2, 10)
    assert version_tuple("2.x") == (2, 0)
    assert version_tuple("1.10") > version_tuple("1.9")

def test_plan_updates():
    installed = {"host-a": "1.0", "host-b": "2.0"}
    index = {
        "templates": [
            {"id": "host-a", "version": "1.1", "url": "a.json", "sha256": "abcd"},
            {"id": "host-b", "version": "2.0", "url": "b.json", "sha256": "efgh"},
            {"id": "host-c", "version": "0.1", "url": "c.json", "sha256": "ijkl"},
            {"id": "host-d", "version": "0.1", "url": "d.json"}  # missing sha256
        ]
    }
    
    from opengrab.plugins.updates import plan_updates
    result = plan_updates(installed, index)
    
    # Only host-a (newer version) and host-c (not installed) should be returned
    assert len(result) == 2
    
    # The order is by ID due to sorting 
    ids = [entry["id"] for entry in result]
    assert "host-a" in ids
    assert "host-c" in ids

def test_apply_update():
    from opengrab.plugins.updates import apply_update
    import tempfile
    
    # Create a valid template manually for testing
    tpl = {
        "id": "host-a",
        "name": "Test Host",
        "site": "test.invalid",
        "description": "Test template",
        "url_pattern": "^https?://test\\.invalid/",
        "direct_url_regex": "href=\"(https?://dl\\.test\\.invalid/[^\\\"]+)\"",
        "filename_regex": "<h1>([^<]+)</h1>",
        "offline_markers": ["File not found"]
    }
    
    data = json.dumps(tpl).encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    
    # Test successful update
    with tempfile.TemporaryDirectory() as tmp_dir:
        entry = {"id": "host-a", "version": "1.0", "url": "a.json", "sha256": sha}
        path = apply_update(entry, data, tmp_dir)
        
        # Verify file was written
        assert path.exists()
        assert path.name == "host-a.json"
        
        # Verify contents
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["id"] == "host-a"

def test_apply_update_sha256_mismatch():
    from opengrab.plugins.updates import apply_update
    import tempfile
    
    # Create a valid template manually for testing
    tpl = {
        "id": "host-a",
        "name": "Test Host",
        "site": "test.invalid",
        "description": "Test template",
        "url_pattern": "^https?://test\\.invalid/",
        "direct_url_regex": "href=\"(https?://dl\\.test\\.invalid/[^\\\"]+)\"",
        "filename_regex": "<h1>([^<]+)</h1>",
        "offline_markers": ["File not found"]
    }
    
    data = json.dumps(tpl).encode("utf-8")
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Test SHA256 mismatch
        entry = {"id": "host-a", "version": "1.0", "url": "a.json", "sha256": "0" * 64}
        try:
            apply_update(entry, data, tmp_dir)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "sha256 mismatch" in str(e)

def test_apply_update_bad_id():
    from opengrab.plugins.updates import apply_update
    import tempfile
    
    # Create a valid template manually for testing
    tpl = {
        "id": "host-a",
        "name": "Test Host", 
        "site": "test.invalid",
        "description": "Test template",
        "url_pattern": "^https?://test\\.invalid/",
        "direct_url_regex": "href=\"(https?://dl\\.test\\.invalid/[^\\\"]+)\"",
        "filename_regex": "<h1>([^<]+)</h1>",
        "offline_markers": ["File not found"]
    }
    
    data = json.dumps(tpl).encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Test bad id
        entry = {"id": "../evil", "version": "1.0", "url": "a.json", "sha256": sha}
        try:
            apply_update(entry, data, tmp_dir)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "bad template id" in str(e)

def test_apply_update_id_mismatch():
    from opengrab.plugins.updates import apply_update
    import tempfile
    
    # Create a valid template manually for testing
    tpl = {
        "id": "host-a",
        "name": "Test Host",
        "site": "test.invalid",
        "description": "Test template",
        "url_pattern": "^https?://test\\.invalid/",
        "direct_url_regex": "href=\"(https?://dl\\.test\\.invalid/[^\\\"]+)\"",
        "filename_regex": "<h1>([^<]+)</h1>",
        "offline_markers": ["File not found"]
    }
    
    data = json.dumps(tpl).encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Test id mismatch - entry has id "host-z" but data has id "host-a"
        entry = {"id": "host-z", "version": "1.0", "url": "a.json", "sha256": sha}
        try:
            apply_update(entry, data, tmp_dir)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "id mismatch" in str(e)

def test_apply_update_invalid_template():
    from opengrab.plugins.updates import apply_update
    import tempfile
    
    # Test with invalid template (doesn't pass validation) 
    data = b'{"id": "host-a", "name": "Test"}'  # minimal valid JSON but not a proper template
    sha = hashlib.sha256(data).hexdigest()
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Test with invalid template
        entry = {"id": "host-a", "version": "1.0", "url": "a.json", "sha256": sha}
        try:
            apply_update(entry, data, tmp_dir)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "invalid template" in str(e)

def test_check_and_update():
    from opengrab.plugins.updates import check_and_update, plan_updates
    import tempfile
    
    # Create a simpler valid template for testing
    tpl_a = {
        "id": "host-a",
        "name": "Test Host A",
        "site": "test-a.invalid",
        "description": "Test template",
        "url_pattern": "^https?://test-a\\.invalid/",
        "direct_url_regex": "href=\"(https?://dl\\.test-a\\.invalid/[^\\\"]+)\"",
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
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Just make sure this works correctly 
        installed = {"host-a": "1.0"}  # host-a is already installed at version 1.0
        
        # The critical check is that we should be finding a candidate to update
        from opengrab.plugins.updates import plan_updates
        plan_result = plan_updates(installed, index)
        assert len(plan_result) == 1  # Should want to update host-a 
        assert plan_result[0]["id"] == "host-a"
        
        # Now test the full integration
        result = check_and_update(installed, "https://idx/index.json", fake_fetch, tmp_dir)
        
        # This should pass - it's updating successfully
        assert result["updated"] == ["host-a"]
        assert len(result["failed"]) == 0
        
        # Verify files were created
        host_a_path = Path(tmp_dir) / "host-a.json"
        assert host_a_path.exists() is True