import pytest

from opengrab.siteauth import (
    CredentialStore,
    curl_auth_name,
    host_matches,
    host_of,
    normalize_pattern,
    normalize_scheme,
    pick_scheme,
)


def fake_protect(text):
    return "enc:" + text[::-1]


def fake_unprotect(text):
    return text[4:][::-1]


def test_normalize_scheme_and_curl_names():
    assert normalize_scheme(" NTLM ") == "ntlm"
    assert curl_auth_name("kerberos") == "NEGOTIATE"
    assert curl_auth_name("Digest") == "DIGEST"
    with pytest.raises(ValueError):
        normalize_scheme("oauth")


def test_host_of_and_patterns():
    assert host_of("https://User@Files.Example.com:8443/a?b=1") == "files.example.com"
    assert host_of("example.com:80") == "example.com"
    assert host_of("") == ""
    assert normalize_pattern("*.Example.COM") == "*.example.com"
    with pytest.raises(ValueError):
        normalize_pattern("   ")


def test_host_matches_wildcard_and_exact():
    assert host_matches("*.example.com", "https://cdn.example.com/x") is True
    assert host_matches("*.example.com", "example.com") is False
    assert host_matches("*.example.com", "badexample.com") is False
    assert host_matches("example.com", "http://EXAMPLE.com/") is True
    assert host_matches("example.com", "") is False


def test_pick_scheme_prefers_strongest_allowed():
    offered = ['Basic realm="x"', "NTLM", "Negotiate"]
    assert pick_scheme(offered) == "negotiate"
    assert pick_scheme(offered, allowed=("basic", "ntlm")) == "ntlm"
    assert pick_scheme('Digest realm="r", qop="auth"') == "digest"
    assert pick_scheme("Negotiate", allowed=("kerberos",)) == "negotiate"
    assert pick_scheme([], allowed=("basic",)) is None
    assert pick_scheme("Bearer", allowed=("basic",)) is None


def test_store_keeps_passwords_protected():
    store = CredentialStore(protect=fake_protect, unprotect=fake_unprotect)
    cred = store.add("files.example.com", "alice", "s3cret", scheme="digest")
    assert cred.secret == "enc:terc3s"
    assert "s3cret" not in str(store.to_list())
    assert store.password(cred) == "s3cret"
    auth = store.auth_for("https://files.example.com/f.zip")
    assert auth == {"scheme": "digest", "curl": "DIGEST", "username": "alice", "password": "s3cret"}
    with pytest.raises(ValueError):
        store.add("example.com", "  ", "pw")


def test_find_prefers_exact_then_longest_wildcard_and_separates_proxy():
    store = CredentialStore()
    store.add("*.example.com", "wide", "a")
    store.add("*.dl.example.com", "narrow", "b")
    store.add("x.dl.example.com", "exact", "c")
    store.add("*.example.com", "proxyuser", "d", scheme="ntlm", for_proxy=True)
    assert store.find("https://x.dl.example.com/").username == "exact"
    assert store.find("https://y.dl.example.com/").username == "narrow"
    assert store.find("https://www.example.com/").username == "wide"
    assert store.find("https://other.org/") is None
    assert store.find("") is None
    assert store.find("www.example.com", for_proxy=True).username == "proxyuser"


def test_add_replaces_and_remove():
    store = CredentialStore()
    store.add("example.com", "old", "1")
    store.add("EXAMPLE.com", "new", "2", scheme="basic")
    assert [c.username for c in store.items] == ["new"]
    assert store.remove("example.com", for_proxy=True) is False
    assert store.remove("example.com") is True
    assert store.items == []


def test_round_trip_skips_bad_entries():
    store = CredentialStore(protect=fake_protect, unprotect=fake_unprotect)
    store.add("example.com", "bob", "pw", scheme="negotiate")
    store.add("*.corp.local", "svc", "pw2", scheme="kerberos", for_proxy=True)
    data = store.to_list() + [
        "junk",
        {"pattern": "", "username": "x", "secret": "s"},
        {"pattern": "a.com", "username": "x", "secret": "s", "scheme": "oauth"},
        {"pattern": "b.com", "username": "", "secret": "s"},
        {"pattern": "c.com", "username": "x", "secret": 5},
    ]
    loaded = CredentialStore.from_list(data, protect=fake_protect, unprotect=fake_unprotect)
    assert loaded.to_list() == store.to_list()
    assert loaded.auth_for("example.com")["password"] == "pw"
    assert loaded.auth_for("app.corp.local", for_proxy=True)["curl"] == "NEGOTIATE"