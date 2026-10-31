"""Responses that set cookies must not crash the client (found 2026-10-30:
Python 3.12's cookiejar needs get_all() and request attributes the fakes
lacked), and every Set-Cookie header counts, not just the first."""
import httpx
from curl_cffi.requests import Headers

from opengrab.http_client import HttpClient
from opengrab.settings import Settings


def test_cookies_from_curl_cffi_and_httpx_headers_are_all_kept():
    for headers in (Headers([("Set-Cookie", "sid=abc; Path=/"), ("Set-Cookie", "lang=en; Path=/; Expires=Wed, 21 Oct 2037 07:28:00 GMT")]),
                    httpx.Headers([("Set-Cookie", "sid=abc; Path=/"), ("Set-Cookie", "lang=en; Path=/")])):
        c = HttpClient(Settings())
        c._store_cookies("https://www.example.com/page", headers)
        assert c._cookies_for("https://www.example.com/other") == {"sid": "abc", "lang": "en"}


def test_plain_dict_headers_still_work():
    c = HttpClient(Settings())
    c._store_cookies("https://files.example.org/x", {"set-cookie": "token=t1; Domain=.example.org; Path=/"})
    assert c._cookies_for("https://files.example.org/y")["token"] == "t1"
