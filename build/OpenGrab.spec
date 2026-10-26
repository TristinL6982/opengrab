# PyInstaller onedir build. Run from this directory with:
#   py -3.12 -m PyInstaller --clean OpenGrab.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
UI = SRC / "opengrab" / "ui" / "static"

datas = [(str(UI), "ui/static")]
binaries = []
hiddenimports = ["webview", "pystray", "PIL", "curl_cffi", "yt_dlp", "ddddocr"]
for package in ("yt_dlp", "ddddocr", "curl_cffi", "webview", "pystray"):
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
    console=True,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="OpenGrab")
