import pytest

from opengrab.avscan import (CLEAN, DEFENDER_PATHS, ERROR, INFECTED, SKIPPED, ScanConfig,
                             Scanner, build_command, config_from_dict, find_defender,
                             interpret)

FILE = r"C:\dl\setup.exe"


def exists_all(path):
    return True


def exists_none(path):
    return False


class FakeRunner:
    def __init__(self, code=0, out="", exc=None):
        self.code, self.out, self.exc = code, out, exc
        self.calls = []

    def __call__(self, argv, timeout_s):
        self.calls.append((argv, timeout_s))
        if self.exc is not None:
            raise self.exc
        return self.code, self.out


def test_config_defaults_off_and_roundtrip():
    cfg = config_from_dict(None)
    assert cfg.enabled is False
    assert cfg.scanner == "defender"
    again = config_from_dict(ScanConfig(enabled=True, scanner="custom",
                                        custom_command="av.exe {path}",
                                        timeout_s=60).to_dict())
    assert again.enabled is True
    assert again.custom_command == "av.exe {path}"
    assert again.timeout_s == 60.0


def test_config_validation():
    with pytest.raises(ValueError):
        ScanConfig(scanner="nope")
    with pytest.raises(ValueError):
        ScanConfig(timeout_s=0)


def test_find_defender():
    assert find_defender(exists_all) == DEFENDER_PATHS[0]
    assert find_defender(exists_none) is None
    assert find_defender(lambda p: p == DEFENDER_PATHS[1]) == DEFENDER_PATHS[1]


def test_build_command_defender_and_custom():
    argv = build_command(ScanConfig(enabled=True), FILE, exists_all)
    assert argv == [DEFENDER_PATHS[0], "-Scan", "-ScanType", "3", "-File", FILE,
                    "-DisableRemediation"]
    assert build_command(ScanConfig(enabled=True), FILE, exists_none) is None
    custom = ScanConfig(enabled=True, scanner="custom",
                        custom_command='"C:\\AV Tools\\scan.exe" /q "{path}"')
    assert build_command(custom, FILE, exists_all) == ["C:\\AV Tools\\scan.exe", "/q", FILE]
    appended = ScanConfig(enabled=True, scanner="custom", custom_command="scan.exe /q")
    assert build_command(appended, FILE, exists_all) == ["scan.exe", "/q", FILE]
    empty = ScanConfig(enabled=True, scanner="custom", custom_command="  ")
    assert build_command(empty, FILE, exists_all) is None


def test_interpret_codes():
    cfg = ScanConfig(enabled=True)
    assert interpret(cfg, FILE, 0, "ok").status == CLEAN
    infected = interpret(cfg, FILE, 2, "Threat found")
    assert infected.status == INFECTED
    assert infected.flagged is True
    assert interpret(cfg, FILE, 5, "boom").status == ERROR


def test_scanner_disabled_skips_without_running():
    runner = FakeRunner()
    res = Scanner(ScanConfig(), runner, exists_all).scan(FILE)
    assert res.status == SKIPPED
    assert runner.calls == []


def test_scanner_runs_and_records_history():
    runner = FakeRunner(code=2, out="Threat")
    sc = Scanner(ScanConfig(enabled=True, timeout_s=30), runner, exists_all)
    res = sc.scan(FILE)
    assert res.status == INFECTED
    assert runner.calls[0][1] == 30
    assert sc.flagged() == [res]
    assert sc.scan(FILE).status == INFECTED
    assert len(sc.history) == 2


def test_scanner_missing_file_and_no_scanner():
    runner = FakeRunner()
    assert Scanner(ScanConfig(enabled=True), runner, exists_none).scan(FILE).status == ERROR
    only_file = Scanner(ScanConfig(enabled=True), runner, lambda p: p == FILE)
    assert only_file.scan(FILE).status == SKIPPED
    assert runner.calls == []


def test_scanner_runner_exception_never_raises():
    runner = FakeRunner(exc=TimeoutError("took too long"))
    res = Scanner(ScanConfig(enabled=True), runner, exists_all).scan(FILE)
    assert res.status == ERROR
    assert "TimeoutError" in res.detail