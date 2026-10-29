import pytest

from opengrab.proxies import Proxy, ProxyList, host_matches, parse_proxy


def test_parse_defaults_to_http_and_default_port():
    assert parse_proxy("10.0.0.1") == Proxy("http", "10.0.0.1", 8080)
    p = parse_proxy("socks5://Proxy.Example.com")
    assert p.port == 1080
    assert p.host == "proxy.example.com"


def test_parse_auth_and_url_roundtrip():
    p = parse_proxy("socks5h://us%40r:p%3Ass@1.2.3.4:9050")
    assert p.username == "us@r"
    assert p.password == "p:ss"
    assert p.remote_dns is True
    assert p.url == "socks5h://us%40r:p%3Ass@1.2.3.4:9050"
    assert parse_proxy(p.url) == p


def test_remote_dns_flags():
    assert parse_proxy("socks4a://h:1").remote_dns is True
    assert parse_proxy("socks4://h:1").remote_dns is False
    assert parse_proxy("http://h:1").remote_dns is False


@pytest.mark.parametrize("bad", ["", "ftp://h:21", "http://:8080", "http://h:0", "http://h:70000"])
def test_parse_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_proxy(bad)


def test_host_matches():
    assert host_matches("*.example.com", "a.b.example.com") is True
    assert host_matches("*.example.com", "example.com") is True
    assert host_matches("*.example.com", "badexample.com") is False
    assert host_matches("WWW.Example.com", "example.com") is True
    assert host_matches("example.com", "a.example.com") is False
    assert host_matches("*", "anything.net") is True


def test_no_proxies_means_direct():
    assert ProxyList().choose("https://x.com/f") is None


def test_failover_in_order_and_exhaustion():
    pl = ProxyList(["http://a:1", "http://b:2"])
    a, b = pl.proxies
    assert pl.choose("https://x.com/f") == a
    pl.mark_failed(a)
    assert pl.choose("https://x.com/f") == b
    assert pl.candidates("https://x.com/f") == [b]
    pl.mark_failed(b)
    with pytest.raises(LookupError):
        pl.choose("https://x.com/f")
    pl.mark_ok(a)
    assert pl.choose("https://x.com/f") == a
    pl.mark_failed(a)
    pl.reset()
    assert pl.candidates("https://x.com/f") == [a, b]


def test_rules_and_bypass():
    pl = ProxyList(
        ["http://a:1"],
        rules={"*.video.net": "socks5h://t:9050", "intranet.local": "direct"},
        bypass=["localhost", "*.lan"],
    )
    assert pl.choose("https://cdn.video.net/v.mp4") == Proxy("socks5h", "t", 9050)
    assert pl.choose("http://intranet.local/x") is None
    assert pl.choose("http://localhost:8000/") is None
    assert pl.choose("http://nas.lan/x") is None
    assert pl.choose("https://www.other.com/") == Proxy("http", "a", 1)


def test_first_matching_rule_wins():
    pl = ProxyList()
    pl.add_rule("*.example.com", "http://first:1")
    pl.add_rule("dl.example.com", None)
    assert pl.choose("https://dl.example.com/f") == Proxy("http", "first", 1)