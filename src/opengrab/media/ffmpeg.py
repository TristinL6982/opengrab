from __future__ import annotations

import os
import shutil
import subprocess

"""
Media helpers (yt-dlp formats, ffmpeg merge, HLS/DASH); every external tool is optional.
"""

class MediaUnavailable(Exception):
    """ffmpeg is missing or cannot be started."""

class MergeError(Exception):
    """ffmpeg ran but the merge failed (bad input, non-zero exit, timeout, empty output)."""

def find_ffmpeg(path: str = "", which=None) -> str:
    if path:
        return path if os.path.isfile(path) else ""
    w = which if which is not None else shutil.which
    return w("ffmpeg") or ""

def available(path: str = "", which=None) -> bool:
    return bool(find_ffmpeg(path, which))

def merge_cmd(exe: str, video: str, audio: str, out: str) -> list[str]:
    return [
        exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        video,
        "-i",
        audio,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        out
    ]

def temp_path(out: str) -> str:
    base, ext = os.path.splitext(out)
    return base + ".merging" + ext

def merge(
    video: str,
    audio: str,
    out: str,
    ffmpeg: str = "",
    run=None,
    timeout: float = 600.0,
    which=None,
) -> str:
    exe = find_ffmpeg(ffmpeg, which)
    if not exe:
        raise MediaUnavailable("ffmpeg not found")

    for p in (video, audio):
        if not os.path.isfile(p):
            raise MergeError("missing input: " + p)

    run = run if run is not None else subprocess.run
    tmp = temp_path(out)

    try:
        proc = run(merge_cmd(exe, video, audio, tmp), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _discard(tmp)
        raise MergeError("ffmpeg timeout")
    except OSError as e:
        _discard(tmp)
        raise MediaUnavailable(str(e))

    if proc.returncode != 0:
        _discard(tmp)
        raise MergeError(
            "ffmpeg failed: "
            + (proc.stderr or b"").decode("utf-8", "replace")[:200]
        )

    if not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
        _discard(tmp)
        raise MergeError("ffmpeg produced no output")

    os.replace(tmp, out)
    return out

def _discard(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass