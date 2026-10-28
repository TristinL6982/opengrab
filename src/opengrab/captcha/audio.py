"""
Offline audio captcha transcriber using Vosk.

This module provides functions to convert audio captcha data into text.
"""

import subprocess
import shutil
import os
import json
import base64


class AudioUnavailable(Exception):
    """Raised when audio processing fails due to missing tools or resources."""
    pass


def to_pcm16k(data: bytes, ffmpeg: str = "", timeout: float = 30.0) -> bytes:
    """Convert audio data to 16kHz PCM format using ffmpeg.
    
    Args:
        data: Audio data bytes
        ffmpeg: Path to ffmpeg executable (or empty to use PATH)
        timeout: Timeout in seconds
        
    Returns:
        PCM audio data bytes at 16kHz, mono
        
    Raises:
        AudioUnavailable: If conversion fails
    """
    exe = ffmpeg or shutil.which("ffmpeg") or ""
    if not exe:
        raise AudioUnavailable("ffmpeg not found")
    
    try:
        p = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
            input=data,
            capture_output=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise AudioUnavailable("ffmpeg timeout")
    except OSError as e:
        raise AudioUnavailable(str(e))
    
    if p.returncode != 0 or not p.stdout:
        raise AudioUnavailable("ffmpeg failed: " + p.stderr.decode("utf-8", "replace")[:200])
        
    return p.stdout


def transcribe_vosk(pcm: bytes, model_path: str) -> str:
    """Transcribe PCM audio using Vosk.
    
    Args:
        pcm: PCM audio data
        model_path: Path to Vosk model
        
    Returns:
        Transcribed text
        
    Raises:
        AudioUnavailable: If transcription fails
    """
    if not model_path or not os.path.isdir(model_path):
        raise AudioUnavailable("vosk model not found")
        
    try:
        import vosk
    except ImportError:
        raise AudioUnavailable("vosk not installed")
        
    model = vosk.Model(model_path)
    rec = vosk.KaldiRecognizer(model, 16000)
    
    # Process audio in chunks
    for i in range(0, len(pcm), 8000):
        rec.AcceptWaveform(pcm[i:i+8000])
        
    res = json.loads(rec.FinalResult())
    return " ".join(str(res.get("text", "")).split())


def transcribe(data: bytes, model_path: str, ffmpeg: str = "") -> str:
    """Full audio transcription pipeline.
    
    Args:
        data: Audio data bytes
        model_path: Path to Vosk model
        ffmpeg: Path to ffmpeg executable (or empty to use PATH)
        
    Returns:
        Transcribed text
    """
    return transcribe_vosk(to_pcm16k(data, ffmpeg), model_path)