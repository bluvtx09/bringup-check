"""Hardware validation for the overload guard — one SO-100/SO-101 follower arm, no leader needed.

    so101-guard-validate --port /dev/ttyACM0 --id my_arm

Four short tests, each logged under its own phase in one CSV:

  sweep            gentle back-and-forth on every joint        -> guard must stay out
  hold_pose        you pose the arm stretched out, it holds it  -> guard must stay out
  wrist_block      wrist pushes into a book you place           -> guard must back off
  gripper_squeeze  gripper closes past a sponge you place       -> guard must ease to a light grip

Positions that depend on your setup (block, sponge, pose) are taught by hand with torque
off, so the script never assumes which direction is "down". Every move is rate-limited.
At the end it prints PASS/FAIL per test (bringup-check --analyze-log reads the same CSV).
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime
from pathlib import Path

from bringup_check.loganalyze import judge, load_rows
from bringup_check.messages import MSG, set_lang, t

from .config_so101_follower_guarded import SO101FollowerGuardedConfig
from .so101_follower_guarded import SO101FollowerGuarded

BODY = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]
ALL = BODY + ["gripper"]
TESTS = ["sweep", "hold_pose", "wrist_block", "gripper_squeeze"]
# Speed limit on the *command*: the goal may change by at most this much per cycle (90 deg/s at 30 fps).
# Deliberately not LeRobot's max_relative_target, which caps goal-minus-present and so also caps the
# holding torque of a joint that sags under the arm's weight.
MAX_STEP = 3.0

MSG["ko"].update({
    "val.intro": "가드 실물 검증을 시작합니다. 시험은 4가지이고 10분쯤 걸립니다. 언제든 Ctrl+C로 멈출 수 있습니다.",
    "val.no_calib": "캘리브레이션 파일이 없습니다: {path}\n먼저 lerobot-calibrate로 이 팔을 캘리브레이션하세요.",
    "val.base": "팔을 캘리브레이션 때의 'ㄴ자' 가운데 자세로 두세요. 준비되면 Enter: ",
    "val.hold_arm": "곧 모터 힘을 뺍니다. 팔이 떨어지지 않게 손으로 받쳐 주세요. 받쳤으면 Enter: ",
    "val.phase": "\n--- 시험: {name} ---",
    "val.sweep": "모든 관절이 ±{a:.0f}° 정도 천천히 왕복합니다({s:.0f}초). 주변을 비워 두세요. Enter: ",
    "val.pose": "팔을 앞으로 쭉 뻗은 자세(무게가 많이 걸리는 자세)로 손으로 옮긴 뒤, 받친 채로 Enter: ",
    "val.pose_hold": "이 자세를 {s:.0f}초 동안 유지합니다. 손을 떼도 됩니다.",
    "val.block_place": "손목이 아래로 굽힐 때 닿을 위치에 두꺼운 책이나 상자를 놓으세요. 놓았으면 Enter: ",
    "val.block_touch": "손목(wrist_flex)의 힘만 뺐습니다. 손목을 손으로 굽혀 책에 살짝 닿게 한 뒤 Enter: ",
    "val.block_push": "손목이 책을 {s:.0f}초 동안 밀도록 명령합니다. 가드가 물러나게 해야 합니다.",
    "val.grip_open": "그리퍼의 힘만 뺐습니다. 그리퍼를 손으로 활짝 연 뒤 Enter: ",
    "val.grip_touch": "스펀지 같은 말랑한 물체를 그리퍼 사이에 넣고, 손으로 닫아 물체에 살짝 닿게 한 뒤 Enter: ",
    "val.grip_push": "그리퍼가 물체를 {s:.0f}초 동안 더 세게 닫도록 명령합니다. 물체를 잡아 두세요.",
    "val.grip_held": "시험 동안 물체가 그리퍼에 계속 잡혀 있었나요? (y/n): ",
    "val.dir_fail": "닿은 위치와 시작 위치가 너무 가깝습니다({d:.1f}). 이 시험은 건너뜁니다.",
    "val.result": "\n=== 결과 ===",
    "val.dropped": "물체를 놓쳤다고 답함",
    "val.share": "기록: {path}\n이 파일을 GitHub 이슈에 첨부해 주시면 기준값을 다듬는 데 쓰겠습니다.",
    "val.end": "끝났습니다. 시작 자세로 돌아간 뒤 모터 힘을 뺍니다. 팔을 받쳐 주세요. Enter: ",
})
MSG["en"].update({
    "val.intro": "Starting the guard hardware validation: 4 tests, about 10 minutes. Ctrl+C stops at any time.",
    "val.no_calib": "No calibration file at {path}\nCalibrate this arm with lerobot-calibrate first.",
    "val.base": "Put the arm in the middle 'L' pose used for calibration. Press Enter when ready: ",
    "val.hold_arm": "Torque is about to turn off. Support the arm with your hand so it doesn't fall. Press Enter: ",
    "val.phase": "\n--- test: {name} ---",
    "val.sweep": "Every joint will move slowly by about ±{a:.0f}° for {s:.0f} s. Keep the area clear. Press Enter: ",
    "val.pose": "Move the arm by hand into a stretched-out pose (lots of weight on the shoulder), keep supporting it, press Enter: ",
    "val.pose_hold": "Holding this pose for {s:.0f} s. You can let go.",
    "val.block_place": "Place a thick book or box where the gripper will touch it when the wrist bends down. Press Enter: ",
    "val.block_touch": "Only the wrist (wrist_flex) is limp now. Bend it by hand until it just touches the book, press Enter: ",
    "val.block_push": "Commanding the wrist to push into the book for {s:.0f} s. The guard should back it off.",
    "val.grip_open": "Only the gripper is limp now. Open it fully by hand, press Enter: ",
    "val.grip_touch": "Put a sponge or other soft object between the jaws, close them by hand until they just touch it, press Enter: ",
    "val.grip_push": "Commanding the gripper to squeeze harder for {s:.0f} s. Keep the object in place.",
    "val.grip_held": "Did the gripper keep holding the object the whole time? (y/n): ",
    "val.dir_fail": "Touch point is too close to the start point ({d:.1f}); skipping this test.",
    "val.result": "\n=== results ===",
    "val.dropped": "you reported the object was dropped",
    "val.share": "Log: {path}\nPlease attach it to a GitHub issue; it is exactly the data needed to tune the thresholds.",
    "val.end": "Done. Returning to the start pose, then turning torque off. Support the arm. Press Enter: ",
})


class Runner:
    def __init__(self, robot, ask=input, sleep=time.sleep, clock=time.perf_counter, fps: float = 30.0,
                 sweep_s: float = 30.0, hold_s: float = 45.0, push_s: float = 8.0):
        self.robot, self.ask, self.sleep, self.clock = robot, ask, sleep, clock
        self.fps, self.sweep_s, self.hold_s, self.push_s = fps, sweep_s, hold_s, push_s
        self.goal: dict[str, float] = {}
        self.notes: dict[str, str] = {}

    # --- low-level helpers ------------------------------------------------------------
    def present(self) -> dict[str, float]:
        return {k.removesuffix(".pos"): float(v) for k, v in self.robot.get_observation().items() if k.endswith(".pos")}

    def limp(self, motors: list[str]) -> None:
        self.robot.bus.disable_torque(motors)

    def stiff(self, motors: list[str]) -> None:
        """Re-enable torque without a jump: goal := present first."""
        pos = self.present()
        for m in motors:
            self.robot.bus.write("Goal_Position", m, pos[m])
            self.goal[m] = pos[m]
        self.robot.bus.enable_torque(motors)

    def run(self, mark: str, seconds: float, goal_fn) -> None:
        self.robot.log_mark = mark
        n = int(seconds * self.fps)
        for k in range(n):
            t0 = self.clock()
            for m, v in goal_fn(k / self.fps).items():
                prev = self.goal.get(m, v)
                self.goal[m] = prev + max(-MAX_STEP, min(MAX_STEP, v - prev))
            self.robot.send_action({f"{m}.pos": v for m, v in self.goal.items()})
            spare = 1 / self.fps - (self.clock() - t0)
            if spare > 0:
                self.sleep(spare)
        self.robot.log_mark = ""

    def move_to(self, target: dict[str, float], seconds: float = 2.0, mark: str = "move") -> None:
        start = dict(self.goal)

        def f(tt):
            a = min(1.0, tt / seconds)
            return {m: start[m] + (v - start[m]) * a for m, v in target.items()}
        self.run(mark, seconds + 0.5, f)

    # --- tests --------------------------------------------------------------------------
    def sweep(self, base) -> None:
        amp = 15.0
        self.ask(t("val.sweep", a=amp, s=self.sweep_s))
        self.move_to({"gripper": 50.0})  # keep the gripper away from its own end stops
        center = {**base, "gripper": 50.0}
        phases = {m: i * 0.7 for i, m in enumerate(ALL)}

        def f(tt):
            ramp = min(1.0, tt / 2.0)  # ease in, so nothing jumps at the start
            return {m: center[m] + ramp * amp * math.sin(2 * math.pi * 0.25 * tt + phases[m]) for m in ALL}
        self.run("sweep", self.sweep_s, f)
        self.move_to(base)

    def hold_pose(self, base) -> None:
        self.ask(t("val.hold_arm"))
        self.limp(BODY)
        self.ask(t("val.pose"))
        self.stiff(BODY)
        print(t("val.pose_hold", s=self.hold_s))
        pose = dict(self.goal)
        self.run("hold_pose", self.hold_s, lambda tt: pose)
        self.move_to(base, 3.0)

    def _teach(self, motor: str, first_msg: str, second_msg: str, place_msg: str | None = None):
        if place_msg:
            self.ask(t(place_msg))
        self.limp([motor])
        self.ask(t(first_msg))
        a = self.present()[motor]
        self.ask(t(second_msg))
        b = self.present()[motor]
        self.stiff([motor])
        return a, b

    def wrist_block(self, base) -> None:
        free = self.goal["wrist_flex"]
        self.ask(t("val.block_place"))
        self.limp(["wrist_flex"])
        self.ask(t("val.block_touch"))
        contact = self.present()["wrist_flex"]
        self.stiff(["wrist_flex"])
        d = contact - free
        if abs(d) < 5:
            print(t("val.dir_fail", d=d))
            self.notes["wrist_block"] = "skipped"
            return
        push = contact + math.copysign(30.0, d)
        print(t("val.block_push", s=self.push_s))
        self.run("wrist_block", self.push_s, lambda tt: {"wrist_flex": push})
        self.move_to({"wrist_flex": free})

    def gripper_squeeze(self, base) -> None:
        opened, contact = self._teach("gripper", "val.grip_open", "val.grip_touch")
        d = contact - opened
        if abs(d) < 5:
            print(t("val.dir_fail", d=d))
            self.notes["gripper_squeeze"] = "skipped"
            return
        self.move_to({"gripper": opened})
        self.move_to({"gripper": contact}, 1.5)
        squeeze = contact + math.copysign(30.0, d)
        print(t("val.grip_push", s=self.push_s))
        self.run("gripper_squeeze", self.push_s, lambda tt: {"gripper": squeeze})
        held = self.ask(t("val.grip_held")).strip().lower().startswith("y")
        if not held:
            self.notes["gripper_squeeze"] = "dropped"
        self.move_to({"gripper": opened})

    def all(self, tests=TESTS) -> None:
        print(t("val.intro"))
        self.ask(t("val.base"))
        base = self.present()
        self.goal = dict(base)
        try:
            for name in tests:
                print(t("val.phase", name=name))
                getattr(self, name)(base)
        finally:
            try:
                self.move_to(base, 3.0)
            except Exception:
                pass


def verdicts(log_path, notes: dict[str, str]):
    rows = load_rows(log_path)
    out = []
    for name in TESTS:
        if notes.get(name) == "skipped":
            continue
        phase = [r for r in rows if r.get("mark") == name]
        if not phase:
            continue
        v = judge(name, phase)
        if name == "gripper_squeeze" and notes.get(name) == "dropped":
            v.passed = False
            v.reasons.append(t("val.dropped"))
        out.append(v)
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="so101-guard-validate", description=__doc__.split("\n")[0])
    p.add_argument("--port", required=True)
    p.add_argument("--id", required=True, help="the robot id you used with lerobot-calibrate")
    p.add_argument("--calibration_dir", default=None)
    p.add_argument("--tests", default=",".join(TESTS))
    p.add_argument("--log", default=None, help="CSV path (default: guard_validation_<time>.csv)")
    p.add_argument("--lang", choices=["ko", "en"])
    a = p.parse_args(argv)
    if a.lang:
        set_lang(a.lang)
    log = a.log or f"guard_validation_{datetime.now():%Y%m%d_%H%M%S}.csv"
    kw = {"calibration_dir": Path(a.calibration_dir)} if a.calibration_dir else {}
    cfg = SO101FollowerGuardedConfig(port=a.port, id=a.id, guard_log=log, **kw)
    robot = SO101FollowerGuarded(cfg)
    if not robot.calibration_fpath.is_file():
        print(t("val.no_calib", path=robot.calibration_fpath))
        return 2
    robot.connect()
    runner = Runner(robot)
    try:
        runner.all([x for x in a.tests.split(",") if x in TESTS])
        input(t("val.end"))
    except KeyboardInterrupt:
        print()
    finally:
        robot.disconnect()
    print(t("val.result"))
    results = verdicts(log, runner.notes)
    for v in results:
        print(f"[{'PASS' if v.passed else 'FAIL'}] {v.test}: " + "; ".join(v.reasons))
    print(t("val.share", path=Path(log).resolve()))
    return 0 if results and all(v.passed for v in results) else 1


if __name__ == "__main__":
    sys.exit(main())
