"""M12c: AccessGate glue (pairing + remote policy + auth rate limit)."""
import json
import os
import tempfile

from opengrab.access import AccessGate, PAIRING_FILE
from opengrab.pairing import PairingStore
from opengrab.remote import AuthRateLimiter, RemotePolicy


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_gate(path=None, policy=None):
    clock = Clock()
    codes = iter(["ABCD2345", "EFGH6789", "JKLM2345"])
    tokens = iter(["tok-one", "tok-two", "tok-three"])
    store = PairingStore(clock=clock, code_factory=lambda: next(codes),
                         token_factory=lambda: next(tokens))
    limiter = AuthRateLimiter(max_failures=3, window=60, block=300, clock=clock)
    gate = AccessGate("master-secret", store=store, policy=policy, limiter=limiter, path=path)
    return gate, clock


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_master_token_scope_and_unknown_token_rejected():
    gate, _ = make_gate()
    assert gate.check("127.0.0.1", auth("master-secret"), "pair_start") == (200, "master")
    status, _ = gate.check("127.0.0.1", auth("nope"))
    assert status == 401
    status, _ = gate.check("127.0.0.1", {})
    assert status == 401


def test_pairing_flow_gives_device_scope_without_master_actions():
    gate, _ = make_gate()
    status, started = gate.pair_start("master")
    assert status == 200
    assert started == {"code": "ABCD-2345", "expires_in": 300}
    status, claimed = gate.pair_claim("127.0.0.1", {}, {"code": "abcd-2345", "name": "Phone"})
    assert status == 200
    assert claimed["token"] == "tok-one"
    assert claimed["device"]["name"] == "Phone"
    assert "token_hash" not in claimed["device"]
    assert gate.check("10.0.0.5", auth("tok-one"), "add_download") == (200, "device")
    status, _ = gate.check("10.0.0.5", auth("tok-one"), "devices_list")
    assert status == 403


def test_pair_start_requires_master_scope():
    gate, _ = make_gate()
    status, body = gate.pair_start("device")
    assert status == 403
    assert "error" in body
    assert gate.store.pending_count() == 0


def test_bad_claims_are_rate_limited_per_ip():
    gate, clock = make_gate()
    gate.pair_start("master")
    for _ in range(3):
        status, _ = gate.pair_claim("10.0.0.9", {}, {"code": "WRONG234"})
        assert status == 400
    status, body = gate.pair_claim("10.0.0.9", {}, {"code": "ABCD2345"})
    assert status == 429
    assert body["retry_after"] == 300
    status, _ = gate.check("10.0.0.9", auth("master-secret"))
    assert status == 429
    assert gate.check("10.0.0.8", auth("master-secret")) == (200, "master")
    clock.now += 301
    assert gate.check("10.0.0.9", auth("master-secret")) == (200, "master")


def test_trusted_proxy_rate_limits_the_forwarded_client():
    policy = RemotePolicy(trusted_proxies=["10.1.0.0/16"])
    gate, _ = make_gate(policy=policy)
    headers = {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https",
               "Authorization": "Bearer bad"}
    assert gate.client_ip("10.1.2.3", headers) == "203.0.113.7"
    assert gate.transport_allowed("10.1.2.3", headers) is True
    for _ in range(3):
        gate.check("10.1.2.3", headers)
    assert gate.limiter.is_blocked("203.0.113.7") is True
    assert gate.limiter.is_blocked("10.1.2.3") is False


def test_transport_default_blocks_remote_http_and_opt_in_allows_it():
    gate, _ = make_gate()
    assert gate.transport_allowed("127.0.0.1", {}) is True
    assert gate.transport_allowed("192.168.1.20", {}) is False
    assert gate.transport_allowed("192.168.1.20", {}, is_tls=True) is True
    gate2, _ = make_gate(policy=RemotePolicy(allow_remote_http=True))
    assert gate2.transport_allowed("192.168.1.20", {}) is True


def test_devices_persist_and_revoke():
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, PAIRING_FILE)
    gate, _ = make_gate(path=path)
    gate.pair_start("master")
    _, claimed = gate.pair_claim("127.0.0.1", {}, {"code": "ABCD2345", "name": "Laptop"})
    with open(path, "r", encoding="utf-8") as fh:
        saved = json.load(fh)
    assert len(saved["devices"]) == 1
    assert "tok-one" not in json.dumps(saved)
    reloaded = AccessGate.load(folder, "master-secret")
    status, listed = reloaded.devices_list()
    assert status == 200
    assert [d["name"] for d in listed["devices"]] == ["Laptop"]
    assert reloaded.check("127.0.0.1", auth("tok-one")) == (200, "device")
    device_id = claimed["device"]["id"]
    assert reloaded.devices_revoke(device_id) == (200, {"revoked": device_id})
    assert reloaded.devices_revoke(device_id)[0] == 404
    status, _ = reloaded.check("127.0.0.1", auth("tok-one"))
    assert status == 401
    again = AccessGate.load(folder, "master-secret")
    assert again.devices_list() == (200, {"devices": []})


def test_load_tolerates_missing_and_corrupt_file():
    folder = tempfile.mkdtemp()
    gate = AccessGate.load(folder, "m")
    assert gate.devices_list() == (200, {"devices": []})
    with open(os.path.join(folder, PAIRING_FILE), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    gate = AccessGate.load(folder, "m")
    assert gate.devices_list() == (200, {"devices": []})
    assert gate.check("127.0.0.1", auth("m")) == (200, "master")