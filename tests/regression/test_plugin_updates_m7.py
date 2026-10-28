import hashlib
import json
from pathlib import Path

import pytest

import opengrab.plugins
from opengrab.plugins.updates import apply_update, check_and_update, plan_updates, version_tuple


def _template():
    path = Path(opengrab.plugins.__file__).parent / "templates" / "example_host.json"
    tpl = json.loads(path.read_text(encoding="utf-8"))
    tpl["id"] = "host-a"
    data = json.dumps(tpl).encode("utf-8")
    return data, hashlib.sha256(data).hexdigest()


def test_version_tuple():
    assert version_tuple("1.2.10") == (1, 2, 10)
    assert version_tuple("2.x") == (2, 0)
    assert (version_tuple("1.10") > version_tuple("1.9")) is True


def test_plan_updates():
    installed = {"host-a": "1.0", "host-b": "2.0"}
    index = {"templates": [
        {"id": "host-c", "version": "0.1", "url": "c.json", "sha256": "0" * 64},
        {"id": "host-a", "version": "1.1", "url": "a.json", "sha256": "0" * 64},
        {"id": "host-b", "version": "2.0", "url": "b.json", "sha256": "0" * 64},
        {"id": "host-d", "version": "1.0", "url": "d.json"},
    ]}
    assert [e["id"] for e in plan_updates(installed, index)] == ["host-a", "host-c"]


def test_apply_update_writes_file(tmp_path):
    data, sha = _template()
    path = apply_update({"id": "host-a", "sha256": sha}, data, tmp_path)
    assert path == tmp_path / "host-a.json"
    assert json.loads(path.read_text(encoding="utf-8"))["id"] == "host-a"


def test_apply_update_sha_mismatch(tmp_path):
    data, _ = _template()
    with pytest.raises(ValueError, match="sha256 mismatch"):
        apply_update({"id": "host-a", "sha256": "0" * 64}, data, tmp_path)


def test_apply_update_bad_id(tmp_path):
    data, sha = _template()
    with pytest.raises(ValueError, match="bad template id"):
        apply_update({"id": "../evil", "sha256": sha}, data, tmp_path)


def test_apply_update_id_mismatch(tmp_path):
    data, sha = _template()
    with pytest.raises(ValueError, match="id mismatch"):
        apply_update({"id": "host-z", "sha256": sha}, data, tmp_path)


def test_apply_update_invalid_template(tmp_path):
    data = b'{"id": "host-a"}'
    sha = hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError, match="invalid template"):
        apply_update({"id": "host-a", "sha256": sha}, data, tmp_path)


def test_check_and_update(tmp_path):
    data, sha = _template()
    bad = b'{"id": "host-c"}'
    index = {"templates": [
        {"id": "host-a", "version": "1.1", "url": "a.json", "sha256": sha},
        {"id": "host-c", "version": "1.0", "url": "c.json", "sha256": hashlib.sha256(bad).hexdigest()},
    ]}
    store = {
        "https://idx/index.json": json.dumps(index).encode("utf-8"),
        "https://idx/a.json": data,
        "https://idx/c.json": bad,
    }
    result = check_and_update({"host-a": "1.0"}, "https://idx/index.json", lambda url: store[url], tmp_path)
    assert result["updated"] == ["host-a"]
    assert list(result["failed"]) == ["host-c"]
    assert (tmp_path / "host-a.json").exists() is True
    assert (tmp_path / "host-c.json").exists() is False