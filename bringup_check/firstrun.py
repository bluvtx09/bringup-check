"""First-run safety mode: bring a freshly built arm up step by step, at low power first.

Order, each step gated on the one before:
  1. power and wiring  - every motor answers, supply voltage and status flags are sane
  2. calibration       - a LeRobot calibration file matches the arm and the arm sits inside it
  3. low-power motion  - each motor in turn makes a small move toward the middle of its range
                         with the torque limit at 20 %, then back
  4. normal power      - the same at 50 % and then the normal limit (100 %, gripper 50 %)

While any motor is powered, every reading is checked. The whole run stops and all torque
is released at once when a servo reports an error flag, gets too hot, or the supply sags.
A motor that pushes hard without moving is stopped on its own: at low power that can be
the arm's weight, so it is retried at the next level; at normal power it means something
is in the way (a tight screw, a caught cable, a misassembled horn).

Original Torque_Limit and Torque_Enable values are restored at the end, even after an
error or Ctrl+C. Thresholds are starting points, not values validated on real arms.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

from .checks import INFO, LEVEL_ORDER, PROBLEM, Finding, label, make, run_all, take_snapshot
from .messages import t
from .registers import decode_status, load_percent, volts

STEPS_PER_DEG = 4096 / 360.0
STOP_BITS = {"voltage", "temperature", "current", "overload"}


@dataclass
class FirstRunConfig:
    levels: tuple[int, ...] = (20, 50, 100)   # torque limit per stage, % of max
    gripper_cap: int = 50                     # LeRobot runs the SO-101 gripper at 50 %
    move_deg: tuple[float, ...] = (4.0, 6.0, 8.0)   # move size at each level
    hz: float = 20.0
    move_s: float = 0.8                       # time to ramp to the target (and back)
    settle_s: float = 0.5                     # hold at the target
    reach_frac: float = 0.6                   # counts as reached within this share of the move
    blocked_load: float = 0.85                # share of the level that counts as pushing hard
    blocked_s: float = 0.5
    sag_frac: float = 0.15                    # supply may drop this share below idle before we stop
    temp_margin: int = 10                     # stop this many °C below the servo's limit
    gripper_ids: frozenset = frozenset({6})


@dataclass
class MotorTrial:
    motor: int
    level: int
    target_steps: int
    reached: bool
    blocked: bool
    peak_load: float
    moved_steps: int


@dataclass
class FirstRunResult:
    completed: bool
    stage: str                                # last stage reached: power/calibration/motion/done
    findings: list[Finding] = field(default_factory=list)
    trials: list[MotorTrial] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


class SafetyStop(Exception):
    def __init__(self, finding: Finding):
        super().__init__(finding.title)
        self.finding = finding


def _center_direction(pos: int, calib_entry: dict | None) -> int:
    """Move toward the middle of the calibrated range, so a test move never nears a limit."""
    if calib_entry and calib_entry.get("range_min") is not None and calib_entry.get("range_max") is not None:
        mid = (calib_entry["range_min"] + calib_entry["range_max"]) / 2
    else:
        mid = 2048
    return 1 if pos <= mid else -1


def _calib_by_id(calib: dict | None) -> dict[int, dict]:
    out = {}
    for name, c in (calib or {}).items():
        if isinstance(c, dict) and "id" in c:
            out[c["id"]] = {**c, "name": name}
    return out


class FirstRun:
    def __init__(self, bus, ids, names, calib: dict | None, config: FirstRunConfig | None = None,
                 sleep=time.sleep, confirm=None, log=print):
        self.bus, self.ids, self.names = bus, list(ids), names or {}
        self.calib = calib
        self.cfg = config or FirstRunConfig()
        self.sleep = sleep
        self.confirm = confirm or (lambda prompt: True)
        self.out = log
        self.res = FirstRunResult(False, "power")
        self.saved: dict[int, dict[str, int | None]] = {}
        self.idle_v: float | None = None

    def say(self, text: str) -> None:
        self.res.log.append(text)
        self.out(text)

    # --- steps 1 and 2 ------------------------------------------------------------

    def check_power_and_calibration(self) -> bool:
        snap = take_snapshot(self.bus, self.ids)
        findings = run_all(snap, self.names, self.calib, gripper_ids=self.cfg.gripper_ids)
        power = [f for f in findings if f.code.split(".")[0] in ("bus", "volt", "status", "temp") and f.level == PROBLEM]
        self.say(t("fr.step1"))
        if power:
            self.res.findings += power
            for f in power:
                self.say(f"  [{t('level.problem')}] {f.title}\n      {f.fix}")
            self.say(t("fr.stop_power"))
            return False
        volts_now = [volts(snap.get(i, "Present_Voltage")) for i in snap.responding
                     if snap.get(i, "Present_Voltage") is not None]
        self.idle_v = statistics.median(volts_now) if volts_now else None
        self.say("  " + t("fr.power_ok", n=len(snap.responding), v=self.idle_v or 0.0))

        self.res.stage = "calibration"
        self.say(t("fr.step2"))
        if not self.calib:
            self.res.findings.append(make(PROBLEM, "fr.no_calib", self.ids))
            self.say("  " + t("fr.no_calib.title") + "\n      " + t("fr.no_calib.fix"))
            return False
        cal = [f for f in findings if f.code.split(".")[0] in ("calib", "limit")
               and (f.level == PROBLEM or f.code in ("calib.outside", "calib.wrap"))]
        if cal:
            self.res.findings += cal
            for f in cal:
                self.say(f"  [{t('level.problem') if f.level == PROBLEM else t('level.warn')}] {f.title}\n      {f.fix}")
            self.say(t("fr.stop_calib"))
            return False
        self.say("  " + t("fr.calib_ok"))
        self.snapshot = snap
        return True

    # --- steps 3 and 4 ------------------------------------------------------------

    def _save(self) -> None:
        for i in self.ids:
            self.saved[i] = {k: self.bus.read(i, k) for k in ("Torque_Limit", "Torque_Enable")}

    def restore(self) -> None:
        for i, regs in self.saved.items():
            self.bus.write(i, "Torque_Enable", 0)
            if regs.get("Torque_Limit") is not None:
                self.bus.write(i, "Torque_Limit", regs["Torque_Limit"])
            if regs.get("Torque_Enable"):
                pos = self.bus.read(i, "Present_Position")
                if pos is not None:
                    self.bus.write(i, "Goal_Position", pos)
                self.bus.write(i, "Torque_Enable", regs["Torque_Enable"])

    def _all_off(self) -> None:
        for i in self.ids:
            self.bus.write(i, "Torque_Enable", 0)

    def _watch(self, powered: list[int]) -> dict[int, dict]:
        """Read every powered motor once and stop everything on a safety condition."""
        reads = {}
        for i in powered:
            r = {k: self.bus.read(i, k) for k in
                 ("Present_Position", "Present_Load", "Present_Voltage", "Present_Temperature", "Status")}
            reads[i] = r
            m = label(i, self.names)
            bits = set(decode_status(r["Status"] or 0)) & STOP_BITS
            if bits:
                raise SafetyStop(make(PROBLEM, "fr.safety_status", [i], m=m,
                                      flags=", ".join(t(f"bit.{b}") for b in sorted(bits))))
            limit = self.snapshot.get(i, "Max_Temperature_Limit") or 70
            if r["Present_Temperature"] is not None and r["Present_Temperature"] >= limit - self.cfg.temp_margin:
                raise SafetyStop(make(PROBLEM, "fr.safety_temp", [i], m=m, temp=r["Present_Temperature"], lim=limit))
            if self.idle_v and r["Present_Voltage"] is not None:
                v = volts(r["Present_Voltage"])
                if v < self.idle_v * (1 - self.cfg.sag_frac):
                    raise SafetyStop(make(PROBLEM, "fr.safety_sag", [i], m=m, v0=self.idle_v, v1=v))
        return reads

    def _trial(self, i: int, level: int, deg: float, cal: dict | None) -> MotorTrial:
        cfg, bus = self.cfg, self.bus
        start = bus.read(i, "Present_Position")
        if start is None:
            raise SafetyStop(make(PROBLEM, "fr.lost", [i], m=label(i, self.names)))
        amp = int(round(deg * STEPS_PER_DEG))
        target = start + _center_direction(start, cal) * amp
        bus.write(i, "Torque_Limit", level * 10)
        bus.write(i, "Goal_Position", start)
        bus.write(i, "Torque_Enable", 1)
        dt = 1.0 / cfg.hz
        ramp = max(1, int(cfg.move_s * cfg.hz))
        hold = max(1, int(cfg.settle_s * cfg.hz))
        plan = ([start + (target - start) * (k + 1) / ramp for k in range(ramp)] + [target] * hold
                + [target + (start - target) * (k + 1) / ramp for k in range(ramp)] + [start] * hold)
        peak, strain, blocked, best = 0.0, 0.0, False, 0
        powered = [i]
        for k, goal in enumerate(plan):
            bus.write(i, "Goal_Position", int(round(goal)))
            self.sleep(dt)
            r = self._watch(powered)[i]
            pos = r["Present_Position"]
            load = abs(load_percent(r["Present_Load"] or 0))
            peak = max(peak, load)
            if pos is not None and k < ramp + hold:
                best = max(best, abs(pos - start) if (pos - start) * (target - start) > 0 else 0)
            gap = abs(goal - pos) if pos is not None else 0
            if load >= cfg.blocked_load * level and gap >= 0.5 * amp:
                strain += dt
                if strain >= cfg.blocked_s:
                    blocked = True
                    break
            else:
                strain = max(0.0, strain - dt)
        # let go of the push, then hand the joint back unpowered
        pos = bus.read(i, "Present_Position")
        if pos is not None:
            bus.write(i, "Goal_Position", pos)
        bus.write(i, "Torque_Enable", 0)
        reached = not blocked and best >= cfg.reach_frac * amp
        return MotorTrial(i, level, amp, reached, blocked, peak, best)

    def run_motion(self) -> bool:
        cfg = self.cfg
        cal = _calib_by_id(self.calib)
        order = [i for i in self.ids if i in self.snapshot.responding]
        self._save()
        pending = set(order)                       # motors that have not yet moved freely
        try:
            for n, level in enumerate(cfg.levels):
                self.res.stage = "motion"
                deg = cfg.move_deg[min(n, len(cfg.move_deg) - 1)]
                head = "fr.step3" if n == 0 else ("fr.step4" if level >= 100 else "fr.step_mid")
                self.say(t(head, level=level, deg=deg))
                if n > 0 and not self.confirm(t("fr.next_prompt", level=level)):
                    self.say(t("fr.aborted"))
                    return False
                for i in order:
                    lv = min(level, cfg.gripper_cap) if i in cfg.gripper_ids else level
                    tr = self._trial(i, lv, deg, cal.get(i))
                    self.res.trials.append(tr)
                    m = label(i, self.names)
                    if tr.reached:
                        pending.discard(i)
                        self.say("  " + t("fr.motor_ok", m=m, level=lv, load=tr.peak_load))
                    elif level < cfg.levels[-1]:
                        self.say("  " + t("fr.motor_weak", m=m, level=lv, moved=tr.moved_steps / STEPS_PER_DEG, deg=deg))
                    else:
                        f = make(PROBLEM, "fr.blocked" if tr.blocked else "fr.no_move", [i], m=m, pct=lv,
                                 moved=tr.moved_steps / STEPS_PER_DEG, deg=deg, load=tr.peak_load)
                        self.res.findings.append(f)
                        self.say(f"  [{t('level.problem')}] {f.title}\n      {f.fix}")
            if pending:
                self.say(t("fr.stop_motion"))
                return False
            self.res.stage = "done"
            self.res.completed = True
            heavy = [i for i in order if any(tr.motor == i and not tr.reached for tr in self.res.trials)]
            for i in heavy:   # needed more than the lowest level, then moved fine: usually the arm's weight
                self.res.findings.append(make(INFO, "fr.weight", [i], m=label(i, self.names)))
            self.say(t("fr.done"))
            return True
        except SafetyStop as s:
            self._all_off()
            self.res.findings.append(s.finding)
            self.say(f"\n[{t('level.problem')}] {s.finding.title}\n    {s.finding.fix}\n" + t("fr.stopped"))
            return False
        finally:
            self.restore()
            self.say(t("fr.restored"))

    def run(self) -> FirstRunResult:
        self.say(t("fr.intro"))
        if not self.check_power_and_calibration():
            return self.res
        if not self.confirm(t("fr.start_prompt")):
            self.say(t("fr.aborted"))
            return self.res
        self.run_motion()
        if self.res.completed:
            self.say(t("fr.next"))
        self.res.findings.sort(key=lambda f: LEVEL_ORDER.get(f.level, 3))
        return self.res
