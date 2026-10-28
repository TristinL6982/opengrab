import pytest
import time
import threading
from unittest.mock import patch
from opengrab.captcha.inbox import CaptchaInbox, CaptchaRequest, InboxFull


def test_submit_and_get():
    inbox = CaptchaInbox()
    request = inbox.submit("example.com", "hcaptcha", "https://example.com")
    
    assert request.id is not None
    assert request.host == "example.com"
    assert request.kind == "hcaptcha"
    assert request.page_url == "https://example.com"
    assert request.status == "pending"
    assert request.answer is None
    
    retrieved = inbox.get(request.id)
    assert retrieved is not None
    assert retrieved.id == request.id
    

def test_dedupe_same_host_kind():
    inbox = CaptchaInbox()
    
    # Submit first request
    req1 = inbox.submit("example.com", "hcaptcha", "https://example.com")
    
    # Submit second request with same host and kind
    req2 = inbox.submit("example.com", "hcaptcha", "https://example.com/someotherpage")
    
    # Should return the same request (deduplication)
    assert req1.id == req2.id


def test_no_dedupe_different_kind():
    inbox = CaptchaInbox()
    
    # Submit first request
    req1 = inbox.submit("example.com", "hcaptcha", "https://example.com")
    
    # Submit second request with same host but different kind
    req2 = inbox.submit("example.com", "recaptcha", "https://example.com")
    
    # Should return different requests (no deduplication)
    assert req1.id != req2.id


def test_inbox_full():
    inbox = CaptchaInbox(max_pending=1)
    
    # Submit one request
    inbox.submit("example.com", "hcaptcha", "https://example.com")
    
    # Try to submit another - should raise InboxFull
    with pytest.raises(InboxFull):
        inbox.submit("example2.com", "hcaptcha", "https://example2.com")


def test_pending_order():
    inbox = CaptchaInbox()
    
    # Submit requests in order
    req1 = inbox.submit("example.com", "hcaptcha", "https://example.com/1")
    time.sleep(0.01)  # Small delay to ensure different timestamps
    # different host, otherwise submit dedupes it into req1
    req2 = inbox.submit("example2.com", "hcaptcha", "https://example2.com/2")
    
    pending = inbox.pending()
    assert len(pending) == 2
    assert pending[0].id == req1.id
    assert pending[1].id == req2.id


def test_expiry_marks_expired_and_drops_from_pending():
    inbox = CaptchaInbox()
    
    # Submit request with short timeout
    req = inbox.submit("example.com", "hcaptcha", "https://example.com", timeout_s=0.01)
    
    time.sleep(0.02)  # Wait for expiration
    
    # Check that the request is now expired
    assert req.is_expired()
    
    # Check that it's no longer in pending
    pending = inbox.pending()
    assert len(pending) == 0
    assert req.status == "expired"


def test_solve_success():
    inbox = CaptchaInbox()
    req = inbox.submit("example.com", "hcaptcha", "https://example.com")
    
    # Solve the request
    success = inbox.solve(req.id, "solution123")
    assert success is True
    assert req.status == "solved"
    assert req.answer == "solution123"


def test_solve_empty_answer_fails():
    inbox = CaptchaInbox()
    req = inbox.submit("example.com", "hcaptcha", "https://example.com")
    
    # Try to solve with empty answer
    success = inbox.solve(req.id, "")
    assert success is False
    assert req.status == "pending"
    assert req.answer is None


def test_solve_on_expired_fails():
    inbox = CaptchaInbox()
    
    # Submit request with short timeout
    req = inbox.submit("example.com", "hcaptcha", "https://example.com", timeout_s=0.01)
    
    time.sleep(0.02)  # Wait for expiration
    
    # Try to solve expired request
    success = inbox.solve(req.id, "solution123")
    assert success is False
    assert req.status == "expired"
    assert req.answer is None


def test_skip():
    inbox = CaptchaInbox()
    req = inbox.submit("example.com", "hcaptcha", "https://example.com")
    
    # Skip the request
    success = inbox.skip(req.id)
    assert success is True
    assert req.status == "skipped"


def test_solve_after_skip_fails():
    inbox = CaptchaInbox()
    req = inbox.submit("example.com", "hcaptcha", "https://example.com")
    
    # Skip the request
    inbox.skip(req.id)
    
    # Try to solve skipped request
    success = inbox.solve(req.id, "solution123")
    assert success is False
    assert req.status == "skipped"
    assert req.answer is None


def test_wait_returns_after_solve_from_another_thread():
    inbox = CaptchaInbox()
    req = inbox.submit("example.com", "hcaptcha", "https://example.com")
    
    result = [None]
    
    def solve_later():
        time.sleep(0.05)
        inbox.solve(req.id, "solution123")
    
    # Start solving in another thread
    thread = threading.Thread(target=solve_later)
    thread.start()
    
    # Wait for it to complete
    result[0] = inbox.wait(req.id, timeout=2.0)
    
    thread.join()
    
    assert result[0] is not None
    assert result[0].status == "solved"
    assert result[0].answer == "solution123"


def test_wait_timeout_still_pending():
    inbox = CaptchaInbox()
    req = inbox.submit("example.com", "hcaptcha", "https://example.com")
    
    # Wait with very short timeout
    result = inbox.wait(req.id, timeout=0.01)
    
    assert result is not None
    assert result.status == "pending"


def test_wait_unknown_id():
    inbox = CaptchaInbox()
    
    # Try to wait for non-existent request
    result = inbox.wait("nonexistent", timeout=0.01)
    
    assert result is None


def test_purge_count():
    inbox = CaptchaInbox()
    
    # Submit requests
    # distinct hosts so submit does not dedupe them
    req1 = inbox.submit("a.example", "hcaptcha", "https://a.example/1")
    req2 = inbox.submit("b.example", "hcaptcha", "https://b.example/2")
    solved = inbox.submit("c.example", "hcaptcha", "https://c.example/3")
    assert inbox.solve(solved.id, "answer")
    expired_req = inbox.submit("d.example", "hcaptcha", "https://d.example/expired", timeout_s=0.01)
    time.sleep(0.02)  # Wait for expiration

    # Purge removes the solved one and the expired one, keeps the two pending
    purged_count = inbox.purge()
    assert purged_count == 2
    assert inbox.get(solved.id) is None
    assert inbox.get(expired_req.id) is None
    assert inbox.get(req1.id) is not None and inbox.get(req2.id) is not None
    assert len(inbox) == 2


def test_to_dict_keys():
    inbox = CaptchaInbox()
    req = inbox.submit("example.com", "hcaptcha", "https://example.com")
    
    req_dict = req.to_dict()
    
    expected_keys = {'id', 'host', 'kind', 'sitekey', 'page_url', 'created_at', 'timeout_s', 'status', 'answer'}
    assert set(req_dict.keys()) == expected_keys