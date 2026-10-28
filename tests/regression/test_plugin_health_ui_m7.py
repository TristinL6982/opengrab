import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC = os.path.join(ROOT, "src", "opengrab", "ui", "static")

def _read(name):
    with open(os.path.join(STATIC, name), "r", encoding="utf-8") as f:
        return f.read()

def test_index_html():
    content = _read("index.html")
    assert 'id="plugin-health-body"' in content
    assert content.index('id="plugin-health-body"') < content.index("<h2>Local API</h2>")
    # The index.html must only have one instance of the tag
    assert content.count('id="plugin-health-body"') == 1

def test_app_js():
    content = _read("app.js")
    assert "async function loadPluginHealth()" in content
    assert 'api("/api/plugins")' in content
    assert 'esc(h[id].last_error' in content
    
    # Find the relevant section and ensure the loadPluginHealth() call is inside the btn-set-reload-plugins handler
    # The btn-set-reload-plugins handler starts with this line:
    reload_handler_start = '"#btn-set-reload-plugins"'
    # And ends before the diagnostics line:
    diagnostics_start = '"#btn-diagnostics-export"'

    start_index = content.find(reload_handler_start)
    end_index = content.find(diagnostics_start)
    
    assert start_index != -1, "#btn-set-reload-plugins handler not found"
    assert end_index != -1, "#btn-diagnostics-export not found"
    
    # Extract the context between these handlers
    handler_content = content[start_index:end_index]
    
    # Check that loadPluginHealth is in this section (meaning it's within the reload plugins handler)
    assert "loadPluginHealth()" in handler_content
