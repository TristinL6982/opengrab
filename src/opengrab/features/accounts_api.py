"""W9 routes: account details (premium, usage limits, cookies, debrid) and usage."""
from __future__ import annotations

from ..cookies import parse_header, parse_netscape
from ..debrid import SERVICES


def account_data(body: dict, current: dict | None = None) -> dict:
    """Validated Account.data from a request body (unknown keys are dropped)."""
    data = dict(current or {})
    usage = body.get("usage")
    if isinstance(usage, dict):
        daily_mb = float(usage.get("daily_mb", 0) or 0)
        parallel = int(usage.get("max_parallel", 0) or 0)
        reset = int(usage.get("reset_hour_utc", 0) or 0)
        if daily_mb < 0 or parallel < 0 or not 0 <= reset <= 23:
            raise ValueError("usage limits must be positive and the reset hour 0-23")
        data["usage"] = {"daily_bytes": int(daily_mb * 1024 * 1024), "max_parallel": parallel,
                         "reset_hour_utc": reset}
    if "cookies_txt" in body:
        text = str(body.get("cookies_txt") or "")
        if text.strip() and not parse_netscape(text):
            raise ValueError("that does not look like a cookies.txt file")
        data["cookies_txt"] = text
    if "cookie_header" in body:
        header = str(body.get("cookie_header") or "")
        if header.strip() and not parse_header(header, "x"):
            raise ValueError("cookie header must look like name=value; name2=value2")
        data["cookie_header"] = header
    if "debrid" in body:
        svc = str(body.get("debrid") or "").lower()
        if svc and svc not in SERVICES:
            raise ValueError("debrid service must be one of: " + ", ".join(SERVICES))
        data["debrid"] = svc
    if "api_token" in body and body.get("api_token"):
        data["api_token"] = str(body["api_token"])
    return {k: v for k, v in data.items() if v not in ("", None)}


def setup(srv):
    mgr = srv.manager
    r = srv.router

    def update(req):
        acc = next((a for a in mgr.accounts.accounts if a.id == req.params["aid"]), None)
        if acc is None:
            return 404, {"error": "account not found"}
        try:
            data = account_data(req.body, acc.data)
        except ValueError as exc:
            return 400, {"error": str(exc)}
        fields: dict = {"data": data}
        if "premium" in req.body:
            fields["premium"] = bool(req.body["premium"])
        if req.body.get("password"):
            fields["password"] = str(req.body["password"])
        mgr.accounts.update(acc.id, **fields)
        return next(x for x in mgr.accounts.snapshot() if x["id"] == acc.id)

    r.post(r"/api/accounts/(?P<aid>[A-Za-z0-9_-]+)", update, action="settings_write")
    r.get(r"/api/accounts/usage", lambda _req: {"accounts": mgr.account_policy.usage(),
                                                "services": sorted(SERVICES)})
    return mgr.account_policy
