"""Finish a merge-mode media download: fetch the audio part, merge with ffmpeg (M9d)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from . import ffmpeg as media_ffmpeg


def audio_part(extra: dict) -> dict | None:
    """Return the audio part dict when extra describes a merge, else None."""
    if not extra or extra.get("media_mode") != "merge":
        return None
    for part in extra.get("media_parts") or []:
        if part.get("kind") == "audio" and part.get("url"):
            return part
    return None


def merge_job(video_path: Path, extra: dict, fetch: Callable[[str, Path], None],
              ffmpeg_path: str = "", merge_fn=None) -> Path:
    """Download the audio part next to video_path and merge it in.

    fetch(url, dest) must write the audio file to dest. Returns the final path.
    On any merge failure the video file is kept, the audio temp is removed and
    extra["media_merge"] records {"status": "failed", "message": ...}.
    """
    video_path = Path(video_path)
    part = audio_part(extra)
    if part is None:
        return video_path
    merge_fn = merge_fn or media_ffmpeg.merge
    ext = str(extra.get("media_ext") or video_path.suffix.lstrip(".") or "mp4")
    audio_ext = str(part.get("ext") or "m4a")
    audio_path = video_path.with_name(video_path.stem + ".audio." + audio_ext)
    final = video_path.with_suffix("." + ext)
    out = final if final != video_path else video_path.with_name(video_path.stem + ".merged." + ext)
    try:
        fetch(str(part["url"]), audio_path)
        merge_fn(str(video_path), str(audio_path), str(out), ffmpeg=ffmpeg_path)
    except (media_ffmpeg.MediaUnavailable, media_ffmpeg.MergeError, OSError) as e:
        _remove(audio_path)
        extra["media_merge"] = {"status": "failed", "message": str(e)[:300]}
        return video_path
    _remove(audio_path)
    if out != final:
        os.replace(out, final)
    else:
        _remove(video_path)
    extra["media_merge"] = {"status": "ok"}
    return final


def _remove(path: Path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass