"""M10d: debrid services (Real-Debrid, AllDebrid) with an injected HTTP function."""
import json

import pytest

from opengrab.debrid import AllDebrid, DebridError, DebridLink, RealDebrid, service_for, unrestrict_with
from opengrab.models import Account


class FakeHttp:
    def __init__(self, status, payload):
        self.status = status
        self.body = payload if isinstance(payload, str) else json.dumps(payload)
        self.calls = []

    def __call__(self, method, url, headers, form):
        self.calls.append((method, url, headers, form))
        return self.status, self.body


def test_realdebrid_ok():
    h = FakeHttp(200, {"download": "https://dl.rd/x.bin", "filename": "x.bin", "filesize": 42})
    r = RealDebrid().unrestrict("https://hoster/f", "tok", h)
    assert r == DebridLink("https://dl.rd/x.bin", "x.bin", 42)
    assert h.calls == [("POST", "https://api.real-debrid.com/rest/1.0/unrestrict/link",
                        {"Authorization": "Bearer tok"}, {"link": "https://hoster/f"})]


def test_realdebrid_errors():
    with pytest.raises(DebridError, match="bad_token"):
        RealDebrid().unrestrict("l", "tok", FakeHttp(401, {"error": "bad_token", "error_code": 8}))
    with pytest.raises(DebridError, match="missing API token"):
        RealDebrid().unrestrict("l", "", FakeHttp(200, {}))
    with pytest.raises(DebridError, match="bad response"):
        RealDebrid().unrestrict("l", "tok", FakeHttp(502, "<html>"))


def test_alldebrid_ok():
    h = FakeHttp(200, {"status": "success", "data": {"link": "https://dl.ad/y", "filename": "y", "filesize": "7"}})
    r = AllDebrid().unrestrict("https://h/f?a=1", "k", h)
    assert r == DebridLink("https://dl.ad/y", "y", 7)
    method, url, headers, form = h.calls[0]
    assert method == "GET"
    assert url == "https://api.alldebrid.com/v4/link/unlock?agent=opengrab&link=https%3A%2F%2Fh%2Ff%3Fa%3D1"
    assert headers == {"Authorization": "Bearer k"}
    assert form == {}


def test_alldebrid_errors():
    dead = {"status": "error", "error": {"code": "LINK_DOWN", "message": "Link is dead"}}
    with pytest.raises(DebridError, match="Link is dead"):
        AllDebrid().unrestrict("l", "k", FakeHttp(200, dead))
    with pytest.raises(DebridError, match="no download link"):
        AllDebrid().unrestrict("l", "k", FakeHttp(200, {"status": "success", "data": {}}))


def test_service_for():
    assert isinstance(service_for(Account(site="real-debrid.com", username="u")), RealDebrid)
    assert isinstance(service_for(Account(site="x", username="u", data={"debrid": "AllDebrid"})), AllDebrid)
    assert service_for(Account(site="example.com", username="u")) is None


def test_unrestrict_with_token_sources():
    h = FakeHttp(200, {"download": "https://d/1"})
    a = Account(site="real-debrid.com", username="u", password="pw", data={"api_token": "T"})
    assert unrestrict_with(a, "https://h/1", h).url == "https://d/1"
    assert h.calls[0][2] == {"Authorization": "Bearer T"}
    b = Account(site="real-debrid.com", username="u", password="pw")
    unrestrict_with(b, "https://h/1", h)
    assert h.calls[1][2] == {"Authorization": "Bearer pw"}
    with pytest.raises(DebridError, match="no debrid service"):
        unrestrict_with(Account(site="example.com", username="u"), "l", h)