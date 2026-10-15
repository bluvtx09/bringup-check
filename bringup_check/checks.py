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

from .registers import (
    MODEL_NAMES, POSITION_RESOLUTION, SNAPSHOT_REGS, decode_status, load_percent,
    milliamps, volts,
)

PROBLEM, WARN, INFO = "문제", "주의", "정보"
LEVEL_ORDER = {PROBLEM: 0, WARN: 1, INFO: 2}

SO101_NAMES = {1: "shoulder_pan", 2: "shoulder_lift", 3: "elbow_flex",
               4: "wrist_flex", 5: "wrist_roll", 6: "gripper"}

STATUS_FIX = {
    "voltage": "전원 전압을 확인하세요(어댑터 정격, 배선 굵기, 커넥터 접촉).",
    "sensor": "엔코더 이상일 수 있습니다. 전원을 껐다 켠 뒤에도 남으면 서보 교체를 고려하세요.",
    "temperature": "전원을 끄고 식히세요. 관절이 무게를 계속 버티거나 걸려 있지 않은지 보세요.",
    "current": "관절이 막혀 있거나 무언가를 세게 누르고 있는지 확인하세요.",
    "angle": "위치 한계 설정과 명령한 목표 위치를 확인하세요.",
    "overload": "관절이 막혔거나, 그리퍼가 물체를 쥔 채 계속 닫히려 하거나, 한계 밖으로 명령했을 수 있습니다.",
}


@dataclass
class Finding:
    level: str
    code: str
    title: str
    detail: str
    fix: str
    motors: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


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


def _label(i: int, names: dict[int, str]) -> str:
    return f"{i}번 모터({names[i]})" if i in names else f"{i}번 모터"


# --- rules -------------------------------------------------------------------

def check_bus(s: Snapshot, names, expected_model: int | None = 777) -> list[Finding]:
    out: list[Finding] = []
    if not s.responding:
        out.append(Finding(PROBLEM, "bus.none", "모터가 하나도 응답하지 않습니다",
            "USB는 열렸지만 버스에서 어떤 모터도 대답하지 않았습니다.",
            "① 모터 전원 어댑터가 켜져 있는지 ② 컨트롤러 보드와 첫 모터 사이 케이블 ③ 통신 속도(기본 1Mbps) "
            "④ Waveshare 보드라면 점퍼가 B(USB) 쪽인지 확인하세요.", list(s.expected)))
        return out
    missing = s.missing
    if missing:
        order = s.expected
        first_missing = order.index(missing[0])
        tail = order[first_missing:]
        if missing == tail and first_missing > 0:
            prev, nxt = order[first_missing - 1], order[first_missing]
            out.append(Finding(PROBLEM, "bus.chain_break",
                f"{_label(nxt, names)}부터 끝까지 응답이 없습니다",
                f"응답한 모터: {sorted(s.responding)} / 응답 없음: {missing}. 데이지 체인이 한 지점에서 끊긴 모양입니다.",
                f"{_label(prev, names)}와 {_label(nxt, names)} 사이 케이블과 커넥터를 다시 꽂거나 교체해 보세요.",
                missing))
        else:
            for m in missing:
                out.append(Finding(PROBLEM, "bus.missing_one", f"{_label(m, names)}만 응답하지 않습니다",
                    "그 뒤 모터는 응답하므로 체인은 이어져 있습니다.",
                    "모터 ID 설정 단계에서 이 모터에 ID가 제대로 들어갔는지 확인하세요. 맞다면 이 서보의 고장을 의심하세요.",
                    [m]))
    if expected_model is not None:
        odd = [i for i, m in s.responding.items() if m != expected_model]
        if odd:
            got = ", ".join(f"{i}번={MODEL_NAMES.get(s.responding[i], s.responding[i])}" for i in odd)
            out.append(Finding(WARN, "bus.model", "예상과 다른 서보 모델이 섞여 있습니다", got,
                "부품 목록과 실제 장착된 서보를 비교하세요.", odd))
    return out


def check_voltage(s: Snapshot, names, servo_rating: float | None = None) -> list[Finding]:
    out: list[Finding] = []
    v = {i: volts(r["Present_Voltage"]) for i, r in s.regs.items() if r.get("Present_Voltage") is not None}
    if not v:
        return out
    med = statistics.median(v.values())
    out.append(Finding(INFO, "volt.supply", f"전원 전압 약 {med:.1f}V",
        "모터들이 보고한 전압의 중앙값입니다.", "", sorted(v)))
    for i, x in v.items():
        hi, lo = s.get(i, "Max_Voltage_Limit"), s.get(i, "Min_Voltage_Limit")
        if hi and x > volts(hi):
            out.append(Finding(PROBLEM, "volt.over", f"{_label(i, names)} 과전압 {x:.1f}V (보호 상한 {volts(hi):.1f}V)",
                "서보가 과전압 보호에 걸려 응답을 멈출 수 있습니다.",
                "어댑터 출력 전압을 확인하고, 서보 정격에 맞는 전원을 쓰세요.", [i]))
        elif lo and x < volts(lo):
            out.append(Finding(PROBLEM, "volt.under", f"{_label(i, names)} 저전압 {x:.1f}V (보호 하한 {volts(lo):.1f}V)",
                "전원이 약하거나 커넥터 접촉이 나쁩니다.", "전원 어댑터와 배선을 확인하세요.", [i]))
    if servo_rating is not None:
        if med > servo_rating * 1.15:
            out.append(Finding(PROBLEM, "volt.rating_high",
                f"{servo_rating:g}V용 서보에 {med:.1f}V가 들어오고 있습니다",
                "정격보다 높은 전압은 서보 기판을 태울 수 있습니다.",
                "서보 정격과 어댑터를 다시 확인하세요(7.4V용과 12V용 STS3215가 따로 있습니다).", sorted(v)))
        elif med < servo_rating * 0.6:
            out.append(Finding(WARN, "volt.rating_low",
                f"{servo_rating:g}V용 서보에 {med:.1f}V만 들어오고 있습니다",
                "전압이 낮으면 낼 수 있는 힘이 크게 줄어 무게를 못 버틸 수 있습니다.",
                "서보 정격에 맞는 어댑터인지 확인하세요.", sorted(v)))
    ordered = [i for i in s.expected if i in v]
    if len(ordered) >= 2:
        drop = v[ordered[0]] - v[ordered[-1]]
        spread = max(v.values()) - min(v.values())
        if drop >= 0.5:
            out.append(Finding(WARN, "volt.chain_drop",
                f"체인 뒤쪽으로 갈수록 전압이 {drop:.1f}V 떨어집니다",
                " → ".join(f"{i}:{v[i]:.1f}V" for i in ordered),
                "중간 커넥터가 헐겁거나 케이블이 가늘 수 있습니다. 전압이 크게 떨어지는 구간의 케이블을 다시 꽂거나 교체하세요.",
                ordered))
        elif spread >= 0.5:
            low = min(v, key=v.get)
            out.append(Finding(WARN, "volt.spread", f"{_label(low, names)}의 전압만 낮습니다 ({v[low]:.1f}V)",
                f"모터 간 전압 차이 {spread:.1f}V.", "이 모터로 들어가는 커넥터 접촉을 확인하세요.", [low]))
    return out


def check_temperature(s: Snapshot, names) -> list[Finding]:
    out: list[Finding] = []
    t = {i: r["Present_Temperature"] for i, r in s.regs.items() if r.get("Present_Temperature") is not None}
    if not t:
        return out
    med = statistics.median(t.values())
    for i, x in t.items():
        lim = s.get(i, "Max_Temperature_Limit") or 70
        if x >= lim - 5:
            out.append(Finding(PROBLEM, "temp.near_limit", f"{_label(i, names)} {x}°C — 과열 보호 직전 (한계 {lim}°C)",
                "곧 힘이 빠지거나 보호 모드로 들어갑니다.", STATUS_FIX["temperature"], [i]))
        elif x >= 60:
            out.append(Finding(WARN, "temp.hot", f"{_label(i, names)} {x}°C", "오래 유지되면 수명이 줄어듭니다.",
                STATUS_FIX["temperature"], [i]))
        elif len(t) >= 3 and x - med >= 10:
            out.append(Finding(WARN, "temp.outlier", f"{_label(i, names)}만 다른 모터보다 {x - med:.0f}°C 뜨겁습니다",
                f"이 모터 {x}°C, 나머지 중앙값 {med:.0f}°C.",
                "이 관절이 무게를 혼자 버티거나 걸려 있는지 보세요. 그런 이유가 없다면 서보 개체 불량을 의심하고, "
                "다른 관절의 서보와 바꿔 끼워 열이 따라가는지 비교하세요.", [i]))
    return out


def check_status(s: Snapshot, names) -> list[Finding]:
    out: list[Finding] = []
    for i, r in s.regs.items():
        raw = r.get("Status") or 0
        bits = decode_status(raw)
        if bits:
            out.append(Finding(PROBLEM, "status.flags",
                f"{_label(i, names)}가 오류를 보고했습니다: {', '.join(b[1] for b in bits)}",
                f"상태 레지스터 값 {raw:#04x}.", " ".join(STATUS_FIX[b[0]] for b in bits), [i]))
    return out


def check_limits(s: Snapshot, names, wheel_ids: set[int] = frozenset()) -> list[Finding]:
    out: list[Finding] = []
    for i, r in s.regs.items():
        lo, hi, mode, pos = (r.get("Min_Position_Limit"), r.get("Max_Position_Limit"),
                             r.get("Operating_Mode"), r.get("Present_Position"))
        if lo is None or hi is None:
            continue
        if i in wheel_ids:
            continue
        if lo == 0 and hi == 0:
            out.append(Finding(WARN, "limit.wheel_mode", f"{_label(i, names)}가 연속회전(바퀴) 모드로 설정돼 있습니다",
                "위치 한계가 0~0이면 관절이 아니라 바퀴처럼 돕니다.",
                "로봇팔 관절이라면 위치 한계를 되돌리세요(예: 0~4095) 또는 LeRobot 모터 설정을 다시 실행하세요.", [i]))
            continue
        if lo >= hi:
            out.append(Finding(PROBLEM, "limit.invalid", f"{_label(i, names)} 위치 한계가 잘못됐습니다 ({lo} ≥ {hi})",
                "이 상태에서는 관절이 거의 움직이지 못하고 힘만 쓸 수 있습니다.",
                "모터 설정 단계를 다시 실행해 한계를 바로잡으세요.", [i]))
            continue
        if mode not in (None, 0):
            out.append(Finding(WARN, "limit.mode", f"{_label(i, names)} 동작 모드가 위치 모드(0)가 아닙니다 (모드 {mode})",
                "관절이 위치 명령을 따르지 않을 수 있습니다.", "동작 모드를 0으로 되돌리세요.", [i]))
        if hi - lo < POSITION_RESOLUTION * 0.05:
            out.append(Finding(WARN, "limit.narrow", f"{_label(i, names)} 허용 범위가 매우 좁습니다 ({lo}~{hi})",
                "명령이 이 범위 밖이면 관절이 멈춰 버립니다.", "의도한 설정인지 확인하세요.", [i]))
        if pos is not None and not (lo <= pos <= hi):
            out.append(Finding(PROBLEM, "limit.outside", f"{_label(i, names)} 현재 위치({pos})가 허용 범위({lo}~{hi}) 밖입니다",
                "범위 안으로 들어가려 하거나, 그 방향으로 움직이지 않을 수 있습니다.",
                "전원을 끈 상태에서 관절을 범위 안으로 옮기고, 혼이 미끄러지지 않았는지 확인하세요.", [i]))
    return out


def check_calibration(s: Snapshot, names, calib: dict | None, margin: int = 100) -> list[Finding]:
    """`calib` is a LeRobot calibration JSON: {motor_name: {id, range_min, range_max, ...}}."""
    out: list[Finding] = []
    if not calib:
        return out
    for name, c in calib.items():
        i = c.get("id")
        if i not in s.regs:
            continue
        lo, hi = c.get("range_min"), c.get("range_max")
        pos = s.get(i, "Present_Position")
        lab = f"{i}번 모터({name})"
        if lo is None or hi is None:
            continue
        if lo >= hi:
            out.append(Finding(PROBLEM, "calib.invalid", f"{lab} 캘리브레이션 범위가 잘못됐습니다 ({lo} ≥ {hi})",
                "", "이 관절을 다시 캘리브레이션하세요.", [i]))
            continue
        if pos is None:
            continue
        if not (lo - margin <= pos <= hi + margin):
            out.append(Finding(WARN, "calib.outside", f"{lab} 현재 위치({pos})가 캘리브레이션 범위({lo}~{hi}) 밖입니다",
                "캘리브레이션 뒤에 혼이 빠졌거나 다시 조립됐을 수 있습니다.",
                "혼과 나사 체결을 확인하고, 조립을 바꿨다면 다시 캘리브레이션하세요.", [i]))
        if pos < margin or pos > POSITION_RESOLUTION - 1 - margin:
            out.append(Finding(WARN, "calib.wrap", f"{lab} 현재 위치({pos})가 엔코더 경계(0/4095) 근처입니다",
                "이 근처에서는 값이 반대쪽 끝으로 튀어 관절이 급하게 움직이거나 하드스톱에 부딪힐 수 있습니다.",
                "관절을 범위 중간(약 2048) 근처에 두고 다시 캘리브레이션하세요.", [i]))
    return out


def check_load_at_rest(s: Snapshot, names, gripper_ids: set[int] = frozenset({6})) -> list[Finding]:
    out: list[Finding] = []
    for i, r in s.regs.items():
        if not r.get("Torque_Enable") or r.get("Moving"):
            continue
        load = load_percent(r["Present_Load"]) if r.get("Present_Load") is not None else 0.0
        cur = milliamps(r["Present_Current"]) if r.get("Present_Current") is not None else 0.0
        if abs(load) >= 50 or cur >= 500:
            if i in gripper_ids:
                out.append(Finding(WARN, "load.gripper", f"{_label(i, names)}가 멈춘 채 계속 힘을 쓰고 있습니다 (부하 {abs(load):.0f}%, {cur:.0f}mA)",
                    "물체를 쥔 채 계속 닫히려 하면 서보가 과부하로 망가질 수 있습니다(실제 사례 있음).",
                    "그리퍼를 열거나, 쥐는 목표 위치를 물체 크기에 맞춰 덜 닫히게 하세요.", [i]))
            else:
                out.append(Finding(WARN, "load.joint", f"{_label(i, names)}가 멈춘 상태에서 부하 {abs(load):.0f}% ({cur:.0f}mA)",
                    "관절이 한계나 물체에 눌려 있거나, 무게를 혼자 오래 버티고 있습니다.",
                    "자세를 바꿔 하중을 줄이거나, 걸리는 곳이 없는지 확인하세요.", [i]))
    return out


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
    return _tidy(findings, names)


MERGEABLE = {"volt.over", "volt.under"}


def _tidy(findings: list[Finding], names) -> list[Finding]:
    """Merge per-motor duplicates of one supply-wide cause, and drop a 'hot outlier'
    guess when the same motor already has an explained load/overload finding."""
    explained = {m for f in findings if f.code.startswith("load.") or f.code == "status.flags" for m in f.motors}
    kept = [f for f in findings if not (f.code == "temp.outlier" and set(f.motors) <= explained)]
    out: list[Finding] = []
    for code in MERGEABLE:
        group = [f for f in kept if f.code == code]
        if len(group) >= 2:
            first = group[0]
            motors = sorted(m for f in group for m in f.motors)
            what = "과전압" if code == "volt.over" else "저전압"
            reading = first.title.split(f"{what} ", 1)[-1]  # e.g. "7.6V (보호 상한 7.0V)"
            out.append(Finding(first.level, code, f"모터 {len(motors)}개 모두 {what}: {reading}",
                               first.detail + " 모터 하나가 아니라 전원 쪽 문제로 보입니다.", first.fix, motors))
            kept = [f for f in kept if f.code != code]
    return sorted(kept + out, key=lambda f: LEVEL_ORDER[f.level])


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
            out.append(Finding(PROBLEM if sag >= 0.15 * v0 else WARN, "hold.sag",
                f"모터들이 힘을 쓰자 전압이 {v0:.1f}V → {v1:.1f}V로 떨어졌습니다",
                "전원 어댑터 용량이 부족하거나 배선이 가늘어 여러 모터를 동시에 못 버팁니다. 동작 중 끊김·떨림의 흔한 원인입니다.",
                "더 높은 전류(A)를 내는 어댑터를 쓰고, 브레드보드를 거치지 말고 굵은 선으로 직접 연결하세요.", ids))
    cur = {i: statistics.median([milliamps(x["Present_Current"]) for x in samples[i] if x["Present_Current"] is not None] or [0])
           for i in ids}
    total = sum(cur.values())
    if len(cur) >= 3:
        med = statistics.median(cur.values())
        for i, c in cur.items():
            if c >= 300 and c >= 2 * max(med, 1):
                out.append(Finding(WARN, "hold.current_outlier",
                    f"{_label(i, names)}만 자세를 유지하는 데 {c:.0f}mA를 씁니다 (다른 모터 중앙값 {med:.0f}mA)",
                    "이 관절이 무게를 혼자 버티거나, 조립이 뻑뻑하거나, 무언가에 눌려 있습니다.",
                    "그 관절을 손으로 (전원 끈 상태에서) 움직여 걸리는 곳이 있는지 보세요.", [i]))
    for i in ids:
        temps = [x["Present_Temperature"] for x in samples[i] if x["Present_Temperature"] is not None]
        if len(temps) >= 2 and temps[-1] - temps[0] >= 5:
            out.append(Finding(WARN, "hold.heating", f"{_label(i, names)}가 {seconds:.0f}초 만에 {temps[-1] - temps[0]}°C 올랐습니다",
                "가만히 버티기만 해도 빠르게 뜨거워집니다.", STATUS_FIX["temperature"], [i]))
        flags = {b for x in samples[i] for b in decode_status(x["Status"] or 0)}
        if flags:
            out.append(Finding(PROBLEM, "hold.status", f"{_label(i, names)}가 버티는 중에 오류를 냈습니다: {', '.join(b[1] for b in flags)}",
                "", " ".join(STATUS_FIX[b[0]] for b in flags), [i]))
    data = {"seconds": seconds, "hz": hz, "median_current_mA": cur, "total_current_mA": total, "samples": samples}
    return sorted(out, key=lambda f: LEVEL_ORDER[f.level]), data
