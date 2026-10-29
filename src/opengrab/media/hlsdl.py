"""HLS segment downloader (M9f). Standard library only; network access is injected."""
from __future__ import annotations

from opengrab.media.hls import is_master, parse_master, parse_media, pick_variant


class HlsError(Exception):
    pass


def resolve_media(url, fetch_text, max_height=0):
    """Follow a master playlist to one variant; return (media_url, parsed media playlist)."""
    text = fetch_text(url)
    if is_master(text):
        variant = pick_variant(parse_master(text, url), max_height)
        if variant is None:
            raise HlsError("master playlist has no variants")
        url = variant["url"]
        text = fetch_text(url)
    return url, parse_media(text, url)


def download_hls(url, dest_path, fetch_bytes, fetch_text, max_height=0,
                 on_progress=None, cancel=None):
    """Download every segment of a finished (VOD) HLS stream into dest_path, in order."""
    media_url, info = resolve_media(url, fetch_text, max_height)
    if not info["endlist"]:
        raise HlsError("live playlists are not supported")
    segments = info["segments"]
    if not segments:
        raise HlsError("playlist has no segments")
    for seg in segments:
        if seg["key"] is not None:
            raise HlsError("encrypted HLS (%s) is not supported yet" % seg["key"]["method"])
    total = len(segments)
    written = 0
    with open(dest_path, "wb") as out:
        if info["init"]:
            data = fetch_bytes(info["init"])
            out.write(data)
            written += len(data)
        for i, seg in enumerate(segments, 1):
            if cancel is not None and cancel():
                raise HlsError("cancelled")
            data = fetch_bytes(seg["url"])
            out.write(data)
            written += len(data)
            if on_progress is not None:
                on_progress(i, total, written)
    return {"media_url": media_url, "segments": total, "bytes": written,
            "duration": info["duration"]}