"""VirusTotal check: hash lookup first, upload only when allowed, the API key
never leaves the server, and a flagged file is quarantined and reported."""
import hashlib
import json
import time

from opengrab import virustotal as vtmod
from tests.helpers.apiserver import pair_device, req, running_server
from tests.helpers.filesite import FileSite


class FakeVT:
    """Stands in for www.virustotal.com: answers from a dict of known hashes."""

    def __init__(self, known=None, key="good-key"):
        self.known = dict(known or {})
        self.key = key
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers.get("x-apikey"), len(body or b"")))
        if headers.get("x-apikey") != self.key:
            return 401, b'{"error": {"code": "WrongCredentialsError"}}'
        if method == "GET" and "/files/" in url and not url.endswith("upload_url"):
            sha = url.rsplit("/", 1)[1]
            if sha not in self.known:
                return 404, b'{"error": {"code": "NotFoundError"}}'
            mal = self.known[sha]
            return 200, json.dumps({"data": {"attributes": {
                "last_analysis_stats": {"malicious": mal, "suspicious": 0, "undetected": 70 - mal, "harmless": 0},
                "last_analysis_results": {"EngineA": {"category": "malicious" if mal else "undetected",
                                                      "result": "Trojan.Test" if mal else None}}}}}).encode()
        if method == "POST" and url.endswith("/files"):
            return 200, b'{"data": {"type": "analysis", "id": "an-1"}}'
        if method == "GET" and "/analyses/" in url:
            return 200, json.dumps({"data": {"attributes": {"status": "completed",
                                                            "stats": {"malicious": 0, "undetected": 69}}}}).encode()
        return 500, b"{}"


def _file(tmp_path, data=b"hello virus total"):
    p = tmp_path / "f.bin"
    p.write_bytes(data)
    return str(p), hashlib.sha256(data).hexdigest()


def test_known_clean_and_flagged_files(tmp_path):
    path, sha = _file(tmp_path)
    vt = vtmod.VirusTotal(vtmod.VtConfig(api_key="good-key"), http=FakeVT({sha: 0}))
    res = vt.check(path)
    assert res.status == "clean" and res.engines == 70 and res.link.endswith(sha)

    fake = FakeVT({sha: 5})
    res = vtmod.VirusTotal(vtmod.VtConfig(api_key="good-key", min_detections=3), http=fake).check(path)
    assert res.flagged and res.malicious == 5 and res.names == ["EngineA: Trojan.Test"]
    assert "5 of 70 engines" in res.detail
    # one engine alone is not enough when two must agree
    res = vtmod.VirusTotal(vtmod.VtConfig(api_key="good-key"), http=FakeVT({sha: 1})).check(path)
    assert res.status == "clean"


def test_unknown_files_are_uploaded_only_when_allowed(tmp_path):
    path, sha = _file(tmp_path)
    fake = FakeVT()
    res = vtmod.VirusTotal(vtmod.VtConfig(api_key="good-key"), http=fake).check(path)
    assert res.status == "unknown"
    assert [c[0] for c in fake.calls] == ["GET"]  # only the hash went out

    fake = FakeVT()
    vt = vtmod.VirusTotal(vtmod.VtConfig(api_key="good-key", upload_unknown=True), http=fake, sleep=lambda s: None)
    res = vt.check(path)
    assert res.status == "clean" and res.engines == 69
    assert any(m == "POST" and size > len(b"hello virus total") for m, _u, _k, size in fake.calls)


def test_bad_key_and_missing_key_are_reported_not_raised(tmp_path):
    path, _ = _file(tmp_path)
    res = vtmod.VirusTotal(vtmod.VtConfig(api_key="wrong"), http=FakeVT()).check(path)
    assert res.status == "error" and "API key" in res.detail
    res = vtmod.VirusTotal(vtmod.VtConfig(api_key=""), http=FakeVT()).check(path)
    assert res.status == "error" and "API key" in res.detail


def _wait(pred, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


def test_finished_download_is_checked_quarantined_and_key_stays_secret(monkeypatch):
    data = b"pretend malware " * 1000
    fake = FakeVT({hashlib.sha256(data).hexdigest(): 9})
    monkeypatch.setattr(vtmod, "_default_http", fake)
    monkeypatch.setattr(vtmod.VirusTotal.__init__, "__defaults__", (fake, time.sleep))
    site = FileSite()
    try:
        url = site.add("/x/evil.bin", data)
        settings = {"virustotal": {"enabled": True, "api_key": "good-key"},
                    "avscan": {"enabled": False, "quarantine": True}}
        with running_server(settings) as srv:
            link = srv.manager.add_url(url)
            got = _wait(lambda: (srv.manager.get(link.id).extra.get("virustotal") or {}).get("status") == "flagged"
                        and srv.manager.get(link.id))
            assert got.extra["avscan"]["quarantined"] and "quarantine" in got.path
            notes = req(srv, "GET", "/api/notifications")[1]
            assert any(n["title"] == "Virus found" and "VirusTotal" in n.get("message", n.get("body", "")) for n in notes)

            # the key is never sent back, and a blank key keeps the saved one
            st, s = req(srv, "GET", "/api/settings")
            assert "api_key" not in s["virustotal"] and s["virustotal"]["api_key_set"] is True
            assert "good-key" not in json.dumps(s)
            req(srv, "POST", "/api/settings", body={"virustotal": {"enabled": True, "api_key": "", "min_detections": 3}})
            assert srv.manager.settings.virustotal["api_key"] == "good-key"
            assert srv.manager.settings.virustotal["min_detections"] == 3

            # a paired phone can neither change it nor trigger uploads
            dev = pair_device(srv)
            st, res = req(srv, "POST", "/api/settings", body={"virustotal": {"upload_unknown": True}}, auth=dev)
            assert st == 403 or "virustotal" not in res.get("applied", [])
            assert req(srv, "POST", f"/api/links/{link.id}/virustotal", body={}, auth=dev)[0] == 403
            # the manual check works for the owner
            st, _ = req(srv, "POST", f"/api/links/{link.id}/virustotal", body={})
            assert st == 202
    finally:
        site.close()
