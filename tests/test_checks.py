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
