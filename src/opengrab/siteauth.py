"""Per-site credentials and server/proxy authentication schemes (M11l).

Stores saved logins per host pattern (exact host or "*.example.com"), keeps passwords
only in protected form (the protect/unprotect functions are injected, e.g. Windows DPAPI),
and picks the strongest scheme a server offers. Pure module: no network, no OS calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

SCHEMES = ("basic", "digest", "ntlm", "negotiate", "kerberos")
CHALLENGE_PREFERENCE = ("negotiate", "ntlm", "digest", "basic")
CURL_NAMES = {
    "basic": "BASIC",
    "digest": "DIGEST",
    "ntlm": "NTLM",
    "negotiate": "NEGOTIATE",
    "kerberos": "NEGOTIATE",
}


def normalize_scheme(scheme) -> str:
    value = str(scheme or "").strip().lower()
    if value not in SCHEMES:
        raise ValueError(f"unknown auth scheme: {scheme!r}")
    return value


def curl_auth_name(scheme) -> str:
    return CURL_NAMES[normalize_scheme(scheme)]


def host_of(url_or_host) -> str:
    text = str(url_or_host or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    return (urlsplit(text).hostname or "").lower()


def normalize_pattern(pattern) -> str:
    text = str(pattern or "").strip().lower()
    if text.startswith("*."):
        rest = host_of(text[2:])
        if not rest:
            raise ValueError(f"bad host pattern: {pattern!r}")
        return "*." + rest
    host = host_of(text)
    if not host:
        raise ValueError(f"bad host pattern: {pattern!r}")
    return host


def host_matches(pattern, host) -> bool:
    pat = normalize_pattern(pattern)
    name = host_of(host)
    if not name:
        return False
    if pat.startswith("*."):
        return name.endswith(pat[1:])
    return name == pat


def pick_scheme(challenges, allowed=SCHEMES):
    """Return the strongest scheme offered in WWW-/Proxy-Authenticate values, or None."""
    if isinstance(challenges, str):
        challenges = [challenges]
    allowed_set = set()
    for item in allowed:
        name = normalize_scheme(item)
        allowed_set.add("negotiate" if name == "kerberos" else name)
    offered = set()
    for value in challenges or []:
        for part in str(value).split(","):
            token = part.strip().split(" ", 1)[0].strip().lower()
            if token and "=" not in token:
                offered.add(token)
    for name in CHALLENGE_PREFERENCE:
        if name in offered and name in allowed_set:
            return name
    return None


@dataclass
class Credential:
    pattern: str
    username: str
    secret: str
    scheme: str = "basic"
    for_proxy: bool = False

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "username": self.username,
            "secret": self.secret,
            "scheme": self.scheme,
            "for_proxy": self.for_proxy,
        }


def _identity(text: str) -> str:
    return text


class CredentialStore:
    def __init__(self, protect=None, unprotect=None):
        self.protect = protect or _identity
        self.unprotect = unprotect or _identity
        self.items: list[Credential] = []

    def add(self, pattern, username, password, scheme="basic", for_proxy=False) -> Credential:
        pat = normalize_pattern(pattern)
        name = normalize_scheme(scheme)
        if not str(username or "").strip():
            raise ValueError("username is required")
        self.remove(pat, for_proxy=for_proxy)
        cred = Credential(pat, str(username).strip(), self.protect(str(password)), name, bool(for_proxy))
        self.items.append(cred)
        return cred

    def remove(self, pattern, for_proxy=False) -> bool:
        pat = normalize_pattern(pattern)
        before = len(self.items)
        self.items = [c for c in self.items if not (c.pattern == pat and c.for_proxy == bool(for_proxy))]
        return len(self.items) != before

    def find(self, url_or_host, for_proxy=False):
        host = host_of(url_or_host)
        if not host:
            return None
        best = None
        best_rank = None
        for cred in self.items:
            if cred.for_proxy != bool(for_proxy) or not host_matches(cred.pattern, host):
                continue
            rank = (1, 0) if not cred.pattern.startswith("*.") else (0, len(cred.pattern))
            if best_rank is None or rank > best_rank:
                best, best_rank = cred, rank
        return best

    def password(self, cred: Credential) -> str:
        return self.unprotect(cred.secret)

    def auth_for(self, url_or_host, for_proxy=False):
        cred = self.find(url_or_host, for_proxy=for_proxy)
        if cred is None:
            return None
        return {
            "scheme": cred.scheme,
            "curl": curl_auth_name(cred.scheme),
            "username": cred.username,
            "password": self.password(cred),
        }

    def to_list(self) -> list:
        return [c.to_dict() for c in self.items]

    @classmethod
    def from_list(cls, data, protect=None, unprotect=None) -> "CredentialStore":
        store = cls(protect=protect, unprotect=unprotect)
        for raw in data or []:
            if not isinstance(raw, dict):
                continue
            try:
                pat = normalize_pattern(raw.get("pattern"))
                name = normalize_scheme(raw.get("scheme", "basic"))
            except ValueError:
                continue
            user = str(raw.get("username") or "").strip()
            secret = raw.get("secret")
            if not user or not isinstance(secret, str):
                continue
            for_proxy = bool(raw.get("for_proxy", False))
            store.items = [c for c in store.items if not (c.pattern == pat and c.for_proxy == for_proxy)]
            store.items.append(Credential(pat, user, secret, name, for_proxy))
        return store