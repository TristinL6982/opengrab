"""M9g: minimal MPEG-DASH (.mpd) manifest parsing (standard library only)."""
import math
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin


class DashError(Exception):
    pass


def _local(tag):
    return str(tag).rsplit("}", 1)[-1]


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _base(el, base):
    b = _child(el, "BaseURL")
    if b is not None and (b.text or "").strip():
        return urljoin(base, b.text.strip())
    return base


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


_DUR = re.compile(r"^P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?"
                  r"(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def parse_duration(text):
    """ISO 8601 duration such as PT1M30.5S to seconds."""
    t = (text or "").strip()
    m = _DUR.match(t)
    if not m or t in ("P", "PT"):
        raise DashError("bad duration: %r" % text)
    d, h, mi, s = (float(x) if x else 0.0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


_TOKEN = re.compile(r"\$(RepresentationID|Number|Bandwidth|Time)(%0\d+d)?\$|\$\$")


def fill_template(tmpl, rep_id="", bandwidth=0, number=None, time=None):
    """Expand $RepresentationID$, $Bandwidth$, $Number$, $Time$ and $$."""
    values = {"RepresentationID": rep_id, "Bandwidth": bandwidth,
              "Number": number, "Time": time}

    def sub(m):
        if m.group(0) == "$$":
            return "$"
        val = values[m.group(1)]
        if val is None:
            raise DashError("template needs $%s$" % m.group(1))
        fmt = m.group(2)
        if fmt and m.group(1) != "RepresentationID":
            return fmt % int(val)
        return str(val)

    return _TOKEN.sub(sub, tmpl)


def _from_template(attrs, timeline, base, rid, bw, total):
    media = attrs.get("media")
    if not media:
        raise DashError("SegmentTemplate without media")
    start = int(attrs.get("startNumber") or 1)
    init = None
    if attrs.get("initialization"):
        init = urljoin(base, fill_template(attrs["initialization"], rid, bw))
    segs = []
    if timeline is not None:
        t = 0
        n = start
        for s in _children(timeline, "S"):
            if s.get("t") is not None:
                t = int(s.get("t"))
            d = int(s.get("d"))
            r = int(s.get("r") or 0)
            if r < 0:
                raise DashError("open-ended segment timeline is not supported")
            for _ in range(r + 1):
                segs.append(urljoin(base, fill_template(media, rid, bw, n, t)))
                t += d
                n += 1
        return init, segs
    dur = int(attrs.get("duration") or 0)
    if dur <= 0 or total <= 0:
        raise DashError("cannot count segments")
    scale = int(attrs.get("timescale") or 1)
    count = max(1, math.ceil(total * scale / dur - 1e-9))
    for i in range(count):
        segs.append(urljoin(base, fill_template(media, rid, bw, start + i, None)))
    return init, segs


def _segments(aset, rep, base, rid, bw, total):
    rt, at = _child(rep, "SegmentTemplate"), _child(aset, "SegmentTemplate")
    if rt is not None or at is not None:
        attrs = {}
        for t in (at, rt):
            if t is not None:
                attrs.update(t.attrib)
        timeline = None
        for t in (rt, at):
            if t is not None and _child(t, "SegmentTimeline") is not None:
                timeline = _child(t, "SegmentTimeline")
                break
        return _from_template(attrs, timeline, base, rid, bw, total)
    sl = _child(rep, "SegmentList")
    if sl is None:
        sl = _child(aset, "SegmentList")
    if sl is not None:
        ini = _child(sl, "Initialization")
        init = None
        if ini is not None and ini.get("sourceURL"):
            init = urljoin(base, ini.get("sourceURL"))
        segs = [urljoin(base, s.get("media") or "") for s in _children(sl, "SegmentURL")]
        return init, segs
    return None, [base]


def _rep(aset, rep, base, total):
    def attr(name):
        v = rep.get(name)
        return v if v is not None else aset.get(name)

    mime = attr("mimeType") or ""
    ctype = (aset.get("contentType") or "").lower() or mime.split("/")[0].lower()
    kind = ctype if ctype in ("video", "audio") else "other"
    rid = rep.get("id") or ""
    bw = _int(rep.get("bandwidth"))
    init, segs = _segments(aset, rep, base, rid, bw, total)
    return {"id": rid, "kind": kind, "mime": mime, "codecs": attr("codecs") or "",
            "bandwidth": bw, "width": _int(attr("width")),
            "height": _int(attr("height")), "init": init, "segments": segs}


def parse_mpd(text, url):
    """Parse a static MPD; return {"duration": seconds, "representations": [...]}."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise DashError("bad manifest") from exc
    if _local(root.tag) != "MPD":
        raise DashError("bad manifest")
    if (root.get("type") or "static").lower() == "dynamic":
        raise DashError("live manifests are not supported")
    for el in root.iter():
        if _local(el.tag) == "ContentProtection":
            raise DashError("encrypted streams are not supported")
    period = _child(root, "Period")
    if period is None:
        raise DashError("no Period")
    total = 0.0
    if period.get("duration"):
        total = parse_duration(period.get("duration"))
    elif root.get("mediaPresentationDuration"):
        total = parse_duration(root.get("mediaPresentationDuration"))
    base = _base(period, _base(root, url))
    reps = []
    for aset in _children(period, "AdaptationSet"):
        abase = _base(aset, base)
        for rep in _children(aset, "Representation"):
            reps.append(_rep(aset, rep, _base(rep, abase), total))
    return {"duration": total, "representations": reps}


def pick_video(reps, max_height=0):
    """Best video representation, at or below max_height when given."""
    vids = [r for r in reps if r["kind"] == "video"]
    if not vids:
        return None
    if max_height and max_height > 0:
        fit = [r for r in vids if r["height"] <= max_height]
        if not fit:
            return min(vids, key=lambda r: (r["height"], r["bandwidth"]))
        vids = fit
    return max(vids, key=lambda r: (r["height"], r["bandwidth"]))


def pick_audio(reps):
    """Highest-bandwidth audio representation, or None."""
    auds = [r for r in reps if r["kind"] == "audio"]
    return max(auds, key=lambda r: r["bandwidth"]) if auds else None