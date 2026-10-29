"""HLS (m3u8) playlist parsing helpers (M9e). Standard library only."""
from __future__ import annotations

import re
from urllib.parse import urljoin

_ATTR = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def parse_attrs(s: str) -> dict:
    """Parse an HLS attribute list like A=1,B="x,y" into a dict."""
    out = {}
    for k, v in _ATTR.findall(s):
        if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        out[k] = v
    return out


def is_master(text: str) -> bool:
    return "#EXT-X-STREAM-INF" in text


def parse_master(text: str, base_url: str) -> list:
    """Return the variant streams of a master playlist."""
    variants = []
    pending = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = parse_attrs(line.split(":", 1)[1])
            continue
        if line.startswith("#"):
            continue
        if pending is None:
            continue
        width = height = 0
        m = re.fullmatch(r"(\d+)x(\d+)", pending.get("RESOLUTION", ""))
        if m:
            width, height = int(m.group(1)), int(m.group(2))
        bw = pending.get("BANDWIDTH", "0")
        variants.append({
            "url": urljoin(base_url, line),
            "bandwidth": int(bw) if bw.isdigit() else 0,
            "width": width,
            "height": height,
            "codecs": pending.get("CODECS", ""),
        })
        pending = None
    return variants


def pick_variant(variants: list, max_height: int = 0):
    """Best variant not taller than max_height (0 = no limit).

    Falls back to the smallest variant when none fits; None when empty.
    """
    if not variants:
        return None
    pool = [v for v in variants if not max_height or v["height"] <= max_height]
    if not pool:
        return min(variants, key=lambda v: (v["height"], v["bandwidth"]))
    return max(pool, key=lambda v: (v["height"], v["bandwidth"]))


def parse_media(text: str, base_url: str) -> dict:
    """Parse a media playlist into segments plus playlist info."""
    segments = []
    key = None
    init = ""
    target = 0
    seq = 0
    endlist = False
    dur = 0.0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-TARGETDURATION:"):
            target = int(float(line.split(":", 1)[1]))
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-KEY:"):
            a = parse_attrs(line.split(":", 1)[1])
            method = a.get("METHOD", "NONE")
            if method == "NONE":
                key = None
            else:
                key = {"method": method, "uri": urljoin(base_url, a.get("URI", "")),
                       "iv": a.get("IV", "")}
        elif line.startswith("#EXT-X-MAP:"):
            init = urljoin(base_url, parse_attrs(line.split(":", 1)[1]).get("URI", ""))
        elif line.startswith("#EXTINF:"):
            dur = float(line.split(":", 1)[1].split(",", 1)[0])
        elif line.startswith("#EXT-X-ENDLIST"):
            endlist = True
        elif line.startswith("#"):
            continue
        else:
            segments.append({"url": urljoin(base_url, line), "duration": dur,
                             "seq": seq + len(segments), "key": key})
            dur = 0.0
    return {
        "segments": segments,
        "init": init,
        "target_duration": target,
        "media_sequence": seq,
        "endlist": endlist,
        "duration": round(sum(s["duration"] for s in segments), 3),
    }
