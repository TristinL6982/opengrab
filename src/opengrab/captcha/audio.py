"""Offline transcriber for audio captchas (PROJECT_SPEC.md section 6d).

Turns the bytes of an audio challenge (for example the MP3 behind a
reCAPTCHA audio button) into text, fully offline:

1. ``to_pcm16k`` uses ffmpeg to convert any input format to 16 kHz mono
   signed 16-bit PCM.
2. ``transcribe_vosk`` feeds that PCM to a Vosk speech-recognition model.

Both tools are optional. ffmpeg is looked up on PATH unless a path is given,
and vosk is imported lazily, so OpenGrab works normally without them; every
problem is reported as ``AudioUnavailable`` so callers can fall back to the
next solver.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess


class AudioUnavailable(Exception):
    """Raised when audio cannot be transcribed (missing tool, model or bad input)."""


def to_pcm16k(data: bytes, ffmpeg: str = "", timeout: float = 30.0) -> bytes:
    """Convert audio bytes to raw 16 kHz mono s16le PCM with ffmpeg."""
    exe = ffmpeg or shutil.which("ffmpeg") or ""
    if not exe:
        raise AudioUnavailable("ffmpeg not found")
    try:
        p = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
            input=data, capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise AudioUnavailable("ffmpeg timeout")
    except OSError as e:
        raise AudioUnavailable(str(e))
    if p.returncode != 0 or not p.stdout:
        raise AudioUnavailable("ffmpeg failed: " + (p.stderr or b"").decode("utf-8", "replace")[:200])
    return p.stdout


def transcribe_vosk(pcm: bytes, model_path: str) -> str:
    """Transcribe 16 kHz mono PCM with the Vosk model in ``model_path``."""
    if not model_path or not os.path.isdir(model_path):
        raise AudioUnavailable("vosk model not found")
    try:
        import vosk
    except ImportError:
        raise AudioUnavailable("vosk not installed")
    try:
        model = vosk.Model(model_path)
    except Exception as e:  # vosk raises a bare Exception for an invalid model folder
        raise AudioUnavailable("vosk model failed to load: " + str(e)[:200])
    rec = vosk.KaldiRecognizer(model, 16000)
    for i in range(0, len(pcm), 8000):
        rec.AcceptWaveform(pcm[i:i + 8000])
    res = json.loads(rec.FinalResult())
    return " ".join(str(res.get("text", "")).split())


def transcribe(data: bytes, model_path: str, ffmpeg: str = "") -> str:
    """Convert ``data`` with ffmpeg and transcribe it with Vosk."""
    return transcribe_vosk(to_pcm16k(data, ffmpeg), model_path)