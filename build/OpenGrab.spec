# PyInstaller onedir build. Run from this directory with:
#   py -3.12 -m PyInstaller --clean OpenGrab.spec
from pathlib import Path
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
UI = SRC / "opengrab" / "ui" / "static"

datas = [(str(UI), "ui/static"), (os.path.join(SPECPATH, "..", "src", "opengrab", "assets"), "opengrab/assets"), (os.path.join(SPECPATH, "opengrab.ico"), "opengrab.ico"), (str(SRC / "opengrab" / "plugins" / "templates"), "opengrab/plugins/templates"), (str(SRC / "opengrab" / "addons_lock.json"), "opengrab")]
binaries = []
hiddenimports = ["webview", "pystray", "PIL", "curl_cffi", "yt_dlp", "py7zr", "rarfile", "opengrab.smoke", "opengrab.engine.archives", "opengrab.engine.packagizer"]
# builtin plugins are discovered with pkgutil at runtime, so PyInstaller cannot see them statically
sys.path.insert(0, str(SRC))
hiddenimports += collect_submodules("opengrab.plugins")
for package in ("yt_dlp", "curl_cffi", "webview", "pystray", "py7zr"):
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
    hookspath=[], hooksconfig={}, runtime_hooks=[], noarchive=False,
    # Spec section 2: no ML packages in the base app. Local OCR is an add-on
    # downloaded on opt-in (src/opengrab/addons.py).
    excludes=["ddddocr", "onnxruntime", "cv2", "numpy", "torch", "tensorflow",
              "libtorrent"],  # torrent engine is an add-on too (W11)
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="OpenGrab",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False,
    icon=os.path.join(SPECPATH, "opengrab.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="OpenGrab")
