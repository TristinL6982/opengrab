"""Debrid services (M10d): unrestrict hoster links through Real-Debrid or AllDebrid with an injected HTTP function."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

from .models import Account

# http(method, url, headers, form) -> (status, body_text)
Http = Callable[[str, str, dict, dict], tuple]


class DebridError(Exception):
    pass


@dataclass
class DebridLink:
    url: str
    filename: str = ""
    size: int = 0


def _json(status: int, body: str) -> dict:
    try:
        data = json.loads(body or "{}")
    except ValueError:
        raise DebridError(f"bad response (HTTP {status})") from None
    if not isinstance(data, dict):
        raise DebridError(f"bad response (HTTP {status})")
    return data


class RealDebrid:
    name = "realdebrid"
    api = "https://api.real-debrid.com/rest/1.0"

    def unrestrict(self, link: str, token: str, http: Http) -> DebridLink:
        if not token:
            raise DebridError("realdebrid: missing API token")
        status, body = http("POST", self.api + "/unrestrict/link",
                            {"Authorization": "Bearer " + token}, {"link": link})
        data = _json(status, body)
        if status >= 400 or "error" in data:
            raise DebridError("realdebrid: " + str(data.get("error") or f"HTTP {status}"))
        url = str(data.get("download") or "")
        if not url:
            raise DebridError("realdebrid: no download link")
        return DebridLink(url, str(data.get("filename") or ""), int(data.get("filesize") or 0))


class AllDebrid:
    name = "alldebrid"
    api = "https://api.alldebrid.com/v4"

    def unrestrict(self, link: str, token: str, http: Http) -> DebridLink:
        if not token:
            raise DebridError("alldebrid: missing API token")
        url = self.api + "/link/unlock?" + urlencode({"agent": "opengrab", "link": link})
        status, body = http("GET", url, {"Authorization": "Bearer " + token}, {})
        data = _json(status, body)
        if status >= 400 or data.get("status") != "success":
            err = data.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise DebridError("alldebrid: " + str(msg or f"HTTP {status}"))
        d = data.get("data") or {}
        u = str(d.get("link") or "")
        if not u:
            raise DebridError("alldebrid: no download link")
        return DebridLink(u, str(d.get("filename") or ""), int(d.get("filesize") or 0))


SERVICES = {"realdebrid": RealDebrid(), "alldebrid": AllDebrid()}
_SITES = {"real-debrid.com": "realdebrid", "alldebrid.com": "alldebrid"}


def service_for(acc: Account):
    """Service named by acc.data["debrid"], else by acc.site; None if neither matches."""
    key = str((acc.data or {}).get("debrid") or "").lower()
    if not key:
        key = _SITES.get((acc.site or "").lower().lstrip("."), "")
    return SERVICES.get(key)


def unrestrict_with(acc: Account, link: str, http: Http) -> DebridLink:
    """Unrestrict link with acc's service; token is acc.data["api_token"], else acc.password."""
    svc = service_for(acc)
    if svc is None:
        raise DebridError(f"no debrid service for account site {acc.site!r}")
    token = str((acc.data or {}).get("api_token") or acc.password or "")
    return svc.unrestrict(link, token, http)