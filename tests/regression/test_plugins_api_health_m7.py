import tempfile
import os
import json
import urllib.request
from pathlib import Path
import time

temp = Path(tempfile.mkdtemp(prefix="opengrab-m7e-"))
os.environ["OPENGRAB_DATA_DIR"] = str(temp / "data")

from opengrab.engine.manager import DownloadManager  # noqa: E402
from opengrab import server as server_module  # noqa: E402
from opengrab.server import ApiServer  # noqa: E402
from opengrab.settings import Settings  # noqa: E402


def test_plugins_api_includes_health():
    """Test that GET /api/plugins returns health data for each plugin."""
    downloads = temp / "downloads"
    downloads.mkdir()
    settings = Settings(download_dir=str(downloads), max_parallel=1)
    manager = DownloadManager(settings)
    manager.start()

    # Create a simple handler that we can use to make requests
    server = ApiServer(settings, manager, port=0)
    port = server.start()
    base = f"http://127.0.0.1:{port}"
    
    def request(path: str, method: str = "GET", body=None,
                data: bytes | None = None, headers=None, auth=True):
        request_headers = dict(headers or {})
        if auth:
            request_headers.setdefault("Authorization", f"Bearer {server.token}")
        if body is not None:
            data = json.dumps(body).encode()
            request_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(base + server.base_path + path,
                                     data=data, method=method,
                                     headers=request_headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.headers, response.read()

    try:
        # Call GET /api/plugins
        status, _, raw = request("/api/plugins")
        
        assert status == 200, f"Expected status 200, got {status}"
        
        data = json.loads(raw)
        
        # Check that all required keys are present
        assert "plugins" in data, "Missing 'plugins' key"
        assert "errors" in data, "Missing 'errors' key"
        assert "health" in data, "Missing 'health' key"
        
        plugins = data["plugins"]
        errors = data["errors"] 
        health = data["health"]
        
        # Check that set of health keys equals set of plugin IDs
        plugin_ids = {p["id"] for p in plugins}
        health_keys = set(health.keys())
        assert health_keys == plugin_ids, f"Health keys {health_keys} don't match plugin IDs {plugin_ids}"
        
        # Test 1: For any plugin, health should initially be the default stats
        if plugins:
            first_plugin_id = plugins[0]["id"]
            assert first_plugin_id in health, f"Health missing for plugin {first_plugin_id}"
            
            initial_health = health[first_plugin_id]
            expected_default = {"ok": 0, "fail": 0, "last_error": None, "last_used": None}
            assert initial_health == expected_default, f"Expected default health {expected_default}, got {initial_health}"
        
        # Test 2: Record some results and verify they're reflected
        if plugins:
            first_plugin_id = plugins[0]["id"]
            
            # Record some results directly in the manager's registry
            manager.registry.record_result(first_plugin_id, True)   # success
            manager.registry.record_result(first_plugin_id, False, "boom")  # failure
            
            # Get fresh data from API
            status2, _, raw2 = request("/api/plugins")
            assert status2 == 200
            data2 = json.loads(raw2)
            
            health2 = data2["health"]
            
            # Verify the counts increased
            assert health2[first_plugin_id]["ok"] == 1, f"Expected ok=1, got {health2[first_plugin_id]['ok']}"
            assert health2[first_plugin_id]["fail"] == 1, f"Expected fail=1, got {health2[first_plugin_id]['fail']}"
            assert health2[first_plugin_id]["last_error"] == "boom", f"Expected last_error='boom', got {health2[first_plugin_id]['last_error']}"
            
            # last_used should be a timestamp (float)
            assert isinstance(health2[first_plugin_id]["last_used"], (int, float)), \
                f"Expected last_used to be numeric, got {type(health2[first_plugin_id]['last_used'])}"
        
        print("[PASS] GET /api/plugins returns keys 'plugins', 'errors' and 'health', and set(health) == set of ids in plugins")
        print("[PASS] Plugin health stats are correctly initialized")
        print("[PASS] Plugin health stats are correctly updated after recording results")

    finally:
        server.stop()
        manager.shutdown()


if __name__ == "__main__":
    test_plugins_api_includes_health()
    print("All tests passed!")