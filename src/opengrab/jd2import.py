"""M14: import from JDownloader 2 (read only; JD2's files are never changed).

Reads a JDownloader 2 folder's cfg/:
* downloadList<N>.zip   - the download list: entries "PP" are packages
  (name, downloadFolder), "PP_LL" are links (url, name, size,
  finalLinkState, properties.FINAL_FILENAME / URL_ORIGIN).
* linkcollector<N>.zip  - the LinkGrabber: "PP_LL" entries wrap a
  ``downloadLink`` with the url.
* org.jdownloader.settings.GeneralSettings.json - download folder, parallel
  downloads, chunks per file, speed limit, per-host limit.
The highest N is JD2's current list. Accounts (*.accounts.ejs) are stored
encrypted by JD2 and are not imported.
"""
from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

GENERAL = "org.jdownloader.settings.GeneralSettings.json"


def candidate_dirs() -> list[Path]:
    env = os.environ
    roots = [env.get("LOCALAPPDATA", ""), env.get("APPDATA", ""), env.get("PROGRAMFILES", ""),
             env.get("PROGRAMFILES(X86)", ""), str(Path.home())]
    names = ["JDownloader 2", "JDownloader 2.0", "JDownloader v2.0", "JDownloader", "jd2"]
    out = []
    for r in roots:
        for n in names:
            p = Path(r) / n if r else None
            if p and (p / "cfg").is_dir() and p not in out:
                out.append(p)
    return out


def _latest(cfg: Path, prefix: str) -> Optional[Path]:
    best, best_n = None, -1
    for p in cfg.glob(prefix + "*.zip"):
        m = re.fullmatch(re.escape(prefix) + r"(\d+)\.zip", p.name)
        if m and int(m.group(1)) > best_n:
            best, best_n = p, int(m.group(1))
    return best


@dataclass
class JdLink:
    url: str
    name: str = ""
    size: int = 0
    finished: bool = False
    package: str = ""


@dataclass
class JdExport:
    folder: str
    downloads: list = field(default_factory=list)
    linkgrabber: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


def _entries(zpath: Path) -> tuple[dict, dict]:
    packages, links = {}, {}
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():
            if not re.fullmatch(r"\d+(_\d+)?", name):
                continue
            try:
                data = json.loads(z.read(name).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if "_" in name:
                links[name] = data
            else:
                packages[name] = data
    return packages, links


def read_download_list(zpath: Path) -> list[JdLink]:
    packages, links = _entries(zpath)
    out = []
    for key in sorted(links, key=lambda k: [int(x) for x in k.split("_")]):
        d = links[key]
        props = d.get("properties") or {}
        url = str(props.get("URL_ORIGIN") or d.get("url") or "")
        if not url.lower().startswith(("http://", "https://", "magnet:")):
            url = str(d.get("url") or "")
        if not url:
            continue
        pkg = packages.get(key.split("_")[0], {})
        out.append(JdLink(url=url, name=str(props.get("FINAL_FILENAME") or d.get("name") or ""),
                          size=int(d.get("size") or props.get("VERIFIEDFILESIZE") or 0),
                          finished=str(d.get("finalLinkState") or "").upper().startswith("FINISHED"),
                          package=str(pkg.get("name") or "")))
    return out


def read_linkcollector(zpath: Path) -> list[JdLink]:
    packages, links = _entries(zpath)
    out = []
    for key in sorted(links, key=lambda k: [int(x) for x in k.split("_")]):
        d = links[key]
        dl = d.get("downloadLink") or {}
        url = str(dl.get("url") or next(iter(d.get("sourceUrls") or []), "") or "")
        if not url:
            continue
        pkg = packages.get(key.split("_")[0], {})
        out.append(JdLink(url=url, name=str(dl.get("name") or ""), size=int(dl.get("size") or 0),
                          package=str(pkg.get("name") or "")))
    return out


def read_settings(cfg: Path) -> dict:
    p = cfg / GENERAL
    if not p.is_file():
        return {}
    g = json.loads(p.read_text(encoding="utf-8"))
    out: dict = {}
    folder = str(g.get("defaultdownloadfolder") or "")
    if folder and "<jd:" not in folder:
        out["download_dir"] = folder
    if isinstance(g.get("maxsimultanedownloads"), int) and g["maxsimultanedownloads"] > 0:
        out["max_parallel"] = min(32, g["maxsimultanedownloads"])
    if isinstance(g.get("maxchunksperfile"), int) and g["maxchunksperfile"] > 0:
        out["per_link_chunks"] = min(16, g["maxchunksperfile"])
    if g.get("downloadspeedlimitenabled") and isinstance(g.get("downloadspeedlimit"), int):
        out["speed_limit_kbps"] = max(1, g["downloadspeedlimit"] // 1024)
    if g.get("maxdownloadsperhostenabled") and isinstance(g.get("maxsimultanedownloadsperhost"), int):
        out["max_per_host"] = max(0, g["maxsimultanedownloadsperhost"])
    return out


def read_export(folder) -> JdExport:
    root = Path(folder)
    cfg = root / "cfg" if (root / "cfg").is_dir() else root
    exp = JdExport(folder=str(root))
    if not cfg.is_dir():
        exp.errors.append("no cfg folder found")
        return exp
    if not (_latest(cfg, "downloadList") or _latest(cfg, "linkcollector") or (cfg / GENERAL).is_file()):
        exp.errors.append("no JDownloader 2 cfg files found in this folder")
        return exp
    for prefix, reader, target in (("downloadList", read_download_list, exp.downloads),
                                   ("linkcollector", read_linkcollector, exp.linkgrabber)):
        z = _latest(cfg, prefix)
        if z is None:
            continue
        try:
            target.extend(reader(z))
        except (OSError, zipfile.BadZipFile) as exc:
            exp.errors.append(f"{z.name}: {exc}")
    try:
        exp.settings = read_settings(cfg)
    except (OSError, ValueError) as exc:
        exp.errors.append(f"{GENERAL}: {exc}")
    if any(cfg.glob("*accounts*.ejs")):
        exp.errors.append("JDownloader 2 stores account passwords encrypted; add your accounts again in OpenGrab")
    return exp
