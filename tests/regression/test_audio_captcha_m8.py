import subprocess
import shutil
import os
import sys
import tempfile
from types import ModuleType

import pytest

from opengrab.captcha.audio import to_pcm16k, transcribe_vosk, AudioUnavailable
from opengrab.captcha.base import AudioCaptchaSolver
from opengrab.settings import Settings
from opengrab.captcha import CaptchaRequest


# Global for storing fake accept waveform lengths (to avoid scope issues)
class FakeKaldiRecognizer:
    """Fake KaldiRecognizer for testing."""
    accept_waveform_lengths = []  # Track the lengths passed to AcceptWaveform
    
    def __init__(self, model, rate):
        self.model = model
        self.rate = rate
        FakeKaldiRecognizer.accept_waveform_lengths = []  # Reset before each test
        
    def AcceptWaveform(self, b):
        self.accept_waveform_lengths.append(len(b))
        return True
        
    def FinalResult(self):
        return '{"text": "  hello   world "}'


def test_to_pcm16k():
    """Test to_pcm16k function."""
    # Fake subprocess.run with correct return values
    fake_calls = []
    
    def fake_subprocess_run(*args, **kwargs):
        fake_calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], 0, stdout=b"PCM", stderr=b"")
    
    # Test case a: Normal operation with ffmpeg path
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_ffmpeg = os.path.join(tmpdir, "ff.exe")
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(subprocess, 'run', fake_subprocess_run)
            mp.setattr(shutil, 'which', lambda n: fake_ffmpeg)  # Return the fake path
            result = to_pcm16k(b"MP3", ffmpeg=fake_ffmpeg)
                
            assert result == b"PCM"
            # Check the first arg in the command (should be the exe path)
            assert fake_calls[0][0][0] == fake_ffmpeg
            assert "16000" in fake_calls[0][0]
            assert fake_calls[0][1]["input"] == b"MP3"


def test_to_pcm16k_errors():
    """Test error conditions for to_pcm16k."""
    
    # Test case b: ffmpeg failure (non-zero return code)
    def fake_subprocess_run_fail(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout=b"", stderr=b"bad")
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(subprocess, 'run', fake_subprocess_run_fail)
        mp.setattr(shutil, 'which', lambda n: None)  # Not found in PATH
        with pytest.raises(AudioUnavailable, match="ffmpeg failed: bad"):
            to_pcm16k(b"MP3")
    
    # Test case b: timeout error  
    def fake_subprocess_run_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="f", timeout=1)
    
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(subprocess, 'run', fake_subprocess_run_timeout)
        mp.setattr(shutil, 'which', lambda n: None)  # Not found in PATH
        with pytest.raises(AudioUnavailable, match="ffmpeg timeout"):
            to_pcm16k(b"MP3")
    
    # Test case b: ffmpeg not found  
    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(shutil, 'which', lambda n: None)  # Not found in PATH
        with pytest.raises(AudioUnavailable, match="ffmpeg not found"):
            to_pcm16k(b"MP3")


def test_transcribe_vosk():
    """Test transcribe_vosk function."""
    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = os.path.join(tmpdir, "model")
        os.makedirs(model_path)
        
        # Create a fake vosk module
        fake_mod = ModuleType("vosk")
        fake_mod.Model = lambda path: None  # Just need the class to exist
        fake_mod.KaldiRecognizer = FakeKaldiRecognizer
        
        with pytest.MonkeyPatch().context() as mp:
            mp.setitem(sys.modules, "vosk", fake_mod)
            
            # Test the function
            pcm = b"x" * 20000
            result = transcribe_vosk(pcm, model_path)
            
            assert result == "hello world"
            # Check that the AcceptWaveform was called with the correct chunk sizes
            assert FakeKaldiRecognizer.accept_waveform_lengths == [8000, 8000, 4000]


def test_transcribe_vosk_errors():
    """Test error conditions for transcribe_vosk."""
    
    # Test case d: model not found
    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = os.path.join(tmpdir, "missing")
        with pytest.raises(AudioUnavailable, match="vosk model not found"):
            transcribe_vosk(b"test", model_path)
            
    # Test case d: vosk not installed
    with tempfile.TemporaryDirectory() as tmpdir:
        model_path = os.path.join(tmpdir, "model")
        os.makedirs(model_path)
        
        with pytest.MonkeyPatch().context() as mp:
            mp.setitem(sys.modules, "vosk", None)  # Vosk module not found
            with pytest.raises(AudioUnavailable, match="vosk not installed"):
                transcribe_vosk(b"test", model_path)


def test_audio_captcha_solver():
    """Test AudioCaptchaSolver."""
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings()
        
        # Test case e: solver disabled by default
        solver = AudioCaptchaSolver(settings)
        assert not solver.enabled()
        
        # Test case e: solver enabled
        settings.captcha_audio_enabled = True
        settings.captcha_vosk_model_path = str(tmpdir)
        assert solver.enabled()
        
        # Test case e: no audio in request - should fail
        req = CaptchaRequest(kind="audio", extra={})
        result = solver.solve(req)
        assert not result.ok
        assert "no audio" in result.error
        
        # Test case e: audio_b64 is not base64
        req = CaptchaRequest(kind="audio", extra={"audio_b64": "invalid"})
        result = solver.solve(req)
        assert not result.ok
        assert "bad audio data" in result.error
        
        # Test case e: successful solve with mocked transcribe
        def mock_transcribe(data, model_path, ffmpeg=""):
            return "abc"
        
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("opengrab.captcha.audio.transcribe", mock_transcribe)
            
            req = CaptchaRequest(kind="audio", extra={"audio_b64": "VGEz"})  # "VA3" base64 encoded
            result = solver.solve(req)
            assert result.ok
            assert result.text == "abc"
            assert result.provider == "audio"
        
        # Test case e: transcribe raises AudioUnavailable
        def mock_transcribe_2(data, model_path, ffmpeg=""):
            raise AudioUnavailable("vosk not installed")
            
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("opengrab.captcha.audio.transcribe", mock_transcribe_2)
            
            req = CaptchaRequest(kind="audio", extra={"audio_b64": "VGEz"})
            result = solver.solve(req)
            assert not result.ok
            assert "vosk not installed" in result.error
            
        # Test case e: transcribe returns empty
        def mock_transcribe_3(data, model_path, ffmpeg=""):
            return ""
            
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("opengrab.captcha.audio.transcribe", mock_transcribe_3)
            
            req = CaptchaRequest(kind="audio", extra={"audio_b64": "VGEz"})
            result = solver.solve(req)
            assert not result.ok
            assert "no speech recognised" in result.error


def test_solver_chain():
    """Test AudioCaptchaSolver is in the SolverChain."""
    settings = Settings()
    from opengrab.captcha.chain import SolverChain
    
    # Test case f: solver chain contains audio exactly once after tesseract
    chain = SolverChain(settings)
    solvers_ids = [s.id for s in chain.solvers]
    
    assert "audio" in solvers_ids
    assert solvers_ids.count("audio") == 1
    
    # Find positions of audio and tesseract solvers
    tesseract_pos = solvers_ids.index("tesseract")
    audio_pos = solvers_ids.index("audio")
    
    # Audio should come directly after tesseract 
    assert audio_pos == tesseract_pos + 1