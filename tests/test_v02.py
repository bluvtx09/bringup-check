import json
import re
import string

import pytest

from bringup_check.calib import candidates, pick
from bringup_check.checks import SO101_NAMES, take_snapshot
from bringup_check.cli import diagnose, main
from bringup_check.messages import MSG, set_lang
from bringup_check.report import share_summary
from bringup_check.scenarios import build, make_bus

SCENARIOS = build()
IDS = [1, 2, 3, 4, 5, 6]
HANGUL = re.compile("[가-힣]")


@pytest.fixture(autouse=True)
def reset_lang():
    yield
    set_lang("ko")


def _fields(text):
    return {f[1] for f in string.Formatter().parse(text) if f[1]}


def test_catalog_has_same_keys_and_placeholders_in_both_languages():
    assert MSG["ko"].keys() == MSG["en"].keys()
    for k in MSG["ko"]:
        assert _fields(MSG["ko"][k]) == _fields(MSG["en"][k]), k


def test_english_report_has_no_korean(capsys):
    assert main(["--demo", "all", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert not HANGUL.search(out)
    assert "No answer from motor 4 (wrist_flex) onward" in out
    assert "between motor 3 (elbow_flex) and motor 4 (wrist_flex)" in out


@pytest.mark.parametrize("lang", ["ko", "en"])
def test_scenarios_detected_in_both_languages(lang):
    set_lang(lang)
    for name, sc in SCENARIOS.items():
        _, findings, _, _ = diagnose(make_bus(sc), IDS, SO101_NAMES, sc["calib"], sc["servo_rating"],
                                     2.0 if sc["hold_test"] else 0.0, sleep=lambda _: None)
        codes = {f.code for f in findings}
        assert set(sc["expect"]) <= codes, (lang, name, codes)


def _write(tmp_path, rel, data):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    return p


def test_auto_calibration_picks_the_file_matching_the_arm(tmp_path):
    sc = SCENARIOS["healthy"]
    good = sc["calib"]
    # a leader-arm file whose ranges don't contain this arm's positions
    wrong = {n: {**c, "range_min": 3500, "range_max": 4000} for n, c in good.items()}
    _write(tmp_path, "robots/so101_follower/my_arm.json", good)
    _write(tmp_path, "teleoperators/so101_leader/my_leader.json", wrong)
    _write(tmp_path, "other/not_calibration.json", {"hello": 1})
    bus = make_bus(sc)
    snap = take_snapshot(bus, IDS)
    cands = candidates([tmp_path])
    assert len(cands) == 2  # the non-calibration JSON is ignored
    path, calib, n = pick(cands, {i: snap.get(i, "Present_Position") for i in snap.responding})
    assert path.name == "my_arm.json" and n == 2


def test_auto_calibration_finds_problems_through_cli_path(tmp_path):
    sc = SCENARIOS["calibration"]
    _write(tmp_path, "robots/so101_follower/arm.json", sc["calib"])
    logs = []
    _, findings, _, _ = diagnose(make_bus(sc), IDS, SO101_NAMES, "auto", calib_roots=[tmp_path], log=logs.append)
    assert {"calib.wrap", "calib.outside"} <= {f.code for f in findings}
    assert any("arm.json" in line for line in logs)


def test_no_calibration_file_is_reported_not_fatal(tmp_path):
    logs = []
    _, findings, _, _ = diagnose(make_bus(SCENARIOS["healthy"]), IDS, SO101_NAMES, "auto",
                                 calib_roots=[tmp_path], log=logs.append)
    assert logs and not [f for f in findings if f.code.startswith("calib.")]


def test_share_summary_is_compact_and_has_no_paths():
    sc = SCENARIOS["chain_break"]
    snap, findings, _, _ = diagnose(make_bus(sc), IDS, SO101_NAMES)
    text = share_summary(snap, findings, {"version": "0.2.0"})
    assert text.startswith("```") and "4: no answer" in text and "bus.chain_break[4, 5, 6]" in text
    assert "/dev/" not in text and "COM" not in text


def test_missing_port_gives_a_plain_message(monkeypatch):
    """The real scservo_sdk raises serial.SerialException when the port doesn't exist."""
    import sys
    import types

    class Port:
        def __init__(self, name): pass
        def openPort(self): raise OSError("could not open port")

    monkeypatch.setitem(sys.modules, "scservo_sdk",
                        types.SimpleNamespace(PortHandler=Port, PacketHandler=lambda e: None, COMM_SUCCESS=0))
    from bringup_check.bus import BusError, FeetechBus
    set_lang("en")
    with pytest.raises(BusError, match="Cannot open port /dev/nope"):
        FeetechBus("/dev/nope")
