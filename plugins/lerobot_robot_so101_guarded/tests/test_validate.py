"""The hardware validation runner, driven against a simulated arm through LeRobot's real robot code."""
import time

import pytest

pytest.importorskip("lerobot.robots.so_follower")

from bringup_check.guard_sim import SimMotor  # noqa: E402
from bringup_check.messages import set_lang  # noqa: E402
from lerobot_robot_so101_guarded.config_so101_follower_guarded import SO101FollowerGuardedConfig  # noqa: E402
from lerobot_robot_so101_guarded.so101_follower_guarded import SO101FollowerGuarded  # noqa: E402
from lerobot_robot_so101_guarded.validate import ALL, Runner, verdicts  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


class SimArm:
    """A FeetechMotorsBus stand-in with torque on/off, driven by SimMotor joints."""

    is_connected = True

    def __init__(self, shoulder_gravity=-45.0):
        self.motors = {m: None for m in ALL}
        self.sim = {m: SimMotor(x=0.0) for m in ALL}
        self.sim["shoulder_lift"].gravity = shoulder_gravity
        self.sim["gripper"] = SimMotor(x=50.0, kp=10.0, max_load=50.0)
        self.torque = {m: True for m in ALL}

    def sync_read(self, name, motors=None, *, normalize=True, num_retry=0):
        if name == "Present_Position":
            return {m: s.x for m, s in self.sim.items()}
        if name == "Present_Load":
            return {m: int(round(s.load * 10)) if self.torque[m] else 0 for m, s in self.sim.items()}
        if name == "Present_Temperature":
            return {m: int(s.temp) for m, s in self.sim.items()}
        raise KeyError(name)

    def sync_write(self, name, values, *, normalize=True, num_retry=0):
        for m, v in values.items():
            if self.torque[m]:
                self.sim[m].step(v, 1 / 30)

    def write(self, name, motor, value, *, normalize=True, num_retry=0):
        assert name == "Goal_Position"

    def disable_torque(self, motors=None, num_retry=0):
        for m in ([motors] if isinstance(motors, str) else motors or ALL):
            self.torque[m] = False
            self.sim[m].load = 0.0

    def enable_torque(self, motors=None, num_retry=0):
        for m in ([motors] if isinstance(motors, str) else motors or ALL):
            self.torque[m] = True

    def disconnect(self, disable_torque=True):
        self.is_connected = False


def make(tmp_path, arm):
    cfg = SO101FollowerGuardedConfig(port="/dev/null", id="t", calibration_dir=tmp_path,
                                     guard_log=str(tmp_path / "v.csv"))
    robot = SO101FollowerGuarded(cfg)
    robot.bus = arm
    robot._open_log()
    clock = Clock()
    robot._clock = clock
    return robot, clock


def scripted_user(arm, dropped=False):
    """Plays the person at the keyboard: places the book and sponge, poses the arm by hand."""
    def ask(msg):
        if "stretched-out pose" in msg:          # hand-posed, weight on the shoulder
            arm.sim["shoulder_lift"].x = 25.0
        elif "just touches the book" in msg:     # book 20° below the wrist's free position
            arm.sim["wrist_flex"].x = -20.0
            arm.sim["wrist_flex"].wall, arm.sim["wrist_flex"].wall_side = -20.0, -1
        elif "Open it fully" in msg:
            arm.sim["gripper"].x = 80.0
        elif "just touch it" in msg:             # sponge at 35 %
            arm.sim["gripper"].x = 35.0
            arm.sim["gripper"].wall, arm.sim["gripper"].wall_side = 35.0, -1
        elif "keep holding the object" in msg:
            return "n" if dropped else "y"
        return ""
    return ask


@pytest.fixture(autouse=True)
def english():
    set_lang("en")
    yield
    set_lang("ko")


def test_full_validation_passes_on_a_well_behaved_arm(tmp_path):
    arm = SimArm()
    robot, clock = make(tmp_path, arm)
    runner = Runner(robot, ask=scripted_user(arm), sleep=clock.sleep, clock=clock, sweep_s=10, hold_s=10)
    runner.all()
    robot.disconnect()
    results = {v.test: v for v in verdicts(tmp_path / "v.csv", runner.notes)}
    assert set(results) == {"sweep", "hold_pose", "wrist_block", "gripper_squeeze"}
    for v in results.values():
        assert v.passed, (v.test, v.reasons)
    # the book was never pushed through, and the wrist ends back at its free position
    assert arm.sim["wrist_flex"].x >= -20.0
    assert abs(arm.sim["wrist_flex"].x - 0.0) < 1.0


def test_dropped_object_fails_the_gripper_test(tmp_path):
    arm = SimArm()
    robot, clock = make(tmp_path, arm)
    runner = Runner(robot, ask=scripted_user(arm, dropped=True), sleep=clock.sleep, clock=clock)
    runner.all(["gripper_squeeze"])
    robot.disconnect()
    (v,) = verdicts(tmp_path / "v.csv", runner.notes)
    assert not v.passed and any("dropped" in r for r in v.reasons)


def test_guard_in_warn_mode_fails_the_blocking_tests(tmp_path):
    """Sanity check of the judge: with the guard only warning, the block tests must fail."""
    arm = SimArm()
    cfg = SO101FollowerGuardedConfig(port="/dev/null", id="t", calibration_dir=tmp_path, guard_mode="warn",
                                     guard_log=str(tmp_path / "w.csv"))
    robot = SO101FollowerGuarded(cfg)
    robot.bus, robot._clock = arm, Clock()
    robot._open_log()
    runner = Runner(robot, ask=scripted_user(arm), sleep=robot._clock.sleep, clock=robot._clock)
    runner.all(["gripper_squeeze"])
    robot.disconnect()
    (v,) = verdicts(tmp_path / "w.csv", runner.notes)
    assert not v.passed
