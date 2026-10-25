"""Anti-bot HTTP client.

Uses curl_cffi to present a real browser TLS/HTTP2 fingerprint
(`impersonate="chrome"` etc.) which defeats most passive bot detection
that fingerprint-checks JA3/JA4, HTTP2 settings, header order and AKAMAI
tokens.  Falls back to plain httpx when curl_cffi is unavailable.

Every request gets:
  * a browser-like UA (or rotation from the configured pool)
  * the configured extra headers (accept-language, referer templates, ...)
  * optional proxy
  * optional cookie jar (per-domain persistence)
"""
from __future__ import annotations

import random
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from .settings import Settings

try:
    from curl_cffi import requests as _crl
    HAVE_CURL_CFFI = True
except Exception:  # pragma: no cover
    _crl = None
    HAVE_CURL_CFFI = False

DEFAULT_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]


class Response:
    """Small response wrapper so callers don't depend on the backend lib."""

    def __init__(self, status: int, headers: dict, content: bytes, url: str,
                 cookies: dict | None = None, elapsed: float = 0.0):
        self.status_code = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.content = content
        self.url = url
        self.cookies = cookies or {}
        self.elapsed = elapsed

    @property
    def text(self) -> str:
        try:
            return self.content.decode("utf-8", errors="replace")
        except Exception:
            return ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def json(self):
        import json as _json
        return _json.loads(self.text)


class HttpClient:
    """Thread-safe-ish wrapper; each call may use a fresh underlying session
    (curl_cffi sessions are not thread-safe). Cookie jars are shared per-domain.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._cookie_jars: dict[str, dict[str, str]] = {}
        self._jars_lock = threading.Lock()
        self._ua_lock = threading.Lock()
        self._last_ua = ""

    # -- headers -----------------------------------------------------------
    def _ua(self) -> str:
        s = self.settings
        if s.user_agent:
            return s.user_agent
        if s.ua_rotation and s.ua_pool:
            pool = s.ua_pool
        elif s.ua_rotation:
            pool = DEFAULT_UAS
        else:
            pool = None
        if pool:
            with self._ua_lock:
                ua = random.choice(pool)
                if ua == self._last_ua and len(pool) > 1:
                    ua = random.choice([u for u in pool if u != self._last_ua] or pool)
                self._last_ua = ua
            return ua
        return DEFAULT_UAS[0]

    def _base_headers(self, referer: Optional[str] = None) -> dict[str, str]:
        h = {
            "user-agent": self._ua(),
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            "upgrade-insecure-requests": "1",
        }
        h.update(self.settings.extra_headers or {})
        if referer:
            h["referer"] = referer
        return h

    # -- cookies -----------------------------------------------------------
    def _domain(self, url: str) -> str:
        return urlparse(url).netloc or url

    def _cookies_for(self, url: str) -> dict[str, str]:
        with self._jars_lock:
            return dict(self._cookie_jars.get(self._domain(url), {}))

    def _store_cookies(self, url: str, headers) -> None:
        import http.cookiejar as _cj
        raw = None
        if hasattr(headers, "getlist"):
            raw = headers.getlist("set-cookie")
        elif hasattr(headers, "get_all"):
            raw = headers.get_all("set-cookie")
        else:
            raw = [headers.get("set-cookie")] if headers.get("set-cookie") else []
        if not raw:
            return
        jar = _cj.CookieJar()
        fake_resp = _FakeResponse(raw)
        jar.extract_cookies(fake_resp, _FakeRequest(url))
        with self._jars_lock:
            store = self._cookie_jars.setdefault(self._domain(url), {})
            for c in jar:
                store[c.name] = c.value

    def set_cookie(self, url: str, name: str, value: str) -> None:
        with self._jars_lock:
            self._cookie_jars.setdefault(self._domain(url), {})[name] = value

    # -- core --------------------------------------------------------------
    def request(self, method: str, url: str, *, data=None, json_body=None,
                headers: Optional[dict] = None, timeout: Optional[float] = None,
                follow: bool = True, max_bytes: Optional[int] = None) -> Response:
        s = self.settings
        headers = dict(self._base_headers())
        if json_body is not None:
            headers.setdefault("content-type", "application/json")
        (headers or {}).update(headers or {})
        headers = {k: v for k, v in headers.items() if v is not None}
        cookie_hdr = "; ".join(f"{k}={v}" for k, v in self._cookies_for(url).items())
        if cookie_hdr:
            headers["cookie"] = cookie_hdr
        timeout = timeout if timeout is not None else s.timeout_s
        proxy = s.proxy or None

        t0 = time.time()
        if HAVE_CURL_CFFI:
            return self._request_curl(method, url, data=data, json_body=json_body,
                                       headers=headers, timeout=timeout, proxy=proxy,
                                       follow=follow, max_bytes=max_bytes, t0=t0)
        return self._request_httpx(method, url, data=data, json_body=json_body,
                                   headers=headers, timeout=timeout, proxy=proxy,
                                   follow=follow, max_bytes=max_bytes, t0=t0)

    def _request_curl(self, method, url, data, json_body, headers, timeout,
                      proxy, follow, max_bytes, t0) -> Response:
        import json as _json
        imp = self.settings.impersonate or "chrome"
        try:
            sess = _crl.Session(impersonate=imp)
        except Exception:
            sess = _crl.Session()
        kw: dict = dict(headers=headers, timeout=timeout, allow_redirects=follow)
        if proxy:
            kw["proxies"] = {"http": proxy, "https": proxy}
        if json_body is not None:
            kw["json"] = json_body
        elif data is not None:
            kw["data"] = data
        m = getattr(sess, method.lower())
        resp = m(url, **kw)
        content = resp.content
        if max_bytes is not None and len(content) > max_bytes:
            content = content[:max_bytes]
        self._store_cookies(url, resp.headers)
        return Response(resp.status_code, dict(resp.headers), content,
                        str(resp.url), elapsed=time.time() - t0)

    def _request_httpx(self, method, url, data, json_body, headers, timeout,
                       proxy, follow, max_bytes, t0) -> Response:  # pragma: no cover
        import httpx
        kw: dict = dict(headers=headers, timeout=timeout, follow_redirects=follow)
        if proxy:
            kw["proxy"] = proxy
        if json_body is not None:
            kw["json"] = json_body
        elif data is not None:
            kw["content"] = data
        with httpx.Client(verify=True) as cli:
            r = cli.request(method.upper(), url, **kw)
            content = r.content
            if max_bytes is not None and len(content) > max_bytes:
                content = content[:max_bytes]
            self._store_cookies(url, r.headers)
            return Response(r.status_code, dict(r.headers), content,
                            str(r.url), elapsed=time.time() - t0)

    def get(self, url: str, **kw) -> Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> Response:
        return self.request("POST", url, **kw)


class _FakeRequest:
    def __init__(self, url: str):
        self.url = url

    def get_full_url(self):
        return self.url

    def get_host(self):
        return urlparse(self.url).netloc

    def get_type(self):
        return urlparse(self.url).scheme or "http"

    def is_verifiable(self):
        return False


class _FakeResponse:
    def __init__(self, set_cookie_values):
        self._sc = set_cookie_values

    def getheaders(self):
        return [("Set-Cookie", v) for v in self._sc]

    def info(self):
        return self

    def get(self, k, default=None):
        if k.lower() == "set-cookie":
            return self._sc[0] if self._sc else default
        return default
