"""M12d part 1 (B17): versioned service worker served with the app version."""
import http.client
import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="og-sw-"))
os.environ.setdefault("OPENGRAB_DATA_DIR", str(_TMP / "data"))

from opengrab import __version__, server  # noqa: E402
from opengrab.engine.manager import DownloadManager  # noqa: E402
from opengrab.settings import Settings  # noqa: E402

SW = Path(server.static_dir()) / "sw.js"


@pytest.fixture(scope="module")
def srv():
    dl = _TMP / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    settings = Settings(download_dir=str(dl), api_token="swtesttoken0123456789")
    mgr = DownloadManager(settings)
    mgr.start()
    s = server.ApiServer(settings, mgr, host="127.0.0.1", port=0)
    s.port_used = s.start()
    yield s
    s.stop()
    mgr.shutdown()


def _get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read().decode("utf-8", "replace")
    headers = {k.lower(): v for k, v in resp.getheaders()}
    conn.close()
    return resp.status, headers, body


def test_sw_source_uses_version_placeholder():
    text = SW.read_text(encoding="utf-8")
    assert '"__OPENGRAB_VERSION__"' in text
    assert '"opengrab-ui-" + VERSION' in text
    assert "opengrab-ui-v4" not in text


def test_sw_source_network_first_and_cleans_old_caches():
    text = SW.read_text(encoding="utf-8")
    assert "function networkFirst" in text
    assert "NETWORK_FIRST" in text and ".css" in text and ".js" in text
    assert "caches.delete" in text and 'k.startsWith("opengrab-ui-")' in text
    assert "API_PREFIX" in text


def test_served_sw_has_app_version(srv):
    status, headers, body = _get(srv.port_used, "/sw.js")
    assert status == 200
    assert "__OPENGRAB_VERSION__" not in body
    assert f'const VERSION = "{__version__}";' in body
    assert "javascript" in headers.get("content-type", "")


def test_served_sw_is_not_cached(srv):
    status, headers, _ = _get(srv.port_used, "/sw.js")
    assert status == 200
    assert headers.get("cache-control") == "no-cache"


def test_other_static_files_keep_short_cache(srv):
    status, headers, body = _get(srv.port_used, "/app.js")
    assert status == 200
    assert headers.get("cache-control") == "max-age=60"
    assert "__OPENGRAB_VERSION__" not in body
