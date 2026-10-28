import pytest
from opengrab.captcha.harvest import (
    SCRIPT_URLS, WIDGET_CLASS, TOKEN_FIELDS, UnsupportedKind, build_harvest_page, extract_token,
)

KINDS = ["recaptcha", "hcaptcha", "turnstile"]


@pytest.mark.parametrize("kind", KINDS)
def test_page_has_script_widget_sitekey_callback(kind):
    page = build_harvest_page(kind, "test-sitekey_1", "req123")
    assert f'<script src="{SCRIPT_URLS[kind]}" async defer></script>' in page
    assert f'<div class="{WIDGET_CLASS[kind]}" data-sitekey="test-sitekey_1" data-callback="ogSolved"></div>' in page


def test_page_starts_with_doctype():
    assert build_harvest_page("recaptcha", "k", "r1").startswith("<!DOCTYPE html>")


def test_ogsolved_script_posts_id_and_answer_as_json():
    page = build_harvest_page("hcaptcha", "k", "req123")
    script = page[page.index("function ogSolved(token)"):]
    assert 'fetch("/api/captcha/solve", {' in script
    assert 'method: "POST"' in script
    assert 'headers: {"Content-Type": "application/json"}' in script
    assert 'body: JSON.stringify({id: "req123", answer: token})' in script


def test_ogsolved_script_braces_balanced():
    page = build_harvest_page("turnstile", "k", "req123")
    script = page[page.index("<script>") + 8:page.rindex("</script>")]
    assert script.count("{") == script.count("}")
    assert script.count("(") == script.count(")")


def test_custom_submit_path_used():
    page = build_harvest_page("recaptcha", "k", "r1", "/custom/path")
    assert 'fetch("/custom/path", {' in page
    assert "/api/captcha/solve" not in page


def test_unknown_kind_raises_unsupported_kind():
    with pytest.raises(UnsupportedKind):
        build_harvest_page("unknown", "k", "r1")
    assert issubclass(UnsupportedKind, ValueError)


@pytest.mark.parametrize("sitekey", ["", 'test"key', "test<key", "a" * 101])
def test_bad_sitekey_raises(sitekey):
    with pytest.raises(ValueError):
        build_harvest_page("recaptcha", sitekey, "r1")


def test_sitekey_100_chars_ok():
    assert 'data-sitekey="' + "a" * 100 + '"' in build_harvest_page("recaptcha", "a" * 100, "r1")


@pytest.mark.parametrize("request_id", ["abc-def", ""])
def test_bad_request_id_raises(request_id):
    with pytest.raises(ValueError):
        build_harvest_page("recaptcha", "k", request_id)


def test_submit_path_without_slash_raises():
    with pytest.raises(ValueError):
        build_harvest_page("recaptcha", "k", "r1", "no-leading-slash")


def test_submit_path_is_html_escaped():
    page = build_harvest_page("recaptcha", "k", "r1", '/x"<y')
    assert '/x"<y' not in page
    assert "/x&quot;&lt;y" in page


@pytest.mark.parametrize("kind", KINDS)
def test_extract_token_stripped(kind):
    assert extract_token(kind, {TOKEN_FIELDS[kind]: "  tok_1  "}) == "tok_1"


@pytest.mark.parametrize("kind", KINDS)
def test_extract_token_missing_key_none(kind):
    assert extract_token(kind, {"other": "x"}) is None


@pytest.mark.parametrize("kind", KINDS)
def test_extract_token_whitespace_none(kind):
    assert extract_token(kind, {TOKEN_FIELDS[kind]: "   "}) is None


def test_extract_token_unknown_kind_raises():
    with pytest.raises(UnsupportedKind):
        extract_token("unknown", {})