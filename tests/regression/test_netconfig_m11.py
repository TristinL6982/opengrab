import json

from opengrab.netconfig import SECTIONS, load_network_config, load_network_config_file


def test_defaults_when_empty():
    cfg = load_network_config({})
    assert cfg.errors == []
    assert cfg.reconnect.enabled is False
    assert cfg.retry.max_attempts == 5
    assert cfg.avscan.enabled is False


def test_non_dict_input_gives_defaults():
    cfg = load_network_config(None)
    assert cfg.errors == []
    assert cfg.retry.max_attempts == 5


def test_sections_are_loaded():
    cfg = load_network_config({
        "reconnect": {"enabled": True, "max_attempts": 4},
        "retry": {"max_attempts": 9},
        "torrent": {"sequential": True},
        "avscan": {"enabled": True},
        "categories": [{"name": "Video", "extensions": [".mp4"], "folder": "Videos"}],
    })
    assert cfg.errors == []
    assert cfg.reconnect.enabled is True and cfg.reconnect.max_attempts == 4
    assert cfg.retry.max_attempts == 9
    assert cfg.torrent.sequential is True
    assert cfg.avscan.enabled is True
    assert cfg.categories.to_list()[0]["name"] == "Video"


def test_bad_section_falls_back_and_is_reported():
    cfg = load_network_config({"retry": {"max_attempts": 0}, "reconnect": {"enabled": True}})
    assert cfg.errors == ["retry"]
    assert cfg.retry.max_attempts == 5
    assert cfg.reconnect.enabled is True


def test_sections_list():
    assert set(SECTIONS) == {"reconnect", "retry", "torrent", "portmap", "avscan", "categories"}


def test_file_missing_gives_defaults(tmp_path):
    cfg = load_network_config_file(tmp_path / "nope.json")
    assert cfg.errors == []


def test_file_bad_json_reported(tmp_path):
    p = tmp_path / "net.json"
    p.write_text("{not json", encoding="utf-8")
    cfg = load_network_config_file(p)
    assert cfg.errors == ["file"]


def test_file_loaded(tmp_path):
    p = tmp_path / "net.json"
    p.write_text(json.dumps({"retry": {"max_attempts": 7}}), encoding="utf-8")
    assert load_network_config_file(p).retry.max_attempts == 7