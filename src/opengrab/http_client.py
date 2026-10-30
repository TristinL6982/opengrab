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
                follow: bool = True, max_bytes: Optional[int] = None,
                detect_challenges: bool = True) -> Response:
        s = self.settings
        extra_headers = headers
        headers = dict(self._base_headers())
        if json_body is not None:
            headers.setdefault("content-type", "application/json")
        # caller headers override the defaults (they were silently dropped before)
        for k, v in (extra_headers or {}).items():
            headers[str(k).lower()] = v
        headers = {k: v for k, v in headers.items() if v is not None}
        cookie_hdr = "; ".join(f"{k}={v}" for k, v in self._cookies_for(url).items())
        if cookie_hdr:
            headers["cookie"] = cookie_hdr
        self._apply_clearance(url, headers)
        timeout = timeout if timeout is not None else s.timeout_s
        proxy = self._proxy_for(url)

        t0 = time.time()
        if HAVE_CURL_CFFI:
            resp = self._request_curl(method, url, data=data, json_body=json_body,
                                      headers=headers, timeout=timeout, proxy=proxy,
                                      follow=follow, max_bytes=max_bytes, t0=t0)
        else:
            resp = self._request_httpx(method, url, data=data, json_body=json_body,
                                       headers=headers, timeout=timeout, proxy=proxy,
                                       follow=follow, max_bytes=max_bytes, t0=t0)
        if detect_challenges:
            from .challenge import raise_for_challenge
            raise_for_challenge(resp.url or url, resp.status_code, resp.headers, resp.content)
        return resp

    # -- W2: Cloudflare clearance and per-host proxy hooks ------------------
    clearance = None  # a clearance.ClearanceStore, set by DownloadManager

    def _apply_clearance(self, url: str, headers: dict) -> bool:
        store = self.clearance
        if store is None:
            return False
        host = urlparse(url).hostname or ""
        c = store.get(host) if host else None
        if c is None:
            return False
        headers["user-agent"] = c.user_agent
        jar = {}
        for part in (headers.get("cookie") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                jar[k] = v
        jar.update(c.cookies)
        headers["cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
        return True

    def _proxy_for(self, url: str):
        return self.settings.proxy or None

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

    # -- streaming (large downloads) -----------------------------------------
    def stream(self, url: str, headers: Optional[dict] = None,
               timeout: Optional[float] = None,
               referer: Optional[str] = None) -> "StreamHandle":
        """Streaming GET for file downloads.  Returns a StreamHandle with
        .status/.headers/.url/.iter_content()/ .close() — the body is NOT
        buffered in memory.  Use for anything that might be big."""
        s = self.settings
        h = dict(self._base_headers(referer))
        for k, v in (headers or {}).items():
            if v is not None:
                h[k] = v
        cookie_hdr = "; ".join(f"{k}={v}"
                               for k, v in self._cookies_for(url).items())
        if cookie_hdr:
            h["cookie"] = cookie_hdr
        self._apply_clearance(url, h)
        timeout = timeout if timeout is not None else max(s.timeout_s, 60.0)
        proxy = self._proxy_for(url)

        if HAVE_CURL_CFFI:
            imp = s.impersonate or "chrome"
            try:
                sess = _crl.Session(impersonate=imp)
            except Exception:
                sess = _crl.Session()
            kw: dict = dict(headers=h, timeout=timeout,
                            allow_redirects=True, stream=True)
            if proxy:
                kw["proxies"] = {"http": proxy, "https": proxy}
            raw = sess.get(url, **kw)
            self._store_cookies(url, raw.headers)
            return StreamHandle(raw, raw.status_code, dict(raw.headers),
                                str(raw.url), owner=sess)
        import httpx
        kw2: dict = dict(verify=True, timeout=timeout, follow_redirects=True)
        if proxy:
            kw2["proxy"] = proxy
        cli = httpx.Client(**kw2)
        cm = cli.stream("GET", url, headers=h)
        raw = cm.__enter__()
        self._store_cookies(url, raw.headers)
        return StreamHandle(raw, raw.status_code, dict(raw.headers),
                            str(raw.url), owner=cli, exit_cm=cm)


class StreamHandle:
    """Streaming response for large downloads (see HttpClient.stream)."""

    def __init__(self, raw, status: int, headers: dict, final_url: str,
                 owner=None, exit_cm=None):
        self._raw = raw
        self.status = status
        self.headers = headers
        self.url = final_url
        self._owner = owner
        self._exit_cm = exit_cm

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def iter_content(self, chunk_size: int = 262144):
        it = self._raw.iter_content(chunk_size=chunk_size) \
            if hasattr(self._raw, "iter_content") else _iter_read(self._raw)
        for chunk in it:
            if chunk:
                yield chunk

    def close(self) -> None:
        try:
            if self._exit_cm is not None:
                self._exit_cm.__exit__(None, None, None)
            elif self._raw is not None and hasattr(self._raw, "close"):
                self._raw.close()
        except Exception:
            pass
        try:
            if self._owner is not None and hasattr(self._owner, "close"):
                self._owner.close()
        except Exception:
            pass


def _iter_read(raw, chunk_size: int = 262144):
    """Fallback iterator for responses without iter_content."""
    while True:
        try:
            piece = raw.read(chunk_size)
        except Exception:
            break
        if not piece:
            break
        yield piece


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
