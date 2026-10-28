# PyInstaller onedir build. Run from this directory with:
#   py -3.12 -m PyInstaller --clean OpenGrab.spec
from pathlib import Path
import os
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
UI = SRC / "opengrab" / "ui" / "static"

datas = [(str(UI), "ui/static"), (os.path.join(SPECPATH, "..", "src", "opengrab", "assets"), "opengrab/assets")]
binaries = []
hiddenimports = ["webview", "pystray", "PIL", "curl_cffi", "yt_dlp", "ddddocr", "py7zr", "rarfile", "opengrab.smoke", "opengrab.engine.archives", "opengrab.engine.packagizer"]
for package in ("yt_dlp", "ddddocr", "curl_cffi", "webview", "pystray", "py7zr"):
    pkg_datas, pkg_bins, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_bins
    hiddenimports += pkg_hidden

a = Analysis(
    [str(ROOT / "build" / "entrypoint.py")],
    pathex=[str(SRC), str(ROOT / "build")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="OpenGrab",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False,
    icon=os.path.join(SPECPATH, "opengrab.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="OpenGrab")
