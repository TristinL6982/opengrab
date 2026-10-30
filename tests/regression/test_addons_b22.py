"""B22 / spec section 2: local OCR is an opt-in add-on, never bundled."""
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from opengrab import addons, hardware
from opengrab.captcha.base import DdddOCRSolver
from opengrab.settings import Settings


def _wheel(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def fake_addon(monkeypatch, tmp_path):
    wheel = _wheel({"fakeocr_b22/__init__.py": "VALUE = 42\n",
                    "fakeocr_b22/common.onnx": "unused model",
                    "fakeocr_b22/common_old.onnx": "used model"})
    lock = {"python": "cp312", "platform": "win_amd64", "addons": {"ocr": {
        "root": "fakeocr_b22", "download_bytes": len(wheel),
        "wheels": [{"name": "fakeocr-b22", "version": "1", "filename": "fakeocr_b22-1-py3-none-any.whl",
                    "url": "https://files.example/fakeocr_b22.whl",
                    "sha256": hashlib.sha256(wheel).hexdigest(), "size": len(wheel)}]}}}
    monkeypatch.setattr(addons, "load_lock", lambda: lock)
    spec = dict(addons.ADDONS["ocr"], module="fakeocr_b22", prune=["fakeocr_b22/common.onnx"])
    monkeypatch.setitem(addons.ADDONS, "ocr", spec)
    data = tmp_path / "data"
    yield data, wheel, lock
    site = str(addons.site_dir("ocr", data))
    if site in sys.path:
        sys.path.remove(site)
    sys.modules.pop("fakeocr_b22", None)


def test_install_verifies_hash_prunes_and_activates(fake_addon):
    data, wheel, _ = fake_addon
    assert not addons.is_installed("ocr", data)
    seen = []
    st = addons.install("ocr", data, fetch=lambda url: (seen.append(url), wheel)[1])
    assert seen == ["https://files.example/fakeocr_b22.whl"]
    assert st["installed"] and st["method"] == "wheels"
    site = addons.site_dir("ocr", data)
    assert (site / "fakeocr_b22" / "common_old.onnx").is_file()
    assert not (site / "fakeocr_b22" / "common.onnx").exists()  # pruned
    assert str(site) in sys.path
    import fakeocr_b22
    assert fakeocr_b22.VALUE == 42
    assert addons.remove("ocr", data) is True
    assert not addons.is_installed("ocr", data) and str(site) not in sys.path


def test_hash_mismatch_is_refused_and_leaves_nothing(fake_addon):
    data, wheel, _ = fake_addon
    with pytest.raises(addons.AddonError, match="hash mismatch"):
        addons.install("ocr", data, fetch=lambda url: wheel + b"tampered")
    assert not addons.is_installed("ocr", data)
    assert not addons.site_dir("ocr", data).exists()


def test_path_traversal_in_wheel_is_refused(fake_addon, monkeypatch):
    data, _, lock = fake_addon
    bad = _wheel({"../../evil.py": "x"})
    lock["addons"]["ocr"]["wheels"][0].update(sha256=hashlib.sha256(bad).hexdigest(), size=len(bad))
    with pytest.raises(addons.AddonError, match="unsafe path"):
        addons.install("ocr", data, fetch=lambda url: bad)
    assert not (data.parent / "evil.py").exists()


def test_real_lock_pins_hashes_and_has_no_pillow():
    lock = json.loads(addons.LOCK_FILE.read_text(encoding="utf-8"))
    wheels = lock["addons"]["ocr"]["wheels"]
    names = {w["name"] for w in wheels}
    assert "ddddocr" in names and "onnxruntime" in names
    assert "pillow" not in names  # already part of the base app
    assert all(len(w["sha256"]) == 64 and w["url"].startswith("https://files.pythonhosted.org/") for w in wheels)


def test_solver_is_off_by_default():
    s = DdddOCRSolver(Settings())
    assert s.enabled() is False
    assert "turned off" in s.unavailable_reason()


def test_solver_needs_the_addon(monkeypatch):
    monkeypatch.setattr(addons, "importable", lambda m: False)
    monkeypatch.setattr(hardware, "profile", lambda: {"weak": False, "reasons": []})
    s = DdddOCRSolver(Settings(captcha_ocr_enabled=True))
    assert s.enabled() is False and "not installed" in s.unavailable_reason()
    monkeypatch.setattr(addons, "importable", lambda m: True)
    assert s.enabled() is True


def test_weak_hardware_skips_unless_overridden(monkeypatch):
    monkeypatch.setattr(addons, "importable", lambda m: True)
    monkeypatch.setattr(hardware, "profile", lambda: {"weak": True, "reasons": ["2.0 GB RAM"]})
    s = DdddOCRSolver(Settings(captcha_ocr_enabled=True))
    assert s.enabled() is False and "weak hardware" in s.unavailable_reason()
    s2 = DdddOCRSolver(Settings(captcha_ocr_enabled=True, captcha_ocr_ignore_hardware=True))
    assert s2.enabled() is True


def test_hardware_profile_flags_low_ram(monkeypatch):
    monkeypatch.setattr(hardware, "total_ram_bytes", lambda: 2 * 1024 ** 3)
    p = hardware.profile()
    assert p["weak"] and "2.0 GB RAM" in p["reasons"]


def test_build_and_install_carry_no_ml_packages():
    root = Path(__file__).resolve().parents[2]
    spec = (root / "build" / "OpenGrab.spec").read_text(encoding="utf-8")
    assert '"ddddocr"' not in spec.split("excludes=")[0]
    assert "onnxruntime" in spec.split("excludes=")[1]
    assert "ddddocr" not in (root / "requirements.txt").read_text(encoding="utf-8")
    setup_py = (root / "setup.py").read_text(encoding="utf-8")
    assert "install_requires" in setup_py and "ddddocr" not in setup_py.split("extras_require")[0]


def test_addon_routes_and_settings_scope():
    from tests.helpers.apiserver import pair_device, req, running_server
    with running_server() as srv:
        st, body = req(srv, "GET", "/api/addons")
        assert st == 200 and body["addons"][0]["name"] == "ocr"
        assert "weak" in body["hardware"]
        device = pair_device(srv)
        st, _ = req(srv, "GET", "/api/addons", auth=device)
        assert st == 200
        st, err = req(srv, "POST", "/api/addons/ocr/install", body={}, auth=device)
        assert st == 403, err
        # settings writes are master-only (scope "settings_write")
        st, err = req(srv, "POST", "/api/settings", body={"max_parallel": 2}, auth=device)
        assert st == 403, err
        st, ok = req(srv, "POST", "/api/settings", body={"captcha_ocr_enabled": True})
        assert st == 200 and "captcha_ocr_enabled" in ok["applied"]
        st, _ = req(srv, "POST", "/api/addons/nope/install", body={})
        assert st == 404
