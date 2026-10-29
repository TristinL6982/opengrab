import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC = os.path.join(ROOT, "src", "opengrab", "ui", "static")


def _read(name):
    with open(os.path.join(STATIC, name), "r", encoding="utf-8") as f:
        return f.read()


def test_index_has_devices_section_once_before_local_api():
    html = _read("index.html")
    for marker in ('id="devices-body"', 'id="btn-pair-new"', 'id="pair-code"', 'id="pair-expiry"'):
        assert html.count(marker) == 1, marker
    assert html.index("<h2>Paired devices</h2>") < html.index("<h2>Local API</h2>")
    assert html.index('id="devices-body"') < html.index("<h2>Local API</h2>")


def test_app_js_loads_devices_from_api():
    js = _read("app.js")
    assert js.count("async function loadDevices()") == 1
    assert 'api("/api/devices")' in js
    assert "esc(d.name)" in js and "esc(d.id)" in js


def test_app_js_pair_and_revoke_calls():
    js = _read("app.js")
    assert 'api("/api/pair/start", "POST"' in js
    assert '"/api/devices/" + encodeURIComponent(' in js
    assert '"DELETE")' in js
    assert '"#btn-pair-new"' in js and '"#devices-body"' in js


def test_load_settings_refreshes_devices():
    js = _read("app.js")
    start = js.index("async function loadSettings()")
    end = js.index('$("#btn-set-save")', start)
    assert "loadDevices();" in js[start:end]


def test_patch_is_idempotent_safe_markers():
    js = _read("app.js")
    assert js.count("async function pollTerminal()") == 1
    assert js.count("paired devices */") == 1