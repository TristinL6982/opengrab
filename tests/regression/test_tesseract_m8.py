import pytest
from opengrab.captcha.base import CaptchaRequest, CaptchaResult
from opengrab.settings import Settings

def test_tesseract_solver(monkeypatch):
    # Create a settings object with tesseract enabled
    s = Settings()
    s.captcha_tesseract_enabled = True
    s.captcha_tesseract_path = "C:\\fake\\tesseract.exe"
    
    # Mock the os.path.isfile to make it return True for our fake path
    def mock_isfile(path):
        return path == "C:\\fake\\tesseract.exe"
    
    monkeypatch.setattr("os.path.isfile", mock_isfile)
    
    # Import TesseractSolver after setting up the mock
    from opengrab.captcha.base import TesseractSolver
    
    # Test: enabled() should be True with proper setup
    solver = TesseractSolver(s)
    assert solver.enabled() == True
    
    # Test: find_binary with fake path
    binary_path = solver.find_binary()
    assert binary_path == "C:\\fake\\tesseract.exe"
    
    # Test: enabled() should be False when the path doesn't exist
    monkeypatch.setattr("os.path.isfile", lambda p: False)
    assert solver.enabled() == False
    
    # Test: enabled() should be True again when path exists
    monkeypatch.setattr("os.path.isfile", mock_isfile)
    assert solver.enabled() == True
    
    # Reset for next test
    monkeypatch.setattr("os.path.isfile", mock_isfile)
    
    # Mock subprocess.run to simulate a successful OCR
    fake_calls = []
    
    def fake_subprocess_run(args, input=None, capture_output=True, timeout=None):
        class MockProcess:
            def __init__(self):
                self.returncode = 0
                self.stdout = b" ab 12\n"
                self.stderr = b""
        
        fake_calls.append((args, {'input': input, 'capture_output': capture_output, 'timeout': timeout}))
        return MockProcess()
    
    monkeypatch.setattr("subprocess.run", fake_subprocess_run)
    
    # Test: successful solve
    req = CaptchaRequest(image_b64="QUJD", kind="text")
    result = solver.solve(req)
    assert result.ok == True
    assert result.text == "ab12"
    assert result.provider == "tesseract" 
    assert len(fake_calls) == 1
    assert fake_calls[0][0][0] == "C:\\fake\\tesseract.exe"
    assert fake_calls[0][0][1:5] == ["stdin", "stdout", "--psm", "7"]
    assert fake_calls[0][1]['input'] == b"ABC"
    
    # Test: empty output
    def fake_subprocess_run_empty(args, input=None, capture_output=True, timeout=None):
        class MockProcess:
            def __init__(self):
                self.returncode = 0
                self.stdout = b"\n"
                self.stderr = b""
        return MockProcess()
    
    monkeypatch.setattr("subprocess.run", fake_subprocess_run_empty)
    result = solver.solve(req)
    assert result.ok == False
    assert "OCR returned empty" in result.error
    
    # Test: nonzero exit code
    def fake_subprocess_run_fail(args, input=None, capture_output=True, timeout=None):
        class MockProcess:
            def __init__(self):
                self.returncode = 1
                self.stdout = b""
                self.stderr = b"boom"
        return MockProcess()
    
    monkeypatch.setattr("subprocess.run", fake_subprocess_run_fail)
    result = solver.solve(req)
    assert result.ok == False
    assert "tesseract failed: boom" in result.error
    
    # Test: timeout
    def fake_subprocess_run_timeout(args, input=None, capture_output=True, timeout=None):
        from subprocess import TimeoutExpired
        raise TimeoutExpired("tesseract", 1)
    
    monkeypatch.setattr("subprocess.run", fake_subprocess_run_timeout)
    result = solver.solve(req)
    assert result.ok == False
    assert "tesseract timeout" in result.error
    
    # Test: no image
    req_no_image = CaptchaRequest(image_b64="", kind="text")
    result = solver.solve(req_no_image)
    assert result.ok == False
    assert "no image to OCR" in result.error
