"""Quality presets and yt-dlp format selection; merging needs ffmpeg, so without it only progressive formats are used."""
from __future__ import annotations

PRESETS: dict[str, dict] = {"best": {"kind": "video", "max_height": 0}, "2160": {"kind": "video", "max_height": 2160}, "1440": {"kind": "video", "max_height": 1440}, "1080": {"kind": "video", "max_height": 1080}, "720": {"kind": "video", "max_height": 720}, "480": {"kind": "video", "max_height": 480}, "audio-m4a": {"kind": "audio", "ext": "m4a"}, "audio-mp3": {"kind": "audio", "ext": "mp3"}}

class NoFormat(Exception): """No format matches the preset."""

def preset_names() -> list[str]: return list(PRESETS)

def _has_video(f) -> bool: return f.get("vcodec") != "none"

def _has_audio(f) -> bool: return f.get("acodec") != "none"

def _height(f) -> int: return f.get("height") or 0

def _vscore(f) -> tuple[int, int]: return (_height(f), f.get("tbr") or 0)

def _ascore(f) -> float: return (f.get("abr") or f.get("tbr") or 0)

def _merged_ext(v, a) -> str:
    ve, ae = v.get("ext"), a.get("ext")
    if ve == "mp4" and ae in ("m4a", "mp4"): return "mp4"
    if ve == "webm" and ae == "webm": return "webm"
    return "mkv"

def select_formats(info: dict, preset: str = "best", can_merge: bool = False) -> dict:
    if preset not in PRESETS: raise ValueError("unknown preset: " + str(preset))
    p = PRESETS[preset]
    formats = [f for f in (info.get("formats") or []) if f.get("url")]
    audios = [f for f in formats if _has_audio(f) and not _has_video(f)]
    if p["kind"] == "audio":
        if p["ext"] == "mp3" and not can_merge: raise NoFormat("mp3 needs ffmpeg")
        if not audios: raise NoFormat("no audio-only format")
        same = [f for f in audios if f.get("ext") == p["ext"]]
        a = max(same or audios, key=_ascore)
        return {"mode": "audio", "video": None, "audio": a, "ext": p["ext"]}
    cap = p["max_height"]
    def fits(f): return not cap or _height(f) <= cap
    prog = [f for f in formats if _has_video(f) and _has_audio(f) and fits(f)]
    videos = [f for f in formats if _has_video(f) and not _has_audio(f) and fits(f)]
    best_prog = max(prog, key=_vscore) if prog else None
    if can_merge and videos and audios:
        v = max(videos, key=_vscore)
        if best_prog is None or _height(v) > _height(best_prog):
            pool = [f for f in audios if f.get("ext") in ("m4a", "mp4")] if v.get("ext") == "mp4" else []
            a = max(pool or audios, key=_ascore)
            return {"mode": "merge", "video": v, "audio": a, "ext": _merged_ext(v, a)}
    if best_prog is not None: return {"mode": "progressive", "video": best_prog, "audio": None, "ext": best_prog.get("ext") or "mp4"}
    raise NoFormat("no format at or below %dp" % cap if cap else "no downloadable format")