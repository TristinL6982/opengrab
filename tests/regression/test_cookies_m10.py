"""M10b: cookie login helpers."""
from opengrab.cookies import Cookie, cookies_for_url, parse_header, parse_netscape, to_header

SAMPLE = (
    "# Netscape HTTP Cookie File\n"
    ".example.com\tTRUE\t/\tFALSE\t0\tsid\tabc\n"
    "#HttpOnly_.example.com\tTRUE\t/\tTRUE\t2000000000\ttok\txyz\n"
    "files.example.com\tFALSE\t/dl\tFALSE\t100\told\tgone\n"
    "bad line without tabs\n"
    "\n"
    "other.org\tFALSE\t/\tFALSE\t0\tempty\t\n"
)


def names(cs):
    return [c.name for c in cs]


def test_parse_netscape_skips_comments_and_bad_lines():
    assert names(parse_netscape(SAMPLE)) == ["sid", "tok", "old", "empty"]


def test_httponly_prefix_secure_and_expiry():
    tok = parse_netscape(SAMPLE)[1]
    assert tok.domain == "example.com"
    assert tok.secure is True
    assert tok.expires == 2000000000
    assert tok.include_subdomains is True


def test_empty_value_and_host_only_flag():
    cs = parse_netscape(SAMPLE)
    assert cs[3].value == ""
    assert cs[2].include_subdomains is False
    assert cs[2].path == "/dl"


def test_crlf_and_bad_expiry():
    assert parse_netscape("example.com\tFALSE\t/\tFALSE\t0\tk\tv\r\n")[0].value == "v"
    assert parse_netscape("x.com\tFALSE\t/\tFALSE\tsoon\tk\tv\n") == []
    assert parse_netscape("") == []


def test_subdomain_and_secure_on_https():
    cs = parse_netscape(SAMPLE)
    assert names(cookies_for_url(cs, "https://www.example.com:8443/x", now=1000)) == ["sid", "tok"]


def test_secure_cookie_not_sent_over_http():
    cs = parse_netscape(SAMPLE)
    assert names(cookies_for_url(cs, "http://example.com/", now=1000)) == ["sid"]


def test_host_only_path_and_expiry():
    cs = parse_netscape(SAMPLE)
    assert names(cookies_for_url(cs, "http://files.example.com/dl/a", now=50)) == ["sid", "old"]
    assert names(cookies_for_url(cs, "http://files.example.com/dl", now=50)) == ["sid", "old"]
    assert names(cookies_for_url(cs, "http://files.example.com/dl/a", now=100)) == ["sid"]
    assert names(cookies_for_url(cs, "http://files.example.com/download", now=50)) == ["sid"]
    assert names(cookies_for_url(cs, "http://a.files.example.com/dl/a", now=50)) == ["sid"]


def test_other_domain_not_matched():
    cs = parse_netscape(SAMPLE)
    assert names(cookies_for_url(cs, "https://notexample.com/", now=0)) == []
    assert names(cookies_for_url(cs, "http://other.org", now=0)) == ["empty"]


def test_parse_header_and_to_header():
    cs = parse_header("a=1; b = 2 ;junk; c=x=y", ".Example.com")
    assert names(cs) == ["a", "b", "c"]
    assert cs[2].value == "x=y"
    assert cs[0].domain == "example.com"
    assert to_header(cs) == "a=1; b=2; c=x=y"


def test_to_header_empty_and_manual_cookie():
    assert to_header([]) == ""
    c = Cookie(domain="example.com", name="k", value="v")
    assert names(cookies_for_url([c], "https://sub.example.com/p", now=0)) == ["k"]