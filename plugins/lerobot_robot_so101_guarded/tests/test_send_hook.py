"""so101-guard-validate offers to send its verdicts at the end, without changing its result.

Runs without LeRobot: the two modules that need it are replaced by stand-ins.
"""
import sys
import types
from pathlib import Path

import pytest

PKG = "lerobot_robot_so101_guarded"


@pytest.fixture
def validate(monkeypatch, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    cfg = types.ModuleType(f"{PKG}.config_so101_follower_guarded")
    rob = types.ModuleType(f"{PKG}.so101_follower_guarded")

    class Config:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Robot:
        def __init__(self, config):
            self.calibration_fpath = tmp_path / "calib.json"
            self.calibration_fpath.write_text("{}")

        def connect(self):
            pass

        def disconnect(self):
            pass

    cfg.SO101FollowerGuardedConfig, rob.SO101FollowerGuarded = Config, Robot
    for name in (PKG, f"{PKG}.validate"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, cfg.__name__, cfg)
    monkeypatch.setitem(sys.modules, rob.__name__, rob)
    import importlib
    mod = importlib.import_module(f"{PKG}.validate")
    yield mod
    sys.path.remove(str(Path(__file__).resolve().parents[1]))


def test_offers_after_the_verdicts_and_keeps_the_exit_code(validate, monkeypatch, tmp_path):
    from bringup_check import send
    from bringup_check.loganalyze import Verdict
    monkeypatch.setattr(validate.Runner, "all", lambda self, tests: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    monkeypatch.setattr(validate, "verdicts", lambda log, notes: [Verdict("sweep", True, ["stayed out"])])
    seen = []
    monkeypatch.setattr(send, "offer", lambda kind, data, **kw: seen.append((kind, kw)) or False)
    rc = validate.main(["--port", "COM3", "--id", "arm", "--log", str(tmp_path / "v.csv")])
    assert rc == 0 and seen == [("guard_validate", {"enabled": True})]
    seen.clear()
    rc = validate.main(["--port", "COM3", "--id", "arm", "--log", str(tmp_path / "v.csv"), "--no-send"])
    assert rc == 0 and seen == [("guard_validate", {"enabled": False})]
