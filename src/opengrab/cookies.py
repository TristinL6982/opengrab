"""Cookie login (M10b): parse Netscape cookies.txt and Cookie headers, pick cookies for a URL."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Cookie:
    domain: str
    name: str
    value: str
    path: str = "/"
    secure: bool = False
    expires: int = 0              # 0 means session cookie (never expires here)
    include_subdomains: bool = True


def parse_netscape(text: str) -> list[Cookie]:
    """Parse a Netscape/Mozilla cookies.txt export; bad lines are skipped."""
    out: list[Cookie] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, flag, path, secure, expires, name, value = parts
        try:
            exp = int(expires or 0)
        except ValueError:
            continue
        if not name or not domain:
            continue
        out.append(Cookie(
            domain=domain.lstrip(".").lower(),
            name=name,
            value=value,
            path=path or "/",
            secure=secure.upper() == "TRUE",
            expires=exp,
            include_subdomains=flag.upper() == "TRUE" or domain.startswith("."),
        ))
    return out


def parse_header(header: str, domain: str) -> list[Cookie]:
    """Parse a pasted 'a=1; b=2' Cookie header into cookies for domain."""
    dom = (domain or "").lstrip(".").lower()
    out: list[Cookie] = []
    for part in (header or "").split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name:
            continue
        out.append(Cookie(domain=dom, name=name, value=value.strip()))
    return out


def _domain_match(c: Cookie, host: str) -> bool:
    return host == c.domain or (c.include_subdomains and host.endswith("." + c.domain))


def _path_match(c: Cookie, path: str) -> bool:
    if c.path == "/" or path == c.path:
        return True
    return path.startswith(c.path.rstrip("/") + "/")


def cookies_for_url(cookies: list[Cookie], url: str, now: float) -> list[Cookie]:
    """Cookies that apply to url at time now (domain, path, secure, expiry)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    https = parts.scheme.lower() == "https"
    out = []
    for c in cookies:
        if not _domain_match(c, host) or not _path_match(c, path):
            continue
        if c.secure and not https:
            continue
        if c.expires and c.expires <= now:
            continue
        out.append(c)
    return out


def to_header(cookies: list[Cookie]) -> str:
    return "; ".join(f"{c.name}={c.value}" for c in cookies)