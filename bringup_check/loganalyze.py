"""Summarise a guard log (guard.csv) and judge the hardware validation tests.

Log columns (written by the LeRobot plugin):
t, motor, goal, sent, present, load, temp, released, strain, event, mark, mode
`event` holds the guard events of that cycle joined by "|"; `mark` labels the test phase.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .guard import GuardConfig

INTERVENE = {"backoff", "light_hold", "jammed"}
TESTS = ("gripper_squeeze", "wrist_block", "hold_pose", "sweep")


@dataclass
class MotorSummary:
    motor: str
    cycles: int = 0
    max_load: float = 0.0
    seconds_high: float = 0.0          # at or above the guard's load threshold
    seconds_released: float = 0.0
    max_temp: float | None = None
    events: list[tuple[float, str]] = field(default_factory=list)

    def first(self, kinds) -> float | None:
        return next((t for t, k in self.events if k in kinds), None)

    def count(self, kinds) -> int:
        return sum(1 for _, k in self.events if k in kinds)


@dataclass
class LogSummary:
    duration: float
    mode: str
    motors: dict[str, MotorSummary]
    marks: list[str]


def _f(x: str) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_rows(path: str | Path, mark: str | None = None) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if mark is not None:
        rows = [r for r in rows if r.get("mark", "") == mark]
    return rows


def summarize(rows: list[dict], config: GuardConfig | None = None) -> LogSummary:
    cfg = config or GuardConfig()
    by_motor: dict[str, list[dict]] = {}
    for r in rows:
        by_motor.setdefault(r["motor"], []).append(r)
    motors: dict[str, MotorSummary] = {}
    t_all = [_f(r["t"]) for r in rows if _f(r["t"]) is not None]
    for m, rs in by_motor.items():
        s = MotorSummary(m, cycles=len(rs))
        thr = cfg.param(m, "load_threshold")
        prev_t = None
        for r in rs:
            t = _f(r["t"])
            dt = 0.0 if prev_t is None or t is None else max(0.0, t - prev_t)
            prev_t = t
            load = abs(_f(r["load"]) or 0.0)
            s.max_load = max(s.max_load, load)
            if load >= thr:
                s.seconds_high += dt
            if r.get("released") == "1":
                s.seconds_released += dt
            temp = _f(r.get("temp"))
            if temp is not None:
                s.max_temp = temp if s.max_temp is None else max(s.max_temp, temp)
            for k in filter(None, (r.get("event") or "").split("|")):
                s.events.append((t, k))
        motors[m] = s
    marks = list(dict.fromkeys(r.get("mark", "") for r in rows if r.get("mark")))
    return LogSummary(duration=(max(t_all) - min(t_all)) if t_all else 0.0,
                      mode=rows[0].get("mode", "") if rows else "", motors=motors, marks=marks)


# --- validation tests ---------------------------------------------------------------
# Each test runs in its own `mark`. Times are relative to the start of that phase.

@dataclass
class Verdict:
    test: str
    passed: bool
    reasons: list[str]
    status: str = ""     # "" | "hardware": a joint did not move at all | "inconclusive": the test never got going

    @property
    def label(self) -> str:
        if self.passed:
            return "PASS"
        return {"hardware": "CHECK HARDWARE", "inconclusive": "INCONCLUSIVE"}.get(self.status, "FAIL")


def _ranges(rows: list[dict], motor: str) -> tuple[float, float]:
    """(commanded range, present range) of one motor in these rows."""
    goal = [v for r in rows if r["motor"] == motor and (v := _f(r.get("goal"))) is not None and v == v]
    pres = [v for r in rows if r["motor"] == motor and (v := _f(r.get("present"))) is not None and v == v]
    span = lambda xs: (max(xs) - min(xs)) if xs else 0.0
    return span(goal), span(pres)


def stuck_motors(rows: list[dict], min_command: float = 10.0, max_motion: float = 1.0) -> dict[str, tuple[float, float]]:
    """Motors commanded over a wide range that did not move: jammed, or a stuck position sensor."""
    out = {}
    for m in dict.fromkeys(r["motor"] for r in rows):
        cmd, moved = _ranges(rows, m)
        if cmd >= min_command and moved < max_motion:
            out[m] = (cmd, moved)
    return out


def judge(test: str, rows: list[dict], config: GuardConfig | None = None) -> Verdict:
    s = summarize(rows, config)
    t0 = min((_f(r["t"]) for r in rows), default=0.0) or 0.0
    reasons: list[str] = []

    def rel(t):
        return None if t is None else t - t0

    if test == "gripper_squeeze":
        g = s.motors.get("gripper")
        first = rel(g.first(INTERVENE)) if g else None
        ok = first is not None and first <= 6.0
        reasons.append(f"gripper intervention at {first:.1f} s" if first is not None else "no gripper intervention")
        cfg = config or GuardConfig()
        thr = cfg.param("gripper", "load_threshold")
        if first is None:
            # Did the squeeze ever load the gripper? Look at the second half of the phase, once it settled.
            grip = [r for r in rows if r["motor"] == "gripper"]
            tail = grip[len(grip) // 2:]
            loads = sorted(abs(_f(r["load"]) or 0.0) for r in tail)
            gaps = sorted(abs((_f(r["goal"]) or 0.0) - (_f(r["present"]) or 0.0)) for r in tail)
            if loads:
                load90, gap = loads[int(0.9 * (len(loads) - 1))], gaps[len(gaps) // 2]
                gthr = cfg.param("gripper", "gap_threshold")
                if load90 < thr:
                    reasons.append(f"the object never loaded the gripper enough to count as blocked: it settled at "
                                   f"{load90:.0f}% load and a {gap:.1f}% gap (guard thresholds {thr:.0f}% and {gthr:.0f}%). "
                                   f"Repeat with a rigid object, such as a wooden block")
                    return Verdict(test, False, reasons, "inconclusive")
                reasons.append(f"the gripper stayed at {load90:.0f}% load with a {gap:.1f}% gap "
                               f"(gap threshold {gthr:.0f}%), and the guard did not step in")
            return Verdict(test, False, reasons)
        # after the guard steps in, the load should stay below its threshold
        after = [abs(_f(r["load"]) or 0) for r in rows
                 if r["motor"] == "gripper" and first is not None and (_f(r["t"]) - t0) > first + 1.0]
        if after:
            hi = sorted(after)[int(0.9 * (len(after) - 1))]
            reasons.append(f"90th-percentile gripper load after intervention {hi:.0f}% (threshold {thr:.0f}%)")
            ok = ok and hi < thr
        return Verdict(test, ok, reasons)

    if test == "wrist_block":
        w = s.motors.get("wrist_flex")
        first = rel(w.first(INTERVENE)) if w else None
        heavy = w.count({"heavy_hold"}) if w else 0
        ok = first is not None and first <= 6.0
        reasons.append(f"wrist_flex back-off at {first:.1f} s" if first is not None else "no wrist_flex back-off")
        if heavy:
            reasons.append(f"{heavy} heavy-hold verdict(s): the block was mistaken for weight")
        return Verdict(test, ok and heavy == 0, reasons)

    if test in ("hold_pose", "sweep"):
        stuck = stuck_motors(rows)
        n = sum(m.count(INTERVENE) for name, m in s.motors.items() if name not in stuck)
        heavy = sum(m.count({"heavy_hold"}) for m in s.motors.values())
        for name, (cmd, moved) in stuck.items():
            reasons.append(f"{name} did not move at all (moved {moved:.1f} while commanded over {cmd:.0f}): it is jammed "
                           f"or its position sensor is stuck. Turn it by hand with torque off. The guard stepping in on "
                           f"it ({s.motors[name].count(INTERVENE)}x) is correct")
        reasons.append(f"{n} intervention(s) where none should happen" + (" on the other joints" if stuck else ""))
        if heavy:
            reasons.append(f"{heavy} heavy-hold check(s) (allowed: the guard correctly stepped back out)")
        if stuck:
            return Verdict(test, False, reasons, "hardware")
        return Verdict(test, n == 0, reasons)

    raise ValueError(f"unknown test: {test}")


def render(s: LogSummary) -> str:
    lines = [f"duration {s.duration:.1f} s · mode {s.mode or '?'}" + (f" · phases: {', '.join(s.marks)}" if s.marks else "")]
    lines.append(f"{'motor':14s} {'max load':>8s} {'s high':>7s} {'s eased':>8s} {'max °C':>7s}  events")
    for m in s.motors.values():
        ev = ", ".join(f"{k}@{t:.1f}s" for t, k in m.events if k not in ("hot", "cooled")) or "-"
        lines.append(f"{m.motor:14s} {m.max_load:7.0f}% {m.seconds_high:7.1f} {m.seconds_released:8.1f} "
                     f"{'-' if m.max_temp is None else f'{m.max_temp:.0f}':>7s}  {ev}")
    return "\n".join(lines)
