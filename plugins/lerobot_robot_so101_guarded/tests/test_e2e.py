"""End-to-end: LeRobot's own config parsing and robot factory, with the serial bus replaced
by a simple servo model. Needs LeRobot installed; skipped otherwise.

    pip install -e ../..  -e .  "lerobot[feetech]"  pytest
    pytest
"""
import csv
import time
from dataclasses import dataclass

import pytest

pytest.importorskip("lerobot.robots.so_follower")

import draccus  # noqa: E402
from lerobot.robots.config import RobotConfig  # noqa: E402
from lerobot.robots.utils import make_robot_from_config  # noqa: E402
from lerobot.utils.import_utils import register_third_party_plugins  # noqa: E402

from bringup_check.guard_sim import SimMotor  # noqa: E402
from bringup_check.messages import set_lang  # noqa: E402

MOTORS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]


@dataclass
class Wrapper:  # same shape as lerobot-teleoperate's config: a `robot` field
    robot: RobotConfig


class FakeBus:
    """Stands in for FeetechMotorsBus. The gripper closes on an object at 35 %; other joints are free."""

    is_connected = True

    def __init__(self):
        self.motors = {m: None for m in MOTORS}
        self.sim = {m: SimMotor(x=0.0) for m in MOTORS}
        self.sim["gripper"] = SimMotor(x=60.0, kp=10.0, max_load=50.0, wall=35.0, wall_side=-1)
        self.reads = []

    def sync_read(self, name, motors=None, *, normalize=True, num_retry=0):
        self.reads.append((name, normalize))
        if name == "Present_Position":
            return {m: s.x for m, s in self.sim.items()}
        if name == "Present_Load":  # raw, signed, 0..1000
            return {m: int(round(s.load * 10)) for m, s in self.sim.items()}
        if name == "Present_Temperature":
            return {m: int(s.temp) for m, s in self.sim.items()}
        raise KeyError(name)

    def sync_write(self, name, values, *, normalize=True, num_retry=0):
        assert name == "Goal_Position"
        for m, v in values.items():
            self.sim[m].step(v, 1 / 30)

    def disconnect(self, disable_torque=True):
        self.is_connected = False


def make(tmp_path, *extra):
    register_third_party_plugins()
    cfg = draccus.parse(config_class=Wrapper, args=[
        "--robot.type=so101_follower_guarded", "--robot.port=/dev/null", "--robot.id=test_arm",
        f"--robot.calibration_dir={tmp_path}", *extra]).robot
    robot = make_robot_from_config(cfg)
    robot.bus = FakeBus()
    robot._open_log()
    return robot


def drive(robot, seconds, leader_gripper):
    sent = []
    for k in range(int(seconds * 30)):
        action = {f"{m}.pos": 0.0 for m in MOTORS}
        action["gripper.pos"] = leader_gripper(k / 30)
        robot._last_t = time.perf_counter() - 1 / 30  # emulate 30 fps in a tight loop
        sent.append(robot.send_action(action)["gripper.pos"])
    robot.disconnect()
    return sent


def test_plugin_is_found_and_reuses_the_so101_calibration_name(tmp_path):
    set_lang("en")
    robot = make(tmp_path)
    assert type(robot).__name__ == "SO101FollowerGuarded"
    from lerobot.robots.so_follower import SO101Follower
    assert robot.name == SO101Follower.name


def test_squeezing_gripper_is_eased_then_follows_again(tmp_path, capsys):
    set_lang("en")
    log = tmp_path / "guard.csv"
    robot = make(tmp_path, f"--robot.guard_log={log}")
    sent = drive(robot, 8, lambda t: 0.0 if t < 6 else 50.0)
    assert sent[30] == 0.0                     # at first, follows the leader
    assert sent[120] == pytest.approx(33.5)    # then a light grip just past the object at 35
    assert sent[210] == 50.0                   # leader opened: follows again
    assert all(n is False for name, n in robot.bus.reads if name != "Present_Position")
    out = capsys.readouterr().out
    assert "light grip" in out and "following the leader again" in out
    rows = list(csv.reader(log.open()))
    assert rows[0][:3] == ["t", "motor", "goal"] and len(rows) == 1 + 8 * 30 * 6


def test_warn_mode_never_changes_the_action(tmp_path):
    set_lang("en")
    robot = make(tmp_path, "--robot.guard_mode=warn")
    sent = drive(robot, 5, lambda t: 0.0)
    assert all(v == 0.0 for v in sent)


def test_read_failure_passes_actions_through(tmp_path):
    set_lang("en")
    robot = make(tmp_path)

    def broken(*a, **k):
        raise ConnectionError("no status packet")

    robot.bus.sync_read = broken
    sent = drive(robot, 1, lambda t: 10.0)
    assert all(v == 10.0 for v in sent)
