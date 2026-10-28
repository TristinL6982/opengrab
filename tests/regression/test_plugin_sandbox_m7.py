import pytest
import types
from opengrab.plugins.sandbox import GuardedClient, sandbox_context
from opengrab.plugins.permissions import Permissions, PermissionError_


class FakeClient:
    def __init__(self):
        self.calls = []
        self.cookies = {"sid": "1"}
        self.timeout = 5

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        return "ok"

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        return "ok"

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        return "ok"

    def head(self, url, **kw):
        self.calls.append(("HEAD", url))
        return "ok"

    def stream(self, method, url, **kw):
        self.calls.append((method, url))
        return "ok"


def test_guarded_client_allows_allowed_hosts():
    client = FakeClient()
    g = GuardedClient(client, Permissions(hosts=["a.com"]))
    result = g.get("https://a.com/x")
    assert result == "ok"
    assert client.calls == [("GET", "https://a.com/x")]


def test_guarded_client_blocks_unallowed_hosts():
    client = FakeClient()
    g = GuardedClient(client, Permissions(hosts=["a.com"]))
    with pytest.raises(PermissionError_, match="host not allowed: evil.com"):
        g.get("https://evil.com/")
    assert client.calls == []


def test_guarded_client_blocks_get():
    client = FakeClient()
    g = GuardedClient(client, Permissions(hosts=["a.com"]))
    with pytest.raises(PermissionError_, match="host not allowed: evil.com"):
        g.get("https://evil.com/")
    assert client.calls == []


def test_guarded_client_blocks_post():
    client = FakeClient()
    g = GuardedClient(client, Permissions(hosts=["a.com"]))
    with pytest.raises(PermissionError_, match="host not allowed: evil.com"):
        g.post("https://evil.com/")
    assert client.calls == []


def test_guarded_client_blocks_head():
    client = FakeClient()
    g = GuardedClient(client, Permissions(hosts=["a.com"]))
    with pytest.raises(PermissionError_, match="host not allowed: evil.com"):
        g.head("https://evil.com/")
    assert client.calls == []


def test_guarded_client_blocks_request():
    client = FakeClient()
    g = GuardedClient(client, Permissions(hosts=["a.com"]))
    with pytest.raises(PermissionError_, match="host not allowed: evil.com"):
        g.request("GET", "https://evil.com/")
    assert client.calls == []


def test_guarded_client_blocks_stream():
    client = FakeClient()
    g = GuardedClient(client, Permissions(hosts=["a.com"]))
    with pytest.raises(PermissionError_, match="host not allowed: evil.com"):
        g.stream("GET", "https://evil.com/")
    assert client.calls == []


def test_guarded_client_allows_cookies_when_permitted():
    client = FakeClient()
    g = GuardedClient(client, Permissions(hosts=["a.com"], cookies=True))
    result = g.cookies
    assert result == {"sid": "1"}


def test_guarded_client_blocks_cookies_when_not_permitted():
    client = FakeClient()
    g = GuardedClient(client, Permissions(hosts=["a.com"]))
    with pytest.raises(PermissionError_, match="cookie access not allowed"):
        _ = g.cookies


def test_guarded_client_passthrough_via_getattr():
    client = FakeClient()
    g = GuardedClient(client, Permissions())
    assert g.timeout == 5


def test_sandbox_context():
    ctx = types.SimpleNamespace(client=FakeClient(), settings={"k": 1})
    s = sandbox_context(ctx, Permissions(hosts=["a.com"]))
    assert isinstance(s.client, GuardedClient)
    assert isinstance(ctx.client, FakeClient)
    assert s.settings == {"k": 1}
    with pytest.raises(PermissionError_):
        s.client.get("https://evil.com/")