"""W12: plugin permissions and sandbox, template validation/updates, marketplace."""
import base64
import hashlib
import json
import os

import pytest
from Cryptodome.PublicKey import ECC
from Cryptodome.Signature import eddsa

from opengrab.engine.plugin_policy import verify_signature
from tests.helpers.apiserver import req, running_server
from tests.helpers.filesite import FileSite

USER_PLUGIN = '''
from opengrab.plugins.base import SitePlugin, ResolveResult
class Probe(SitePlugin):
    id = "probe"
    name = "Probe"
    site = "probe.test"
    PERMISSIONS = {"hosts": ["allowed.test"]}
    def resolve(self, url, ctx):
        ctx.client.get(TARGET)
        return ResolveResult(url="http://allowed.test/file.bin")
'''

TEMPLATE = {"id": "tplhost", "name": "Tpl host", "site": "tplhost.test",
            "url_pattern": r"^https?://tplhost\.test/f/", "direct_url_regex": r'href="([^"]+\.bin)"'}


def _plugins_dir():
    from opengrab.settings import user_plugins_dir
    return user_plugins_dir()


def test_user_plugin_needs_approval_and_is_sandboxed():
    with running_server() as srv:
        (_plugins_dir() / "probe.py").write_text(USER_PLUGIN.replace("TARGET", '"http://elsewhere.test/x"'))
        srv.manager.registry.scan(force=True)
        st, ov = req(srv, "GET", "/api/plugins/permissions")
        entry = next(p for p in ov["plugins"] if p["id"] == "probe")
        assert entry["missing"] == ["host:allowed.test"]
        link = srv.manager.add_url("http://probe.test/a")
        import time
        deadline = time.time() + 10
        while srv.manager.get(link.id).status.value not in ("error", "waiting", "finished") and time.time() < deadline:
            time.sleep(0.1)
        l = srv.manager.get(link.id)
        assert l.status.value == "error" and "needs your approval" in l.error
        # approve what it asked for: it may reach allowed.test only
        assert req(srv, "POST", "/api/plugins/probe/permissions", body={})[0] == 200
        plugin = srv.manager.registry.get("probe")
        ctx = srv.manager._ctx_for(plugin)
        from opengrab.plugins.permissions import PermissionError_
        with pytest.raises(PermissionError_):
            ctx.client.get("http://elsewhere.test/x")  # outside the grant
        assert req(srv, "DELETE", "/api/plugins/probe/permissions")[0] == 200


def test_builtin_plugins_are_not_sandboxed():
    with running_server() as srv:
        direct = srv.manager.registry.get("direct")
        assert srv.manager._ctx_for(direct).client is srv.manager.client


def test_template_validation_route():
    with running_server() as srv:
        st, ok = req(srv, "POST", "/api/plugins/validate", body={"template": TEMPLATE})
        assert st == 200 and ok["ok"] is True, ok
        st, bad = req(srv, "POST", "/api/plugins/validate", body={"template": "{not json"})
        assert bad["ok"] is False and "JSON" in bad["errors"][0]


@pytest.fixture
def site():
    s = FileSite()
    yield s
    s.close()


def test_template_updates_from_an_index(site):
    data = json.dumps(TEMPLATE).encode()
    site.add("/tpl/tplhost.json", data)
    site.add("/tpl/index.json", json.dumps({"templates": [
        {"id": "tplhost", "version": "1.2", "url": "tplhost.json", "sha256": hashlib.sha256(data).hexdigest()},
        {"id": "broken", "version": "1.0", "url": "tplhost.json", "sha256": "0" * 64}]}).encode())
    with running_server({"plugin_index_url": site.base + "/tpl/index.json"}) as srv:
        st, res = req(srv, "POST", "/api/plugins/update", body={})
        assert st == 200 and res["updated"] == ["tplhost"] and "broken" in res["failed"]
        assert srv.manager.registry.get("tplhost") is not None
        assert srv.manager.plugin_policy.installed_versions()["tplhost"] == "1.2"
        st, again = req(srv, "POST", "/api/plugins/update", body={})
        assert again["updated"] == []  # already current


def _keypair():
    key = ECC.generate(curve="ed25519")
    return key, key.public_key().export_key(format="raw").hex()


def test_signature_check():
    key, pub = _keypair()
    data = b"plugin bytes"
    sig = base64.b64encode(eddsa.new(key, "rfc8032").sign(data)).decode()
    assert verify_signature(sig, data, [pub])
    assert not verify_signature(sig, data + b"!", [pub])
    other_key, other_pub = _keypair()
    assert not verify_signature(sig, data, [other_pub])
    assert verify_signature(sig, data, [other_pub, pub])


def test_marketplace_install_requires_a_trusted_signature(site):
    key, pub = _keypair()
    data = json.dumps(TEMPLATE).encode()
    sig = base64.b64encode(eddsa.new(key, "rfc8032").sign(data)).decode()
    site.add("/m/tplhost.json", data)
    site.add("/m/catalog.json", json.dumps({"plugins": [{
        "id": "tplhost", "name": "Tpl host", "version": "2.0", "url": site.base + "/m/tplhost.json",
        "sha256": hashlib.sha256(data).hexdigest(), "rating": 4.5, "ratings_count": 12,
        "signature": sig, "permissions": {"hosts": ["tplhost.test"]}}]}).encode())
    with running_server({"marketplace_url": site.base + "/m/catalog.json"}) as srv:
        st, cat = req(srv, "GET", "/api/marketplace?q=tpl")
        assert st == 200 and cat["listings"][0]["signed"] and cat["listings"][0]["installed"] is None
        st, err = req(srv, "POST", "/api/marketplace/install", body={"id": "tplhost"})
        assert st == 400 and "key" in err["error"]  # no trusted key yet
        req(srv, "POST", "/api/settings", body={"marketplace_keys": [pub]})
        st, done = req(srv, "POST", "/api/marketplace/install", body={"id": "tplhost"})
        assert st == 201 and done["version"] == "2.0"
        assert srv.manager.registry.get("tplhost") is not None
        assert req(srv, "GET", "/api/marketplace")[1]["listings"][0]["installed"] == "2.0"
        assert req(srv, "POST", "/api/marketplace/install", body={"id": "nope"})[0] == 404


def test_cli_validate_template(tmp_path, capsys):
    from opengrab.cli import main
    good = tmp_path / "good.json"
    good.write_text(json.dumps(TEMPLATE))
    assert main(["--validate-template", str(good)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"id": "x"}))
    assert main(["--validate-template", str(bad)]) == 1
    assert "problem" in capsys.readouterr().out
