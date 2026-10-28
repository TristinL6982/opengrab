"""M8m: offline audio captcha transcriber (ffmpeg + Vosk) and AudioCaptchaSolver.

ffmpeg and vosk are never run: subprocess.run and the vosk module are faked.
pytest's tmp_path is broken on the dev PC, so folders come from tempfile.mkdtemp.
"""
import shutil
import subprocess
import sys
import tempfile
import types

import pytest

from opengrab.captcha import audio
from opengrab.captcha.audio import AudioUnavailable, to_pcm16k, transcribe_vosk
from opengrab.captcha.base import AudioCaptchaSolver, CaptchaRequest
from opengrab.captcha.chain import SolverChain
from opengrab.settings import Settings


def _fake_run(calls, returncode=0, stdout=b"", stderr=b""):
    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args[0], returncode, stdout=stdout, stderr=stderr)
    return fake


def _fake_vosk(lengths, models):
    mod = types.ModuleType("vosk")

    class Model:
        def __init__(self, path):
            models.append(path)

    class KaldiRecognizer:
        def __init__(self, model, rate):
            self.rate = rate

        def AcceptWaveform(self, b):
            lengths.append(len(b))
            return True

        def FinalResult(self):
            return '{"text": "  hello   world "}'

    mod.Model = Model
    mod.KaldiRecognizer = KaldiRecognizer
    return mod


# a
def test_to_pcm16k(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, 0, stdout=b"PCM"))
    assert to_pcm16k(b"MP3", ffmpeg="C:\\ff.exe") == b"PCM"
    args, kwargs = calls[0]
    assert args[0][0] == "C:\\ff.exe"
    assert "16000" in args[0]
    assert kwargs["input"] == b"MP3"


# b
def test_to_pcm16k_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, 1, stderr=b"bad"))
    with pytest.raises(AudioUnavailable, match="ffmpeg failed: bad"):
        to_pcm16k(b"MP3", ffmpeg="C:\\ff.exe")

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="f", timeout=1)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    with pytest.raises(AudioUnavailable, match="ffmpeg timeout"):
        to_pcm16k(b"MP3", ffmpeg="C:\\ff.exe")

    monkeypatch.setattr(shutil, "which", lambda n: None)
    with pytest.raises(AudioUnavailable, match="ffmpeg not found"):
        to_pcm16k(b"MP3", ffmpeg="")


# b2: a zero exit code with no PCM output is still a failure
def test_to_pcm16k_empty_output(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, 0, stdout=b"", stderr=b"silent"))
    with pytest.raises(AudioUnavailable, match="ffmpeg failed: silent"):
        to_pcm16k(b"MP3", ffmpeg="C:\\ff.exe")


# b3: ffmpeg path that cannot be executed
def test_to_pcm16k_oserror(monkeypatch):
    def raise_oserror(*args, **kwargs):
        raise OSError("cannot execute")

    monkeypatch.setattr(subprocess, "run", raise_oserror)
    with pytest.raises(AudioUnavailable, match="cannot execute"):
        to_pcm16k(b"MP3", ffmpeg="C:\\ff.exe")


# c
def test_transcribe_vosk(monkeypatch):
    model_dir = tempfile.mkdtemp()
    lengths, models = [], []
    monkeypatch.setitem(sys.modules, "vosk", _fake_vosk(lengths, models))
    assert transcribe_vosk(b"x" * 20000, model_dir) == "hello world"
    assert lengths == [8000, 8000, 4000]
    assert models == [model_dir]


# d
def test_transcribe_vosk_errors(monkeypatch):
    model_dir = tempfile.mkdtemp()
    with pytest.raises(AudioUnavailable, match="vosk model not found"):
        transcribe_vosk(b"...", model_dir + "\\missing")
    monkeypatch.setitem(sys.modules, "vosk", None)
    with pytest.raises(AudioUnavailable, match="vosk not installed"):
        transcribe_vosk(b"...", model_dir)


# d2: an invalid model folder makes vosk.Model raise; that must become AudioUnavailable
def test_transcribe_vosk_bad_model(monkeypatch):
    model_dir = tempfile.mkdtemp()
    mod = types.ModuleType("vosk")

    def bad_model(path):
        raise Exception("Failed to create a model")

    mod.Model = bad_model
    monkeypatch.setitem(sys.modules, "vosk", mod)
    with pytest.raises(AudioUnavailable, match="vosk model failed to load: Failed to create a model"):
        transcribe_vosk(b"...", model_dir)


# transcribe() chains ffmpeg conversion into vosk
def test_transcribe_pipeline(monkeypatch):
    model_dir = tempfile.mkdtemp()
    calls, lengths, models = [], [], []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, 0, stdout=b"y" * 9000))
    monkeypatch.setitem(sys.modules, "vosk", _fake_vosk(lengths, models))
    assert audio.transcribe(b"MP3", model_dir, "C:\\ff.exe") == "hello world"
    assert calls[0][0][0][0] == "C:\\ff.exe"
    assert lengths == [8000, 1000]


# e
def test_audio_captcha_solver(monkeypatch):
    s = Settings()
    solver = AudioCaptchaSolver(s)
    assert solver.enabled() is False
    s.captcha_audio_enabled = True
    s.captcha_vosk_model_path = tempfile.mkdtemp()
    assert solver.enabled() is True

    r = solver.solve(CaptchaRequest(kind="audio", extra={}))
    assert r.ok is False
    assert r.error == "no audio"

    r = solver.solve(CaptchaRequest(kind="audio", extra={"audio_b64": "invalid"}))
    assert r.ok is False
    assert r.error == "bad audio data"

    seen = []

    def ok_transcribe(d, m, f=""):
        seen.append((d, m, f))
        return "abc"

    monkeypatch.setattr(audio, "transcribe", ok_transcribe)
    r = solver.solve(CaptchaRequest(kind="audio", extra={"audio_b64": "TVAz"}))
    assert r.ok is True
    assert r.text == "abc"
    assert r.provider == "audio"
    assert seen == [(b"MP3", s.captcha_vosk_model_path, "")]

    def unavailable(d, m, f=""):
        raise AudioUnavailable("vosk not installed")

    monkeypatch.setattr(audio, "transcribe", unavailable)
    r = solver.solve(CaptchaRequest(kind="audio", extra={"audio_b64": "TVAz"}))
    assert r.ok is False
    assert r.error == "vosk not installed"

    monkeypatch.setattr(audio, "transcribe", lambda d, m, f="": "")
    r = solver.solve(CaptchaRequest(kind="audio", extra={"audio_b64": "TVAz"}))
    assert r.ok is False
    assert r.error == "no speech recognised"


# f
def test_solver_chain_order():
    ids = [x.id for x in SolverChain(Settings()).solvers]
    assert ids.count("audio") == 1
    assert ids.index("audio") == ids.index("tesseract") + 1