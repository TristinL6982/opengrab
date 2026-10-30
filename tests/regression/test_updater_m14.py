"""M14: in-app updates from signed GitHub releases, with rollback."""
import base64
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from Cryptodome.PublicKey import ECC
from Cryptodome.Signature import eddsa

from opengrab import updater
from tests.helpers.apiserver import pair_device, req, running_server


@pytest.fixture(scope="module")
def key():
    k = ECC.generate(curve="ed25519")
    return k, k.public_key().export_key(format="raw").hex()


def _sign(k, data: bytes) -> str:
    return base64.b64encode(eddsa.new(k, "rfc8032").sign(data)).decode()


def _build_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_versions_compare_like_releases():
    assert updater.is_newer("0.2.0", "0.1.9")
    assert updater.is_newer("v1.0.0", "0.9.9")
    assert not updater.is_newer("0.1.0", "0.1.0")
    assert updater.is_newer("0.2.0", "0.2.0rc1")  # the final release beats its candidate
    assert not updater.is_newer("0.2.0b1", "0.2.0")
    assert not updater.is_newer("nonsense", "0.1.0")


def test_release_parsing_needs_the_zip_and_its_signature():
    data = {"tag_name": "v0.3.0", "body": "notes", "html_url": "https://github.com/x",
            "assets": [{"name": "OpenGrab-0.3.0-win64.zip", "browser_download_url": "https://dl/zip"},
                       {"name": "OpenGrab-0.3.0-win64.zip.sig", "browser_download_url": "https://dl/sig"}]}
    rel = updater.parse_release(data)
    assert (rel.version, rel.zip_url, rel.sig_url) == ("0.3.0", "https://dl/zip", "https://dl/sig")
    assert updater.parse_release({**data, "assets": data["assets"][:1]}) is None  # unsigned
    assert updater.parse_release({**data, "prerelease": True}) is None
    assert updater.parse_release({**data, "draft": True}) is None
    res = updater.check(fetch=lambda url: json.dumps(data).encode())
    assert res["available"] and res["version"] == "0.3.0" and res["installable"] is False  # source run


def test_downloads_are_https_only():
    with pytest.raises(updater.UpdateError):
        updater._get("http://example.com/x.zip")


def test_stage_verifies_unpacks_and_self_tests(tmp_path, key):
    k, pub = key
    z = _build_zip({"OpenGrab/OpenGrab.exe": b"new exe", "OpenGrab/_internal/lib.dll": b"lib"})
    sig = _sign(k, z)
    tested = []
    new = updater.stage(z, sig, tmp_path / "updates", keys=[pub], smoke=lambda p: tested.append(p) or True)
    assert (new / "OpenGrab.exe").read_bytes() == b"new exe" and tested == [new]

    # a tampered zip, a wrong key or no key at all installs nothing
    with pytest.raises(updater.UpdateError, match="signature"):
        updater.stage(z + b"x", sig, tmp_path / "u2", keys=[pub], smoke=lambda p: True)
    other = ECC.generate(curve="ed25519").public_key().export_key(format="raw").hex()
    with pytest.raises(updater.UpdateError, match="signature"):
        updater.stage(z, sig, tmp_path / "u3", keys=[other], smoke=lambda p: True)
    with pytest.raises(updater.UpdateError, match="signature"):
        updater.stage(z, sig, tmp_path / "u4", keys=[], smoke=lambda p: True)

    # a build that fails its self-test is thrown away
    with pytest.raises(updater.UpdateError, match="self-test"):
        updater.stage(z, sig, tmp_path / "u5", keys=[pub], smoke=lambda p: False)
    assert not (tmp_path / "u5" / "OpenGrab.new").exists()


def test_stage_refuses_paths_that_leave_the_folder(tmp_path, key):
    k, pub = key
    z = _build_zip({"OpenGrab/OpenGrab.exe": b"x", "../../evil.txt": b"x"})
    with pytest.raises(updater.UpdateError, match="unsafe"):
        updater.stage(z, _sign(k, z), tmp_path / "u", keys=[pub], smoke=lambda p: True)
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path.parent / "evil.txt").exists()
    z2 = _build_zip({"Other/OpenGrab.exe": b"x"})
    with pytest.raises(updater.UpdateError, match="no OpenGrab folder"):
        updater.stage(z2, _sign(k, z2), tmp_path / "v", keys=[pub], smoke=lambda p: True)


def test_python_swap_restores_the_old_folder_if_the_new_one_cannot_move(tmp_path):
    root, new, old = tmp_path / "OpenGrab", tmp_path / "OpenGrab.new", tmp_path / "OpenGrab.old"
    root.mkdir(); (root / "v").write_text("1")
    new.mkdir(); (new / "v").write_text("2")
    updater.swap(root, new, old)
    assert (root / "v").read_text() == "2" and (old / "v").read_text() == "1"
    # rollback is the same swap the other way
    updater.swap(root, old, new)
    assert (root / "v").read_text() == "1" and (new / "v").read_text() == "2"
    with pytest.raises(OSError):
        updater.swap(root, tmp_path / "missing", old)
    assert (root / "v").read_text() == "1"  # put back


@pytest.mark.skipif(sys.platform != "win32", reason="cmd script")
def test_swap_script_waits_for_exit_then_swaps_and_rolls_back(tmp_path):
    install = tmp_path / "Program Files" / "OpenGrab" / "app"
    install.mkdir(parents=True)
    (install / "OpenGrab.exe").write_text("v1")
    staged = tmp_path / "data" / "updates" / "OpenGrab.new"
    staged.mkdir(parents=True)
    (staged / "OpenGrab.exe").write_text("v2")
    (staged / "_internal").mkdir()
    (staged / "_internal" / "lib.dll").write_text("lib2")
    waiter = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    script = tmp_path / "swap.cmd"
    keep = install.with_name("app.old")
    script.write_text(updater.swap_script(install, staged, keep, waiter.pid, restart=False), encoding="mbcs")
    res = subprocess.run(["cmd", "/c", str(script)], capture_output=True, timeout=60)
    assert waiter.poll() is not None  # it waited for the process to end
    assert res.returncode == 0, res
    assert (install / "OpenGrab.exe").read_text() == "v2" and (install / "_internal" / "lib.dll").read_text() == "lib2"
    assert (keep / "OpenGrab.exe").read_text() == "v1" and not staged.exists()
    assert updater.rollback_available(install)

    # rollback: the previous version comes back, the newer one is kept aside
    script.write_text(updater.swap_script(install, keep, install.with_name("app.new"), 999999, restart=False),
                      encoding="mbcs")
    assert subprocess.run(["cmd", "/c", str(script)], capture_output=True, timeout=60).returncode == 0
    assert (install / "OpenGrab.exe").read_text() == "v1"
    assert (install.with_name("app.new") / "OpenGrab.exe").read_text() == "v2"

    # a failed copy leaves the running version untouched
    script.write_text(updater.swap_script(install, tmp_path / "nothing", keep, 999999, restart=False),
                      encoding="mbcs")
    subprocess.run(["cmd", "/c", str(script)], capture_output=True, timeout=60)
    assert (install / "OpenGrab.exe").read_text() == "v1"


def test_update_routes_are_master_and_local_only():
    with running_server() as srv:
        st, s = req(srv, "GET", "/api/update")
        assert st == 200 and s["current"] == updater.__version__ and s["frozen"] is False
        assert "version" not in s  # nothing is fetched unless asked
        st, res = req(srv, "POST", "/api/update/install", body={})
        assert st == 400 and "source" in res["error"]
        st, res = req(srv, "POST", "/api/update/rollback", body={})
        assert st == 400
        dev = pair_device(srv)
        assert req(srv, "POST", "/api/update/install", body={}, auth=dev)[0] == 403
        assert req(srv, "POST", "/api/update/rollback", body={}, auth=dev)[0] == 403


def test_release_tools_sign_what_the_updater_accepts(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    pem = tmp_path / "key.pem"
    out = subprocess.run([sys.executable, str(root / "tools" / "release_key.py"), str(pem)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    pub = out.stdout.strip().splitlines()[-1]
    assert len(pub) == 64
    asset = tmp_path / "OpenGrab-9.9.9-win64.zip"
    asset.write_bytes(_build_zip({"OpenGrab/OpenGrab.exe": b"x"}))
    env = {**__import__("os").environ, "RELEASE_SIGNING_KEY": pem.read_text()}
    res = subprocess.run([sys.executable, str(root / "tools" / "sign_release.py"), str(asset)],
                         capture_output=True, text=True, env=env, timeout=60)
    assert res.returncode == 0, res.stderr
    sig = Path(str(asset) + ".sig").read_text()
    assert updater.verify(asset.read_bytes(), sig, [pub])
    assert not updater.verify(asset.read_bytes() + b"!", sig, [pub])
    # without the secret the tool says so instead of producing a fake signature
    env.pop("RELEASE_SIGNING_KEY")
    assert subprocess.run([sys.executable, str(root / "tools" / "sign_release.py"), str(asset)],
                          capture_output=True, env=env, timeout=60).returncode == 2
