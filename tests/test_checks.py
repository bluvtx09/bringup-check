import pytest

from bringup_check.checks import PROBLEM, SO101_NAMES, WARN, run_hold_test, take_snapshot
from bringup_check.cli import diagnose, main
from bringup_check.registers import decode_status, load_percent, milliamps, sign_magnitude, volts
from bringup_check.messages import set_lang
from bringup_check.scenarios import build, make_bus


@pytest.fixture(autouse=True)
def korean_output():
    set_lang("ko")
    yield
    set_lang("ko")

SCENARIOS = build()
IDS = [1, 2, 3, 4, 5, 6]


def _run(sc):
    bus = make_bus(sc)
    snap, findings, hold, text = diagnose(bus, IDS, SO101_NAMES, sc["calib"], sc["servo_rating"],
                                          2.0 if sc["hold_test"] else 0.0, sleep=lambda _: None)
    return bus, findings, text


def test_unit_conversions():
    assert volts(76) == pytest.approx(7.6)
    assert milliamps(120) == pytest.approx(780)
    assert sign_magnitude(0b100_0000_0101, 10) == -5
    assert load_percent(850) == pytest.approx(85.0)
    assert load_percent(1024 + 300) == pytest.approx(-30.0)
    assert decode_status(0b100001) == ["voltage", "overload"]


def test_healthy_robot_has_no_problems_or_warnings():
    _, findings, _ = _run(SCENARIOS["healthy"])
    bad = [f.title for f in findings if f.level in (PROBLEM, WARN)]
    assert bad == []


@pytest.mark.parametrize("name", [n for n in SCENARIOS if n != "healthy"])
def test_each_incident_scenario_is_detected(name):
    sc = SCENARIOS[name]
    _, findings, _ = _run(sc)
    codes = {f.code for f in findings}
    for code in sc["expect"]:
        assert code in codes, f"{name}: expected {code}, got {sorted(codes)}"


def test_chain_break_points_to_the_right_cable():
    _, findings, _ = _run(SCENARIOS["chain_break"])
    f = next(f for f in findings if f.code == "bus.chain_break")
    assert "3번 모터" in f.fix and "4번 모터" in f.fix
    assert f.motors == [4, 5, 6]


def test_hot_outlier_names_only_the_hot_motor():
    _, findings, _ = _run(SCENARIOS["hot_outlier"])
    hot = [f for f in findings if f.code == "temp.outlier"]
    assert [f.motors for f in hot] == [[3]]


def test_hold_test_holds_in_place_and_restores_torque():
    sc = SCENARIOS["weak_supply"]
    bus = make_bus(sc)
    snap = take_snapshot(bus, IDS)
    run_hold_test(bus, snap, SO101_NAMES, seconds=1, hz=5, sleep=lambda _: None)
    for i in IDS:
        writes = [(n, v) for (m, n, v) in bus.writes if m == i]
        # goal is set to the present position before torque turns on, so nothing jumps
        assert writes[0] == ("Goal_Position", sc["motors"][i]["Present_Position"])
        assert writes[1] == ("Torque_Enable", 1)
        # torque returns to its original state (off) afterwards
        assert writes[-1] == ("Torque_Enable", 0)


def test_weak_supply_is_a_problem_level_finding():
    _, findings, _ = _run(SCENARIOS["weak_supply"])
    sag = next(f for f in findings if f.code == "hold.sag")
    assert sag.level == PROBLEM


def test_no_motor_answers():
    from bringup_check.bus import FakeBus
    bus = FakeBus({})
    _, findings, _, _ = diagnose(bus, IDS, SO101_NAMES)
    assert [f.code for f in findings] == ["bus.none"]


def test_cli_demo_runs(capsys):
    assert main(["--demo", "all"]) == 0
    out = capsys.readouterr().out
    assert "체인 중간 단선" in out and "결과:" in out


def test_supply_wide_overvoltage_is_reported_once():
    _, findings, _ = _run(SCENARIOS["overvoltage"])
    over = [f for f in findings if f.code == "volt.over"]
    assert len(over) == 1 and over[0].motors == IDS


def test_gripper_heat_is_explained_by_load_not_called_a_bad_unit():
    _, findings, _ = _run(SCENARIOS["gripper_squeeze"])
    assert "temp.outlier" not in {f.code for f in findings}


def test_feetech_bus_uses_sdk_signatures(monkeypatch):
    """Plumbing check against a stand-in for scservo_sdk with the SDK's call/return shapes."""
    import sys
    import types

    calls = []

    class Port:
        def __init__(self, name): self.name = name
        def openPort(self): return True
        def setBaudRate(self, b): return True
        def closePort(self): calls.append("close")

    class PH:
        def ping(self, port, i): return (777, 0, 0) if i == 1 else (0, -6, 0)
        def read1ByteTxRx(self, port, i, addr): calls.append(("r1", i, addr)); return 50, 0, 0
        def read2ByteTxRx(self, port, i, addr): calls.append(("r2", i, addr)); return 2048, 0, 0
        def write1ByteTxRx(self, port, i, addr, v): calls.append(("w1", i, addr, v)); return 0, 0
        def write2ByteTxRx(self, port, i, addr, v): calls.append(("w2", i, addr, v)); return 0, 0

    fake = types.SimpleNamespace(PortHandler=Port, PacketHandler=lambda end: PH(), COMM_SUCCESS=0)
    monkeypatch.setitem(sys.modules, "scservo_sdk", fake)
    from bringup_check.bus import FeetechBus
    bus = FeetechBus("/dev/null")
    assert bus.ping(1) == 777 and bus.ping(2) is None
    assert bus.read(1, "Present_Voltage") == 50 and ("r1", 1, 62) in calls
    assert bus.read(1, "Present_Position") == 2048 and ("r2", 1, 56) in calls
    assert bus.write(1, "Torque_Enable", 1) and ("w1", 1, 40, 1) in calls
    bus.close()
    assert "close" in calls


@pytest.mark.parametrize("lang,words", [("ko", ["3번 모터", "멀티미터", "바꿔 끼워"]),
                                        ("en", ["motor 3", "multimeter", "swap"])])
def test_single_low_motor_fix_names_it_and_gives_both_checks(lang, words):
    import copy
    sc = copy.deepcopy(SCENARIOS["healthy"])
    sc["motors"][3]["Present_Voltage"] -= 6          # only motor 3 reads 0.6 V low
    set_lang(lang)
    _, findings, _ = _run(sc)
    f = next(f for f in findings if f.code == "volt.spread")
    assert f.motors == [3]
    assert all(w.lower() in f.fix.lower() for w in words), f.fix


# --- arm profiles (SO-ARM 102: mixed servo models, 12 V follower / 5 V leader) ---

from bringup_check.profiles import PROFILES, STS3235, STS3250
from bringup_check.scenarios import FakeBus, healthy_motors


def _arm102_follower(volts_x10=120):
    m = healthy_motors()
    for i, model in PROFILES["so-arm102-follower"].models.items():
        m[i]["Model_Number"] = model
        m[i]["Present_Voltage"] = volts_x10
    return m


def _diag(motors, profile):
    p = PROFILES[profile]
    _, findings, _, _ = diagnose(FakeBus(motors, {}), IDS, SO101_NAMES, None, p.servo_rating,
                                 expected_model=p.models)
    return findings


def test_so_arm102_follower_with_its_own_profile_is_clean():
    codes = [f.code for f in _diag(_arm102_follower(), "so-arm102-follower") if f.level in (PROBLEM, WARN)]
    assert "bus.model" not in codes and not any(c.startswith("volt.rating") for c in codes)


def test_so_arm102_follower_under_the_default_profile_flags_the_mixed_servos():
    f = [f for f in _diag(_arm102_follower(), "so101") if f.code == "bus.model"]
    assert f and f[0].motors == [1, 2, 3, 4]


def test_so_arm102_wrong_servo_in_a_slot_names_the_expected_model():
    m = _arm102_follower()
    m[2]["Model_Number"] = STS3235                     # a base servo fitted at the shoulder
    f = [f for f in _diag(m, "so-arm102-follower") if f.code == "bus.model"]
    assert f and f[0].motors == [2] and "STS3250" in f[0].detail and "STS3235" in f[0].detail


def test_so_arm102_swapped_supplies_are_caught_both_ways():
    follower_on_5v = _diag(_arm102_follower(volts_x10=50), "so-arm102-follower")
    assert any(f.code == "volt.rating_low" for f in follower_on_5v)
    leader = healthy_motors()
    for i in leader:
        leader[i]["Present_Voltage"] = 120                # leader plugged into the follower's 12 V supply
    assert any(f.code == "volt.rating_high" and f.level == PROBLEM for f in _diag(leader, "so-arm102-leader"))
    for i in leader:
        leader[i]["Present_Voltage"] = 50                 # its own 5 V supply is fine
    assert not any(f.code.startswith("volt.rating") for f in _diag(leader, "so-arm102-leader"))
