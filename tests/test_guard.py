import copy

import pytest

from bringup_check.guard import GuardConfig, OverloadGuard, Reading
from bringup_check.guard_sim import run, scenarios
from bringup_check.messages import set_lang

SCENARIOS = {s.name: s for s in scenarios()}
DT = 1 / 30


@pytest.fixture(autouse=True)
def english():
    set_lang("en")
    yield
    set_lang("ko")


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_guard_steps_in_only_when_it_should(name):
    sc = SCENARIOS[name]
    res = run(sc)
    assert res.acted == sc.expect_action, [(t, e.kind) for t, e in res.events]


@pytest.mark.parametrize("name", ["gripper_squeeze", "elbow_into_table"])
def test_guard_cuts_time_at_high_load_and_resumes(name):
    sc = SCENARIOS[name]
    guarded, unguarded = run(sc, guard=True), run(sc, guard=False)
    assert guarded.seconds_over_threshold < 0.5 * unguarded.seconds_over_threshold
    assert guarded.first_action_s < 3.5          # steps in within a few seconds of the push starting
    assert guarded.resumed                       # follows again once the leader is moved back


def test_gripper_keeps_a_light_grip_instead_of_dropping_the_object():
    sc = SCENARIOS["gripper_squeeze"]
    sim, g = copy.deepcopy(sc.sim), OverloadGuard()
    loads = []
    for k in range(int(7.5 / DT)):  # leader stays closed until t=8 s
        goal, _ = g.step({"gripper": sc.leader(k * DT)}, {"gripper": Reading(sim.x, sim.load)}, DT)
        sim.step(goal["gripper"], DT)
        loads.append(abs(sim.load))
    after = loads[int(4.5 / DT):]
    assert all(5.0 <= x < GuardConfig().param("gripper", "load_threshold") for x in after)  # still gripping, lightly
    assert sim.x == sc.sim.wall                                                           # still on the object


def test_weight_bearing_joint_barely_moves():
    sc = SCENARIOS["heavy_holding"]
    sim, g = copy.deepcopy(sc.sim), OverloadGuard()
    xs, kinds = [], []
    for k in range(int(sc.seconds / DT)):
        goal, ev = g.step({sc.motor: sc.leader(k * DT)}, {sc.motor: Reading(sim.x, sim.load)}, DT)
        kinds += [e.kind for e in ev]
        sim.step(goal[sc.motor], DT)
        xs.append(sim.x)
    rest = xs[int(0.7 / DT)]
    assert max(abs(x - rest) for x in xs[int(0.7 / DT):]) < 2.5   # at most a small, brief dip
    assert abs(xs[-1] - rest) < 0.1                                  # back where it was
    assert "heavy_hold" in kinds and "backoff" not in kinds


def test_warn_mode_never_changes_the_command():
    sc = SCENARIOS["elbow_into_table"]
    cfg = GuardConfig(mode="warn")
    sim, g = copy.deepcopy(sc.sim), OverloadGuard(cfg)
    kinds = []
    for k in range(int(sc.seconds / DT)):
        want = sc.leader(k * DT)
        goal, ev = g.step({sc.motor: want}, {sc.motor: Reading(sim.x, sim.load)}, DT)
        assert goal[sc.motor] == want
        kinds += [e.kind for e in ev]
        sim.step(goal[sc.motor], DT)
    assert "backoff" in kinds and "resume" in kinds


def test_temperature_warnings_and_stop():
    g = OverloadGuard()
    kinds = []
    # blocked gripper heating up quickly: too_hot makes the guard act before the strain timer
    for k, temp in enumerate([40, 50, 56, 60, 64, 64]):
        _, ev = g.step({"gripper": 0.0}, {"gripper": Reading(35.0, 40.0, temp)}, DT)
        kinds += [e.kind for e in ev]
    assert kinds[:3] == ["hot", "too_hot", "light_hold"]
    # cooling down
    for temp in [60, 56, 54]:
        _, ev = g.step({"gripper": 50.0}, {"gripper": Reading(36.0, 5.0, temp)}, DT)
        kinds += [e.kind for e in ev]
    assert "cooled" in kinds and "resume" in kinds


def test_hot_but_only_holding_weight_is_warned_not_released():
    g = OverloadGuard()
    out, ev = g.step({"shoulder_lift": 30.0}, {"shoulder_lift": Reading(32.0, 60.0, 65.0)}, DT)
    assert out["shoulder_lift"] == 30.0
    assert {e.kind for e in ev} == {"hot", "too_hot"}


def test_motors_without_readings_pass_through():
    g = OverloadGuard()
    out, ev = g.step({"a": 1.0, "b": 2.0}, {}, DT)
    assert out == {"a": 1.0, "b": 2.0} and ev == []


def test_bad_config_is_rejected():
    with pytest.raises(ValueError):
        GuardConfig(mode="off")
    with pytest.raises(ValueError):
        GuardConfig(warn_after=2.0, act_after=1.0)


def test_guard_demo_cli_runs(capsys):
    from bringup_check.cli import main
    assert main(["--guard-demo", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "gripper_squeeze" in out and "guard stepped in" in out and "not from a real SO-101" in out
