"""M9g: download a static DASH (MPD) stream with injected fetchers (standard library only)."""
from .dash import DashError, parse_mpd, pick_audio, pick_video


def download_rep(rep, dest_path, fetch_bytes, on_part=None, cancel=None):
    """Write a representation's init segment, then every media segment, into dest_path."""
    urls = ([rep["init"]] if rep.get("init") else []) + list(rep["segments"])
    written = 0
    with open(dest_path, "wb") as fh:
        for u in urls:
            if cancel is not None and cancel():
                raise DashError("cancelled")
            data = fetch_bytes(u)
            fh.write(data)
            written += len(data)
            if on_part is not None:
                on_part(len(data))
    return written


def download_dash(url, video_path, audio_path, fetch_bytes, fetch_text,
                  max_height=0, on_progress=None, cancel=None):
    """Download the best video and audio tracks of a static DASH stream to two files.

    Returns {"video": path or None, "audio": path or None, "segments": n,
    "bytes": n, "duration": seconds}.
    """
    info = parse_mpd(fetch_text(url), url)
    video = pick_video(info["representations"], max_height)
    audio = pick_audio(info["representations"])
    if video is None and audio is None:
        raise DashError("no audio or video tracks")
    jobs = [(r, p) for r, p in ((video, video_path), (audio, audio_path)) if r is not None]
    total = sum(len(r["segments"]) + (1 if r.get("init") else 0) for r, _ in jobs)
    state = {"done": 0, "bytes": 0}

    def part(n):
        state["done"] += 1
        state["bytes"] += n
        if on_progress is not None:
            on_progress(state["done"], total, state["bytes"])

    for rep, path in jobs:
        download_rep(rep, path, fetch_bytes, part, cancel)
    return {"video": video_path if video is not None else None,
            "audio": audio_path if audio is not None else None,
            "segments": total, "bytes": state["bytes"], "duration": info["duration"]}