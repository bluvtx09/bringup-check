"""Diagnostic rules. Each rule turns raw register readings into plain-language findings.

The rules were written against real setup incidents collected from public
issues and forums (wiring breaks, weak or wrong supplies, a single hot servo,
bad position limits, encoder wrap-around after calibration, a gripper left
squeezing an object). Thresholds are deliberately conservative starting points,
not validated constants; tune them with real logs.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

from .messages import has, t
from .registers import (
    MODEL_NAMES, POSITION_RESOLUTION, SNAPSHOT_REGS, decode_status, load_percent,
    milliamps, volts,
)

PROBLEM, WARN, INFO = "problem", "warn", "info"
LEVEL_ORDER = {PROBLEM: 0, WARN: 1, INFO: 2}

SO101_NAMES = {1: "shoulder_pan", 2: "shoulder_lift", 3: "elbow_flex",
               4: "wrist_flex", 5: "wrist_roll", 6: "gripper"}


@dataclass
class Finding:
    level: str
    code: str
    title: str
    detail: str
    fix: str
    motors: list[int] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def make(level: str, code: str, motors, detail: str | None = None, fix: str | None = None, **kw) -> Finding:
    """Build a finding from the message catalog (`<code>.title/.detail/.fix`)."""
    title = t(f"{code}.title", **kw)
    if detail is None:
        detail = t(f"{code}.detail", **kw) if has(f"{code}.detail") else ""
    if fix is None:
        fix = t(f"{code}.fix", **kw) if has(f"{code}.fix") else ""
    return Finding(level, code, title, detail, fix, list(motors), dict(kw))


def bit_names(bits: list[str]) -> str:
    return ", ".join(t(f"bit.{b}") for b in bits)


def bit_fixes(bits: list[str]) -> str:
    return " ".join(t(f"fix.{b}") for b in bits)


@dataclass
class Snapshot:
    expected: list[int]
    responding: dict[int, int]
    regs: dict[int, dict[str, int | None]]
    protocol_errors: dict[int, int] = field(default_factory=dict)

    @property
    def missing(self) -> list[int]:
        return [i for i in self.expected if i not in self.responding]

    def get(self, motor_id: int, name: str) -> int | None:
        return self.regs.get(motor_id, {}).get(name)

    def to_dict(self) -> dict:
        return {"expected": self.expected, "responding": self.responding,
                "missing": self.missing, "regs": self.regs,
                "protocol_errors": self.protocol_errors}


def take_snapshot(bus, ids: list[int]) -> Snapshot:
    responding, regs = {}, {}
    for i in ids:
        model = bus.ping(i)
        if model is None:
            continue
        responding[i] = model
        regs[i] = {name: bus.read(i, name) for name in SNAPSHOT_REGS}
    return Snapshot(list(ids), responding, regs, dict(getattr(bus, "last_error", {}) or {}))


def label(i: int, names: dict[int, str]) -> str:
    return t("label.named", i=i, name=names[i]) if i in names else t("label.plain", i=i)


# --- rules -------------------------------------------------------------------

def check_bus(s: Snapshot, names, expected_model: int | None = 777) -> list[Finding]:
    out: list[Finding] = []
    if not s.responding:
        return [make(PROBLEM, "bus.none", s.expected)]
    missing = s.missing
    if missing:
        order = s.expected
        first = order.index(missing[0])
        if missing == order[first:] and first > 0:
            prev, nxt = order[first - 1], order[first]
            out.append(make(PROBLEM, "bus.chain_break", missing, prev=label(prev, names), nxt=label(nxt, names),
                            ok=sorted(s.responding), missing=missing))
        else:
            for m in missing:
                out.append(make(PROBLEM, "bus.missing_one", [m], m=label(m, names)))
    if expected_model is not None:
        odd = [i for i, m in s.responding.items() if m != expected_model]
        if odd:
            got = ", ".join(f"{label(i, names)}={MODEL_NAMES.get(s.responding[i], s.responding[i])}" for i in odd)
            out.append(make(WARN, "bus.model", odd, detail=got))
    return out


def check_voltage(s: Snapshot, names, servo_rating: float | None = None) -> list[Finding]:
    out: list[Finding] = []
    v = {i: volts(r["Present_Voltage"]) for i, r in s.regs.items() if r.get("Present_Voltage") is not None}
    if not v:
        return out
    med = statistics.median(v.values())
    out.append(make(INFO, "volt.supply", sorted(v), v=med))
    for i, x in v.items():
        hi, lo = s.get(i, "Max_Voltage_Limit"), s.get(i, "Min_Voltage_Limit")
        if hi and x > volts(hi):
            out.append(make(PROBLEM, "volt.over", [i], m=label(i, names), v=x, lim=volts(hi)))
        elif lo and x < volts(lo):
            out.append(make(PROBLEM, "volt.under", [i], m=label(i, names), v=x, lim=volts(lo)))
    if servo_rating is not None:
        if med > servo_rating * 1.15:
            out.append(make(PROBLEM, "volt.rating_high", sorted(v), r=servo_rating, v=med))
        elif med < servo_rating * 0.6:
            out.append(make(WARN, "volt.rating_low", sorted(v), r=servo_rating, v=med))
    ordered = [i for i in s.expected if i in v]
    if len(ordered) >= 2:
        drop = v[ordered[0]] - v[ordered[-1]]
        spread = max(v.values()) - min(v.values())
        if drop >= 0.5:
            out.append(make(WARN, "volt.chain_drop", ordered,
                            detail=" → ".join(f"{i}:{v[i]:.1f}V" for i in ordered), d=drop))
        elif spread >= 0.5:
            low = min(v, key=v.get)
            out.append(make(WARN, "volt.spread", [low], m=label(low, names), v=v[low], d=spread))
    return out


def check_temperature(s: Snapshot, names) -> list[Finding]:
    out: list[Finding] = []
    temps = {i: r["Present_Temperature"] for i, r in s.regs.items() if r.get("Present_Temperature") is not None}
    if not temps:
        return out
    med = statistics.median(temps.values())
    for i, x in temps.items():
        lim = s.get(i, "Max_Temperature_Limit") or 70
        if x >= lim - 5:
            out.append(make(PROBLEM, "temp.near_limit", [i], fix=t("fix.temperature"), m=label(i, names), t=x, lim=lim))
        elif x >= 60:
            out.append(make(WARN, "temp.hot", [i], fix=t("fix.temperature"), m=label(i, names), t=x))
        elif len(temps) >= 3 and x - med >= 10:
            out.append(make(WARN, "temp.outlier", [i], m=label(i, names), d=x - med, t=x, med=med))
    return out


def check_status(s: Snapshot, names) -> list[Finding]:
    out: list[Finding] = []
    for i, r in s.regs.items():
        raw = r.get("Status") or 0
        bits = decode_status(raw)
        if bits:
            out.append(make(PROBLEM, "status.flags", [i], fix=bit_fixes(bits),
                            m=label(i, names), flags=bit_names(bits), raw=f"{raw:#04x}", bits=bits))
    return out


def check_limits(s: Snapshot, names, wheel_ids=frozenset()) -> list[Finding]:
    out: list[Finding] = []
    for i, r in s.regs.items():
        lo, hi, mode, pos = (r.get("Min_Position_Limit"), r.get("Max_Position_Limit"),
                             r.get("Operating_Mode"), r.get("Present_Position"))
        if lo is None or hi is None or i in wheel_ids:
            continue
        m = label(i, names)
        if lo == 0 and hi == 0:
            out.append(make(WARN, "limit.wheel_mode", [i], m=m))
            continue
        if lo >= hi:
            out.append(make(PROBLEM, "limit.invalid", [i], m=m, lo=lo, hi=hi))
            continue
        if mode not in (None, 0):
            out.append(make(WARN, "limit.mode", [i], m=m, mode=mode))
        if hi - lo < POSITION_RESOLUTION * 0.05:
            out.append(make(WARN, "limit.narrow", [i], m=m, lo=lo, hi=hi))
        if pos is not None and not (lo <= pos <= hi):
            out.append(make(PROBLEM, "limit.outside", [i], m=m, pos=pos, lo=lo, hi=hi))
    return out


def check_calibration(s: Snapshot, names, calib: dict | None, margin: int = 100) -> list[Finding]:
    """`calib` is a LeRobot calibration JSON: {motor_name: {id, range_min, range_max, ...}}."""
    out: list[Finding] = []
    if not calib:
        return out
    for name, c in calib.items():
        if not isinstance(c, dict):
            continue
        i = c.get("id")
        if i not in s.regs:
            continue
        lo, hi = c.get("range_min"), c.get("range_max")
        pos = s.get(i, "Present_Position")
        m = t("label.named", i=i, name=name)
        if lo is None or hi is None:
            continue
        if lo >= hi:
            out.append(make(PROBLEM, "calib.invalid", [i], m=m, lo=lo, hi=hi))
            continue
        if pos is None:
            continue
        if not (lo - margin <= pos <= hi + margin):
            out.append(make(WARN, "calib.outside", [i], m=m, pos=pos, lo=lo, hi=hi))
        if pos < margin or pos > POSITION_RESOLUTION - 1 - margin:
            out.append(make(WARN, "calib.wrap", [i], m=m, pos=pos))
    return out


def check_load_at_rest(s: Snapshot, names, gripper_ids=frozenset({6})) -> list[Finding]:
    out: list[Finding] = []
    for i, r in s.regs.items():
        if not r.get("Torque_Enable") or r.get("Moving"):
            continue
        load = load_percent(r["Present_Load"]) if r.get("Present_Load") is not None else 0.0
        cur = milliamps(r["Present_Current"]) if r.get("Present_Current") is not None else 0.0
        if abs(load) >= 50 or cur >= 500:
            code = "load.gripper" if i in gripper_ids else "load.joint"
            out.append(make(WARN, code, [i], m=label(i, names), load=abs(load), cur=cur))
    return out


MERGEABLE = ("volt.over", "volt.under")


def tidy(findings: list[Finding]) -> list[Finding]:
    """Merge per-motor duplicates of one supply-wide cause, and drop a 'hot outlier'
    guess when the same motor already has an explained load/overload finding."""
    explained = {m for f in findings if f.code.startswith("load.") or f.code == "status.flags" for m in f.motors}
    kept = [f for f in findings if not (f.code == "temp.outlier" and set(f.motors) <= explained)]
    merged: list[Finding] = []
    for code in MERGEABLE:
        group = [f for f in kept if f.code == code]
        if len(group) >= 2:
            motors = sorted(m for f in group for m in f.motors)
            d = group[0].data
            merged.append(Finding(group[0].level, code,
                                  t(f"{code}.merged", n=len(motors), v=statistics.median(f.data["v"] for f in group), lim=d["lim"]),
                                  f"{group[0].detail} {t('volt.merged.detail')}", group[0].fix, motors,
                                  {"n": len(motors), "lim": d["lim"]}))
            kept = [f for f in kept if f.code != code]
    return sorted(kept + merged, key=lambda f: LEVEL_ORDER[f.level])


def run_all(s: Snapshot, names=None, calib=None, servo_rating=None, gripper_ids=frozenset({6}),
            wheel_ids=frozenset(), expected_model=777) -> list[Finding]:
    names = names or {}
    findings = check_bus(s, names, expected_model)
    findings += check_voltage(s, names, servo_rating)
    findings += check_temperature(s, names)
    findings += check_status(s, names)
    findings += check_limits(s, names, wheel_ids)
    findings += check_calibration(s, names, calib)
    findings += check_load_at_rest(s, names, gripper_ids)
    return tidy(findings)


# --- opt-in hold test (energizes motors, holds the current pose) ---------------

def run_hold_test(bus, s: Snapshot, names, seconds: float = 5.0, hz: float = 10.0,
                  sleep=time.sleep) -> tuple[list[Finding], dict]:
    ids = sorted(s.responding)
    before = {i: s.get(i, "Torque_Enable") or 0 for i in ids}
    samples: dict[int, list[dict]] = {i: [] for i in ids}
    try:
        for i in ids:  # hold where it is: goal = present, so nothing jumps
            pos = bus.read(i, "Present_Position")
            if pos is not None:
                bus.write(i, "Goal_Position", pos)
            bus.write(i, "Torque_Enable", 1)
        for _ in range(max(1, int(seconds * hz))):
            for i in ids:
                samples[i].append({k: bus.read(i, k) for k in
                                   ("Present_Voltage", "Present_Current", "Present_Temperature", "Present_Load", "Status")})
            sleep(1.0 / hz)
    finally:
        for i in ids:
            bus.write(i, "Torque_Enable", before[i])

    out: list[Finding] = []
    idle_v = [volts(s.get(i, "Present_Voltage")) for i in ids if s.get(i, "Present_Voltage") is not None]
    hold_v = [volts(x["Present_Voltage"]) for i in ids for x in samples[i] if x["Present_Voltage"] is not None]
    if idle_v and hold_v:
        v0, v1 = statistics.median(idle_v), min(hold_v)
        sag = v0 - v1
        if sag >= max(0.5, 0.08 * v0):
            out.append(make(PROBLEM if sag >= 0.15 * v0 else WARN, "hold.sag", ids, v0=v0, v1=v1))
    cur = {i: statistics.median([milliamps(x["Present_Current"]) for x in samples[i]
                                 if x["Present_Current"] is not None] or [0]) for i in ids}
    if len(cur) >= 3:
        med = statistics.median(cur.values())
        for i, c in cur.items():
            if c >= 300 and c >= 2 * max(med, 1):
                out.append(make(WARN, "hold.current_outlier", [i], m=label(i, names), c=c, med=med))
    for i in ids:
        temps = [x["Present_Temperature"] for x in samples[i] if x["Present_Temperature"] is not None]
        if len(temps) >= 2 and temps[-1] - temps[0] >= 5:
            out.append(make(WARN, "hold.heating", [i], fix=t("fix.temperature"),
                            m=label(i, names), s=seconds, d=temps[-1] - temps[0]))
        bits = sorted({b for x in samples[i] for b in decode_status(x["Status"] or 0)})
        if bits:
            out.append(make(PROBLEM, "hold.status", [i], detail="", fix=bit_fixes(bits),
                            m=label(i, names), flags=bit_names(bits)))
    data = {"seconds": seconds, "hz": hz, "median_current_mA": cur, "total_current_mA": sum(cur.values()),
            "samples": samples}
    return sorted(out, key=lambda f: LEVEL_ORDER[f.level]), data
