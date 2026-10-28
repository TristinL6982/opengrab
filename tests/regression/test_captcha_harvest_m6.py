import pytest
from opengrab.captcha.harvest import build_harvest_page, extract_token, UnsupportedKind

# Test build_harvest_page
@pytest.mark.parametrize("kind", ["recaptcha", "hcaptcha", "turnstile"])
def test_build_harvest_page_script_urls(kind):
    page = build_harvest_page(kind, "test-sitekey", "testrequestid")
    assert f'src="https://www.google.com/recaptcha/api.js"' in page or \
           f'src="https://js.hcaptcha.com/1/api.js"' in page or \
           f'src="https://challenges.cloudflare.com/turnstile/v0/api.js"' in page

@pytest.mark.parametrize("kind", ["recaptcha", "hcaptcha", "turnstile"])
def test_build_harvest_page_widget_class(kind):
    page = build_harvest_page(kind, "test-sitekey", "testrequestid")
    assert f'class="{kind}-captcha"' in page or \
           f'class="g-recaptcha"' in page or \
           f'class="h-captcha"' in page or \
           f'class="cf-turnstile"' in page

def test_build_harvest_page_data_sitekey():
    page = build_harvest_page("recaptcha", "test-sitekey", "testrequestid")
    assert 'data-sitekey="test-sitekey"' in page

def test_build_harvest_page_data_callback():
    page = build_harvest_page("recaptcha", "test-sitekey", "testrequestid")
    assert 'data-callback="ogSolved"' in page

def test_build_harvest_page_doctype():
    page = build_harvest_page("recaptcha", "test-sitekey", "testrequestid")
    assert page.startswith("<!DOCTYPE html>")

def test_build_harvest_page_request_id_in_script():
    page = build_harvest_page("recaptcha", "test-sitekey", "testrequestid")
    assert 'id: "testrequestid"' in page

def test_build_harvest_page_submit_path_in_script():
    page = build_harvest_page("recaptcha", "test-sitekey", "testrequestid", "/custom/path")
    assert '/custom/path' in page

def test_build_harvest_page_unknown_kind_raises_unsupported_kind():
    with pytest.raises(UnsupportedKind):
        build_harvest_page("unknown", "test-sitekey", "testrequestid")

def test_build_harvest_page_invalid_sitekey_raises_value_error():
    with pytest.raises(ValueError):
        build_harvest_page("recaptcha", "", "testrequestid")
        
    with pytest.raises(ValueError):
        build_harvest_page("recaptcha", "a" * 101, "testrequestid")
        
    with pytest.raises(ValueError):
        build_harvest_page("recaptcha", 'test"key', "testrequestid")

def test_build_harvest_page_invalid_request_id_raises_value_error():
    with pytest.raises(ValueError):
        build_harvest_page("recaptcha", "test-sitekey", "abc-def")
        
    with pytest.raises(ValueError):
        build_harvest_page("recaptcha", "test-sitekey", "")

def test_build_harvest_page_invalid_submit_path_raises_value_error():
    with pytest.raises(ValueError):
        build_harvest_page("recaptcha", "test-sitekey", "testrequestid", "no-leading-slash")

# Test extract_token
def test_extract_token_returns_stripped_token_recaptcha():
    form = {"g-recaptcha-response": "  token_value  "}
    result = extract_token("recaptcha", form)
    assert result == "token_value"

def test_extract_token_returns_stripped_token_hcaptcha():
    form = {"h-captcha-response": "  token_value  "}
    result = extract_token("hcaptcha", form)
    assert result == "token_value"

def test_extract_token_returns_stripped_token_turnstile():
    form = {"cf-turnstile-response": "  token_value  "}
    result = extract_token("turnstile", form)
    assert result == "token_value"

def test_extract_token_returns_none_for_missing_key():
    form = {}
    result = extract_token("recaptcha", form)
    assert result is None

def test_extract_token_returns_none_for_whitespace_only_value_recaptcha():
    form = {"g-recaptcha-response": "   "}
    result = extract_token("recaptcha", form)
    assert result is None

def test_extract_token_returns_none_for_whitespace_only_value_hcaptcha():
    form = {"h-captcha-response": "   "}
    result = extract_token("hcaptcha", form)
    assert result is None

def test_extract_token_returns_none_for_whitespace_only_value_turnstile():
    form = {"cf-turnstile-response": "   "}
    result = extract_token("turnstile", form)
    assert result is None

def test_extract_token_unknown_kind_raises_unsupported_kind():
    with pytest.raises(UnsupportedKind):
        extract_token("unknown", {})