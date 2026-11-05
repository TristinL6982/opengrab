"""Default apps: OpenGrab registers as a capable app for magnet links and .torrent
files (the supported route; UserChoice is only read), reports who handles them
now, and opens Windows' Default apps page. Registry work happens only in a
scratch key under HKCU\\Software\\OpenGrabTest."""
import sys
import uuid
from pathlib import Path

import pytest

from opengrab import winintegration as wi
from tests.helpers.apiserver import req, pair_device, running_server

STATIC = Path(__file__).resolve().parents[2] / "src" / "opengrab" / "ui" / "static"
EXE = "C:\\OpenGrab\\OpenGrab.exe"
_RealRegistry = wi.Registry
win_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")


@pytest.fixture
def scratch():
    import winreg
    base = rf"Software\OpenGrabTest\{uuid.uuid4().hex}"
    reg = wi.Registry(base=base, command=f'"{EXE}"')
    try:
        yield reg, base
    finally:
        wi.Registry(base="")._delete_tree(base)
    with pytest.raises(OSError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)


def _user_choice(reg, key, sub, progid):
    reg._set(sub.format(key), "ProgId", progid)
    reg._set(sub.format(key), "Hash", "not-a-real-hash")


@win_only
def test_capabilities_are_written_and_removed(scratch):
    reg, _ = scratch
    assert not reg.capabilities_registered()
    reg.set_protocol("magnet", True)
    reg.set_file_type(".torrent", True)
    assert reg.capabilities_registered()
    assert reg._get(wi.CAP_KEY, "ApplicationName") == "OpenGrab"
    assert reg._get(wi.CAP_KEY, "ApplicationDescription")
    assert reg._get(wi.CAP_KEY, "ApplicationIcon") == f"{EXE},0"
    assert reg._get(wi.CAP_KEY + r"\UrlAssociations", "magnet") == "OpenGrab.magnet"
    assert reg._get(wi.CAP_KEY + r"\FileAssociations", ".torrent") == "OpenGrab.torrent"
    assert reg._get(wi.REGISTERED_APPS, "OpenGrab") == wi.CAP_KEY
    cls = wi.CLASSES
    assert reg._get(rf"{cls}\OpenGrab.magnet\shell\open\command") == f'"{EXE}" "%1"'
    assert reg._get(rf"{cls}\OpenGrab.magnet", "URL Protocol") == ""
    assert reg._get(rf"{cls}\OpenGrab.magnet\DefaultIcon") == f"{EXE},0"
    assert reg._get(rf"{cls}\OpenGrab.torrent\shell\open\command") == f'"{EXE}" "%1"'
    assert reg._get(rf"{cls}\OpenGrab.torrent\DefaultIcon") == f"{EXE},0"
    assert reg.protocol_is_ours("magnet")  # the classic Classes\magnet registration still works

    # user's protected choice survives everything
    _user_choice(reg, "magnet", wi.URL_USERCHOICE, "BitTorrent.magnet")
    reg.set_protocol("magnet", False)
    assert reg.capabilities_registered()  # .torrent is still ours
    reg.set_file_type(".torrent", False)
    assert not reg.capabilities_registered()
    assert reg._get(wi.CAP_KEY, "ApplicationName") is None
    assert reg._get(wi.REGISTERED_APPS, "OpenGrab") is None
    assert reg._get(rf"{cls}\OpenGrab.magnet") is None and reg._get(rf"{cls}\OpenGrab.torrent") is None
    assert reg.user_choice("magnet") == "BitTorrent.magnet"
    assert reg._get(wi.URL_USERCHOICE.format("magnet"), "Hash") == "not-a-real-hash"


@win_only
def test_current_handler_reads_user_choice(scratch):
    reg, _ = scratch
    assert wi.current_handler("magnet", reg) == {"progid": "", "name": "", "is_ours": False}
    # ProgId -> Application\ApplicationName
    reg._set(rf"{wi.CLASSES}\BitTorrent.magnet\Application", "ApplicationName", "BitTorrent")
    _user_choice(reg, "magnet", wi.URL_USERCHOICE, "BitTorrent.magnet")
    assert wi.current_handler("magnet", reg) == {"progid": "BitTorrent.magnet", "name": "BitTorrent", "is_ours": False}
    # ProgId -> exe name from its command (file does not exist, so the file name is used)
    reg._set(rf"{wi.CLASSES}\Other.torrent\shell\open\command", "", '"C:\\Nowhere\\ut.exe" "%1"')
    _user_choice(reg, ".torrent", wi.EXT_USERCHOICE, "Other.torrent")
    assert wi.current_handler(".torrent", reg)["name"] == "ut"
    # unknown ProgId -> the ProgId itself
    _user_choice(reg, ".torrent", wi.EXT_USERCHOICE, "Mystery.app")
    assert wi.current_handler(".torrent", reg) == {"progid": "Mystery.app", "name": "Mystery.app", "is_ours": False}
    # our own ProgId
    _user_choice(reg, "magnet", wi.URL_USERCHOICE, "OpenGrab.magnet")
    h = wi.current_handler("magnet", reg)
    assert h["is_ours"] and h["progid"] == "OpenGrab.magnet"


@win_only
def test_current_handler_without_a_choice_uses_class_default(scratch):
    reg, _ = scratch
    reg.set_protocol("magnet", True)
    assert wi.current_handler("magnet", reg) == {"progid": "", "name": "OpenGrab", "is_ours": True}


def test_open_default_apps_prefers_the_windows_11_page(monkeypatch):
    opened = []
    monkeypatch.setattr(wi.sys, "getwindowsversion", lambda: type("V", (), {"build": 26100})(), raising=False)
    monkeypatch.setattr("os.startfile", lambda u: opened.append(u), raising=False)
    assert wi.open_default_apps()
    assert opened == ["ms-settings:defaultapps?registeredAppUser=OpenGrab"]
    opened.clear()
    monkeypatch.setattr(wi.sys, "getwindowsversion", lambda: type("V", (), {"build": 19045})(), raising=False)
    assert wi.open_default_apps()
    assert opened == ["ms-settings:defaultapps"]
    # falls back when the first URI is refused
    opened.clear()
    monkeypatch.setattr(wi.sys, "getwindowsversion", lambda: type("V", (), {"build": 26100})(), raising=False)

    def picky(u):
        if "registeredAppUser" in u:
            raise OSError("no handler")
        opened.append(u)
    monkeypatch.setattr("os.startfile", picky, raising=False)
    assert wi.open_default_apps() and opened == ["ms-settings:defaultapps"]


def test_integration_never_writes_user_choice():
    src = Path(wi.__file__).read_text(encoding="utf-8")
    assert "UserChoice" in src
    # the only operations on UserChoice paths are reads through user_choice()
    assert "_set(URL_USERCHOICE" not in src and "_set(EXT_USERCHOICE" not in src
    assert "_delete_tree(URL_USERCHOICE" not in src and "_delete_tree(EXT_USERCHOICE" not in src


@win_only
def test_default_apps_route_registers_and_opens_the_page(monkeypatch):
    base = rf"Software\OpenGrabTest\{uuid.uuid4().hex}"
    monkeypatch.setattr(wi, "Registry", lambda *a, **k: _RealRegistry(base=base, command=f'"{EXE}"'))
    opened = []
    monkeypatch.setattr(wi, "open_default_apps", lambda: opened.append(1) or True)
    try:
        with running_server() as srv:
            dev = pair_device(srv)
            assert req(srv, "POST", "/api/system/default-apps", body={}, auth=dev)[0] == 403
            assert not opened
            st, res = req(srv, "GET", "/api/system/integration")
            assert st == 200 and res["handlers"].keys() == {"magnet", ".torrent"} and res["capabilities"] is False
            st, res = req(srv, "POST", "/api/system/default-apps", body={})
            assert st == 200, res
            assert opened == [1] and res["opened"] is True and res["capabilities"] is True
            # turning magnet and .torrent off removes the capabilities again
            st, res = req(srv, "POST", "/api/system/integration",
                          body={"protocols": {"magnet": True}, "files": {".torrent": True}})
            assert st == 200 and res["handlers"]["magnet"]["is_ours"]
            st, res = req(srv, "POST", "/api/system/integration",
                          body={"protocols": {"magnet": False}, "files": {".torrent": False}})
            assert st == 200 and res["capabilities"] is False
    finally:
        _RealRegistry(base="")._delete_tree(base)


def test_markup_and_script_wire_the_default_apps_button():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="btn-win-default"' in html and "Make OpenGrab the default…" in html
    assert "Magnet links open in:" in html and ".torrent files open in:" in html
    assert 'id="win-h-magnet"' in html and 'id="win-h-torrent"' in html
    assert "pick OpenGrab for magnet links and .torrent in the page that opens" in html
    assert "/api/system/default-apps" in js
    assert 'window.addEventListener("focus"' in js and "loadIntegration()" in js
    assert (STATIC / "i18n" / "zh-CN.json").read_bytes().endswith(b"\n")
