import pytest
import tempfile
import pathlib
import os
from opengrab.plugins.registry import PluginRegistry
from opengrab.plugins.declarative import DeclarativePlugin


def test_template_loading():
    # Test case 1: good template loads
    r = PluginRegistry()
    d = tempfile.mkdtemp()
    r.set_user_dir(pathlib.Path(d))
    
    # Write a valid JSON template file
    template_content = {
        "id":"exhost",
        "name":"ExHost",
        "site":"exhost.com",
        "direct_url_regex":r'href="([^"]+)" id="dl"'
    }
    
    with open(os.path.join(d, "good.json"), "w", encoding="utf-8") as f:
        import json
        json.dump(template_content, f)
    
    # Scan and check that template is loaded
    r.scan(force=True)
    assert r.get("exhost") is not None
    assert r.get("exhost").origin == "template"
    assert any(p.id == "exhost" for p in r.for_url("https://exhost.com/f/abc"))
    
    # Test case 2: bad JSON file
    with open(os.path.join(d, "bad.json"), "w", encoding="utf-8") as f:
        f.write('{nope": "template:bad.json"')
    
    r.scan(force=True)
    assert "template:bad.json" in r.errors
    
    # Test case 3: removing the file
    os.remove(os.path.join(d, "good.json"))
    r.scan(force=True)
    assert r.get("exhost") is None
    
    # Test case 4: fixing bad.json 
    with open(os.path.join(d, "bad.json"), "w", encoding="utf-8") as f:
        import json
        json.dump(template_content, f)
        
    r.scan(force=True)
    assert "template:bad.json" not in r.errors
    assert r.get("exhost") is not None
    
    # Test case 5: conflict with builtin plugin
    builtin_plugin_id = list(r.plugins.keys())[0]  # Get first builtin plugin ID
    # Make sure it's a builtin plugin
    if r.plugins[builtin_plugin_id].origin == "builtin":
        conflict_template = {
            "id": builtin_plugin_id,
            "name":"Conflicting Plugin",
            "site":"conflict.example",
            "direct_url_regex":r'href="([^"]+)" id="dl"'
        }
        
        with open(os.path.join(d, "conflict.json"), "w", encoding="utf-8") as f:
            import json
            json.dump(conflict_template, f)
        
        r.scan(force=True)
        
        # Check that the builtin plugin still exists and has origin "builtin"
        assert r.get(builtin_plugin_id).origin == "builtin"
        # Check that error is recorded
        assert f"template:{builtin_plugin_id}" in r.errors


if __name__ == "__main__":
    test_template_loading()
    print("All tests passed!")