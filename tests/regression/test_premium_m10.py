"""M10e: premium resolve through debrid accounts."""
import json

from opengrab.models import Account
from opengrab.premium import debrid_accounts, premium_resolve

RD_OK = (200, {"download": "https://dl.rd/x.bin", "filename": "x.bin", "filesize": 42})
AD_DEAD = (200, {"status": "error", "error": {"code": "LINK_DOWN", "message": "Link is dead"}})


class Router:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers, form):
        self.calls.append(url)
        for key, (status, payload) in self.routes.items():
            if key in url:
                return status, json.dumps(payload)
        return 404, "{}"


class Refuse:
    def __init__(self, ids):
        self.ids = ids

    def can_start(self, acc_id, rule, active):
        return acc_id not in self.ids


def rd(**kw):
    return Account(site="real-debrid.com", username="u", data={"api_token": "T"}, **kw)


def ad(**kw):
    return Account(site="alldebrid.com", username="u", data={"api_token": "K"}, **kw)


def test_debrid_accounts_premium_first():
    plain = Account(site="example.com", username="u", premium=True)
    r = rd(id="R")
    a = ad(id="D", premium=True)
    assert [x.id for x in debrid_accounts([plain, r, a])] == ["D", "R"]


def test_premium_resolve_ok():
    h = Router({"real-debrid": RD_OK})
    res = premium_resolve("https://hoster/f", [rd(id="R")], h)
    assert res.url == "https://dl.rd/x.bin"
    assert res.filename == "x.bin"
    assert res.size == 42
    assert res.extra == {"account": "R", "service": "realdebrid"}


def test_skips_failing_service():
    h = Router({"alldebrid": AD_DEAD, "real-debrid": RD_OK})
    res = premium_resolve("https://hoster/f", [rd(id="R"), ad(id="D", premium=True)], h)
    assert res.extra["account"] == "R"
    assert len(h.calls) == 2
    assert "alldebrid" in h.calls[0]


def test_tracker_refusal_skips_account():
    h = Router({"real-debrid": RD_OK})
    res = premium_resolve("https://hoster/f", [rd(id="R1", premium=True), rd(id="R2")], h, tracker=Refuse({"R1"}))
    assert res.extra["account"] == "R2"
    assert len(h.calls) == 1


def test_none_when_nothing_usable():
    h = Router({"real-debrid": RD_OK})
    assert premium_resolve("https://hoster/f", [Account(site="example.com", username="u")], h) is None
    assert h.calls == []
    assert premium_resolve("https://hoster/f", [rd(id="R")], h, tracker=Refuse({"R"})) is None
    assert h.calls == []


def test_zero_size_becomes_none():
    h = Router({"real-debrid": (200, {"download": "https://d/1"})})
    res = premium_resolve("https://hoster/f", [rd(id="R")], h)
    assert res.size is None
    assert res.filename == ""