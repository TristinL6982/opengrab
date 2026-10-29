import pytest

from opengrab.remote import (AuthRateLimiter, RemotePolicy, SCOPE_DEVICE, SCOPE_MASTER,
                             bearer, forwarded_proto, ip_in_list, is_loopback,
                             policy_from_dict, resolve_scope, scope_allows)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_loopback_and_ip_lists():
    assert is_loopback("127.0.0.1")
    assert is_loopback("::1")
    assert is_loopback("::ffff:127.0.0.1")
    assert not is_loopback("192.168.1.5")
    assert not is_loopback("garbage")
    assert ip_in_list("10.0.0.7", ["10.0.0.0/8"])
    assert ip_in_list("172.17.0.1", ["bad", "172.17.0.1"])
    assert not ip_in_list("192.168.1.5", ["10.0.0.0/8", "::1"])
    assert not ip_in_list("nope", ["10.0.0.0/8"])


def test_forwarded_proto_variants():
    assert forwarded_proto({"X-Forwarded-Proto": "HTTPS, http"}) == "https"
    assert forwarded_proto({"Forwarded": 'for=1.2.3.4;proto="https"'}) == "https"
    assert forwarded_proto({}) == ""


def test_transport_default_policy():
    p = RemotePolicy()
    assert p.transport_allowed("127.0.0.1", {})
    assert p.transport_allowed("8.8.8.8", {}, is_tls=True)
    assert not p.transport_allowed("192.168.1.5", {})
    assert p.transport_allowed("127.0.0.1", {"X-Forwarded-For": "8.8.8.8", "X-Forwarded-Proto": "https"})
    assert not p.transport_allowed("127.0.0.1", {"X-Forwarded-For": "8.8.8.8"})
    assert not p.transport_allowed("192.168.1.5", {"X-Forwarded-Proto": "https"})


def test_transport_trusted_proxy_and_remote_http():
    p = RemotePolicy(trusted_proxies=["172.17.0.0/16"])
    assert p.transport_allowed("172.17.0.1", {"X-Forwarded-Proto": "https"})
    assert not p.transport_allowed("172.17.0.1", {"X-Forwarded-Proto": "http"})
    lan = RemotePolicy(allow_remote_http=True)
    assert lan.transport_allowed("192.168.1.5", {})
    assert lan.transport_allowed("127.0.0.1", {"X-Forwarded-For": "8.8.8.8"})


def test_client_ip_only_trusts_proxies():
    p = RemotePolicy(trusted_proxies=["10.0.0.2"])
    assert p.client_ip("10.0.0.2", {"X-Forwarded-For": "8.8.8.8, 10.0.0.2"}) == "8.8.8.8"
    assert p.client_ip("127.0.0.1", {"X-Forwarded-For": "9.9.9.9"}) == "9.9.9.9"
    assert p.client_ip("192.168.1.5", {"X-Forwarded-For": "8.8.8.8"}) == "192.168.1.5"
    assert p.client_ip("10.0.0.2", {"X-Forwarded-For": "junk"}) == "10.0.0.2"


def test_policy_from_dict():
    p = policy_from_dict({"allow_remote_http": True, "trusted_proxies": ["10.0.0.1", 5]})
    assert p.allow_remote_http is True
    assert p.trusted_proxies == ["10.0.0.1"]
    q = policy_from_dict({"allow_remote_http": "yes", "trusted_proxies": "10.0.0.1"})
    assert q.allow_remote_http is False
    assert q.trusted_proxies == []
    assert policy_from_dict(None) == RemotePolicy()


def test_bearer_and_scopes():
    assert bearer({"Authorization": "Bearer abc "}) == "abc"
    assert bearer({"Authorization": "bearer xyz"}) == "xyz"
    assert bearer({"Authorization": "Basic abc"}) == ""
    assert bearer({}) == ""
    devices = {"dev-tok": object()}
    assert resolve_scope("master-tok", "master-tok", devices.get) == SCOPE_MASTER
    assert resolve_scope("dev-tok", "master-tok", devices.get) == SCOPE_DEVICE
    assert resolve_scope("wrong", "master-tok", devices.get) is None
    assert resolve_scope("", "master-tok", devices.get) is None
    assert resolve_scope("dev-tok", "master-tok", None) is None
    assert scope_allows(SCOPE_MASTER, "pair_start")
    assert not scope_allows(SCOPE_DEVICE, "pair_start")
    assert not scope_allows(SCOPE_DEVICE, "devices_revoke")
    assert scope_allows(SCOPE_DEVICE, "add_links")
    assert not scope_allows(None, "add_links")


def test_rate_limiter_blocks_and_expires():
    clock = Clock()
    rl = AuthRateLimiter(max_failures=3, window=60, block=300, clock=clock)
    assert rl.record_failure("1.2.3.4") is False
    assert rl.record_failure("1.2.3.4") is False
    assert rl.record_failure("1.2.3.4") is True
    assert rl.is_blocked("1.2.3.4")
    assert not rl.is_blocked("5.6.7.8")
    assert rl.retry_after("1.2.3.4") == 300
    clock.now += 300
    assert not rl.is_blocked("1.2.3.4")
    assert rl.retry_after("1.2.3.4") == 0


def test_rate_limiter_window_and_success_reset():
    clock = Clock()
    rl = AuthRateLimiter(max_failures=3, window=60, block=300, clock=clock)
    rl.record_failure("ip")
    rl.record_failure("ip")
    clock.now += 61
    assert rl.record_failure("ip") is False
    rl.record_failure("ip")
    rl.record_success("ip")
    assert rl.record_failure("ip") is False
    assert not rl.is_blocked("ip")