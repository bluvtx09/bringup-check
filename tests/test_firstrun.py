import pytest

from bringup_check.checks import SO101_NAMES
from bringup_check.cli import main
from bringup_check.firstrun import FirstRun
from bringup_check.firstrun_sim import scenarios

IDS = [1, 2, 3, 4, 5, 6]


def run(name, confirm=None):
    sc = scenarios()[name]
    bus = sc["make"]()
    res = FirstRun(bus, IDS, SO101_NAMES, sc["calib"], sleep=bus.advance, confirm=confirm, log=lambda s: None).run()
    return sc, bus, res


def powered_on(bus):
    return [(i, v) for i, n, v in bus.writes if n == "Torque_Enable" and v == 1]


@pytest.mark.parametrize("name", list(scenarios()))
def test_each_scenario_ends_as_expected(name):
    sc, bus, res = run(name)
    assert res.completed == sc["expect"]["completed"]
    codes = {f.code for f in res.findings}
    for code in sc["expect"]["codes"]:
        assert code in codes, (name, codes)


@pytest.mark.parametrize("name", list(scenarios()))
def test_everything_is_restored_afterwards(name):
    _, bus, _ = run(name)
    for i, regs in bus.regs.items():
        assert regs.get("Torque_Enable", 0) == 0
        assert regs["Torque_Limit"] == 1000


def test_power_or_calibration_problem_means_nothing_moves():
    for name in ("chain_break", "no_calibration"):
        _, bus, res = run(name)
        assert powered_on(bus) == []
        assert res.trials == []


def test_torque_rises_in_steps_and_gripper_stays_capped():
    _, bus, res = run("healthy")
    limits = lambda m: [v for i, n, v in bus.writes if i == m and n == "Torque_Limit"]
    assert limits(1) == [200, 500, 1000, 1000]          # 20 %, 50 %, 100 %, then restored
    assert max(limits(6)[:-1]) == 500                   # gripper never above 50 % during the test
    # the shoulder could not lift the arm at 20 % but did at 50 %: reported as weight, not as a fault
    sh = [tr for tr in res.trials if tr.motor == 2]
    assert [tr.reached for tr in sh] == [False, True, True]
    assert [f.code for f in res.findings] == ["fr.weight"]


def test_safety_stop_powers_everything_off_and_moves_nothing_more():
    _, bus, res = run("faulty_servo")
    assert res.findings[0].code == "fr.safety_status" and res.findings[0].motors == [4]
    assert {i for i, _ in powered_on(bus)} == {1, 2, 3, 4}   # 5 and 6 were never powered
    assert res.stage == "motion" and not res.completed


def test_stuck_joint_is_retried_and_only_reported_at_full_power():
    _, _, res = run("stuck_elbow")
    elbow = [tr for tr in res.trials if tr.motor == 3]
    assert [tr.level for tr in elbow] == [20, 50, 100] and not any(tr.reached for tr in elbow)
    blocked = [f for f in res.findings if f.code == "fr.blocked"]
    assert len(blocked) == 1 and blocked[0].motors == [3]


def test_saying_no_stops_before_raising_torque():
    asked = []

    def confirm(prompt):
        asked.append(prompt)
        return len(asked) == 1        # yes to start, no to the next level

    _, bus, res = run("healthy", confirm=confirm)
    assert not res.completed and len(asked) == 2
    assert {tr.level for tr in res.trials} == {20}
    assert all(r.get("Torque_Enable", 0) == 0 and r["Torque_Limit"] == 1000 for r in bus.regs.values())


def test_cli_demo_runs(capsys):
    assert main(["--lang", "en", "--first-run-demo", "stuck_elbow"]) == 0
    out = capsys.readouterr().out
    assert "[4/4] Normal power" in out and "pushes but cannot move" in out
