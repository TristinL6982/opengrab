import pytest
from unittest.mock import Mock, MagicMock

from opengrab.captcha.base import NopeCHASolver
from opengrab.settings import Settings


class FakeResp:
    def __init__(self, text, json_resp):
        self.text = text
        self._json_resp = json_resp
    
    def json(self):
        return self._json_resp


class FakeClient:
    def __init__(self):
        self.posts = []
        self.gets = []
        self.next_gets = []
    
    def post(self, url, json_body=None, **kw):
        self.posts.append((url, json_body))
        # Return a mock response
        # For text image type, this is the default that will be overridden 
        # by the specific test cases - but we need to keep consistency with 
        # how it's called in the real solver
        return FakeResp('{"data": "J1"}', {"data": "J1"})
    
    def get(self, url, **kw):
        self.gets.append(url)
        # Return the next response from our queue
        if self.next_gets:
            return self.next_gets.pop(0)
        # Default response for first call - this is not actually used for tests  
        return FakeResp('{"data": "TOKEN"}', {"data": "TOKEN"})


def test_enabled():
    """Test that NopeCHASolver is enabled when key is set"""
    s = Settings()
    s.captcha_nopecha_key = ""
    solver = NopeCHASolver(s)
    assert not solver.enabled()
    
    s.captcha_nopecha_key = "K"
    solver = NopeCHASolver(s)
    assert solver.enabled()


def test_recaptcha():
    """Test solving a reCAPTCHA with NopeCHA"""
    s = Settings()
    s.captcha_nopecha_key = "K"
    solver = NopeCHASolver(s)
    
    # Set up fake client
    client = FakeClient()
    
    # Mock the get response to return error 14 then success
    fake_resp1 = FakeResp('{"error": 14}', {"error": 14})
    fake_resp2 = FakeResp('{"data": "TOKEN"}', {"data": "TOKEN"})
    client.next_gets = [fake_resp1, fake_resp2]
    
    # Create request for recaptcha
    req = Mock()
    req.kind = "recaptcha"
    req.recaptcha_sitekey = "SK"
    req.site_url = "https://x.com/p"
    req.image_b64 = ""
    
    # Set poll interval to 0 for testing
    solver.poll_interval = 0
    
    result = solver.solve(req, client=client)
    
    assert result.ok is True
    assert result.text == "TOKEN"
    assert result.provider == "nopecha"
    assert len(client.posts) == 1
    assert client.posts[0][0] == "https://api.nopecha.com/token/"
    assert client.posts[0][1]["key"] == "K"
    assert client.posts[0][1]["type"] == "recaptcha2"
    assert client.posts[0][1]["sitekey"] == "SK"
    assert client.posts[0][1]["url"] == "https://x.com/p"
    
    # Verify we made two GET calls
    assert len(client.gets) == 2
    assert client.gets[0] == "https://api.nopecha.com/token/?key=K&id=J1"


def test_text_image():
    """Test solving a text image captcha with NopeCHA"""
    s = Settings()
    s.captcha_nopecha_key = "K"
    solver = NopeCHASolver(s)
    
    # Set up fake client
    client = FakeClient()
    
    # Mock response for GET (success case)
    fake_resp = FakeResp('{"data": ["abcd"]}', {"data": ["abcd"]})
    client.next_gets = [fake_resp]
    
    # Create request for text image
    req = Mock()
    req.kind = "text"
    req.recaptcha_sitekey = ""
    req.site_url = ""
    req.image_b64 = "QUJD"
    
    # Set poll interval to 0 for testing
    solver.poll_interval = 0
    
    result = solver.solve(req, client=client)
    
    assert result.text == "abcd"
    assert len(client.posts) == 1
    assert client.posts[0][0] == "https://api.nopecha.com/"
    assert client.posts[0][1]["image_data"] == ["QUJD"]
    assert client.posts[0][1]["type"] == "textcaptcha"


def test_submit_error():
    """Test handling of submit error"""
    s = Settings()
    s.captcha_nopecha_key = "K"
    solver = NopeCHASolver(s)
    
    # Set up fake client that returns an error
    client = FakeClient()
    # Override post to return error
    def mock_post(url, json_body=None, **kw):
        client.posts.append((url, json_body))
        return FakeResp('{"error": 10, "message": "Invalid key"}', {"error": 10, "message": "Invalid key"})
    
    # Mock the post method in client
    original_post = client.post
    client.post = mock_post
    
    # Create request for text image
    req = Mock()
    req.kind = "text"
    req.recaptcha_sitekey = ""
    req.site_url = ""
    req.image_b64 = "QUJD"
    
    result = solver.solve(req, client=client)
    
    assert result.ok is False
    assert result.error == "Invalid key"
    # Should not make any GET calls due to POST error
    assert len(client.gets) == 0


def test_poll_error():
    """Test handling of poll error"""
    s = Settings()
    s.captcha_nopecha_key = "K"
    solver = NopeCHASolver(s)
    
    # Set up fake client  
    client = FakeClient()
    
    # Mock first response is success for POST, and a subsequent error in the polling
    # But we must make sure it fails on the second poll, not on first one
    fake_resp1 = FakeResp('{"data": "J3"}', {"data": "J3"})  # For POST - success job creation
    fake_resp2 = FakeResp('{"error": 11, "message": "Out of credit"}', {"error": 11, "message": "Out of credit"})
    client.next_gets = [fake_resp1, fake_resp2]
    
    # Create request for text image
    req = Mock()
    req.kind = "text"
    req.recaptcha_sitekey = ""
    req.site_url = ""
    req.image_b64 = "QUJD"
    
    # Set poll interval to 0 for testing
    solver.poll_interval = 0
    
    # The solver should have an error here
    result = solver.solve(req, client=client)
    
    assert result.ok is False
    assert result.error == "Out of credit"


def test_nothing_to_send():
    """Test handling when nothing to send"""
    s = Settings()
    s.captcha_nopecha_key = "K"
    solver = NopeCHASolver(s)
    
    # Create request for text but with no image
    req = Mock()
    req.kind = "text"
    req.recaptcha_sitekey = ""
    req.site_url = ""
    req.image_b64 = ""
    # Explicitly set image_url to empty string to avoid Mock issues with urllib.parse
    req.image_url = ""
    
    result = solver.solve(req)
    
    assert result.ok is False
    assert result.error == "nothing to send"
    # No posts should happen
    # assert len(getattr(solver, 'posts', [])) == 0  # We can't even check this due to client being auto-created in the case of no image


def test_solver_chain():
    """Test that NopeCHASolver is in the solver chain correctly"""
    from opengrab.captcha.chain import SolverChain
    
    s = Settings()
    s.captcha_nopecha_key = "K"
    
    chain = SolverChain(s)
    
    # Find all solvers by ID
    solver_ids = [s.id for s in chain.solvers]
    
    # Should contain 'capsolver' and 'nopecha'
    assert "capsolver" in solver_ids
    assert "nopecha" in solver_ids
    
    # Check that nopecha comes right after capsolver (based on priority)
    capsolver_index = solver_ids.index("capsolver")
    nopecha_index = solver_ids.index("nopecha") 
    assert nopecha_index == capsolver_index + 1