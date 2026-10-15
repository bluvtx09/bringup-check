"""Demo scenarios that imitate real setup incidents found in public reports.

Values are illustrative register readings, not recordings from those robots.
Each scenario lists the incident it imitates and the finding codes the
diagnosis should produce.
"""

from __future__ import annotations

from copy import deepcopy

from .bus import FakeBus

_BASE = {
    "Min_Position_Limit": 0, "Max_Position_Limit": 4095, "Max_Temperature_Limit": 70,
    "Max_Voltage_Limit": 140, "Min_Voltage_Limit": 40, "Operating_Mode": 0,
    "Torque_Enable": 0, "Present_Load": 0, "Present_Voltage": 50, "Status": 0,
    "Moving": 0, "Present_Current": 0,
}
_POS = {1: 2040, 2: 1180, 3: 2950, 4: 2300, 5: 2048, 6: 2100}
_TEMP = {1: 31, 2: 32, 3: 31, 4: 30, 5: 30, 6: 31}


def healthy_motors() -> dict[int, dict[str, int]]:
    return {i: {**_BASE, "Present_Position": _POS[i], "Present_Temperature": _TEMP[i]} for i in range(1, 7)}


def _calib(motors=None) -> dict:
    names = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
    ranges = {1: (750, 3350), 2: (850, 3200), 3: (900, 3100), 4: (850, 3150), 5: (0, 4095), 6: (2000, 3500)}
    return {n: {"id": i, "drive_mode": 0, "homing_offset": 0, "range_min": ranges[i][0], "range_max": ranges[i][1]}
            for i, n in zip(range(1, 7), names)}


def _mk(name, title, case, motors, expect, hold=None, rating=None, calib=None, hold_test=False):
    return {"name": name, "title": title, "case": case, "motors": motors, "expect": expect,
            "hold_effects": hold or {}, "servo_rating": rating, "calib": calib, "hold_test": hold_test}


def build() -> dict[str, dict]:
    sc = {}

    m = healthy_motors()
    sc["healthy"] = _mk("healthy", "정상 SO-101", "기준선", m, [], rating=7.4, calib=_calib(), hold_test=True,
                        hold={i: {"Present_Voltage": 49, "Present_Current": 15} for i in m})

    m = healthy_motors()
    for i in (4, 5, 6):
        del m[i]
    sc["chain_break"] = _mk("chain_break", "체인 중간 단선",
        "LeRobot #3131 등 — 여러 관절이 움직일 때 팔로워 연결이 끊김", m, ["bus.chain_break"])

    m = healthy_motors()
    del m[3]
    sc["missing_one"] = _mk("missing_one", "한 모터만 무응답", "LeRobot #605·Pollen 913 — 특정 모터만 버스에서 안 보임",
                            m, ["bus.missing_one"])

    m = healthy_motors()
    sc["weak_supply"] = _mk("weak_supply", "부하가 걸리면 전원이 주저앉음",
        "Arduino 포럼 634588·889760 — 서보를 보드 5V나 약한 어댑터로 구동", m, ["hold.sag"],
        hold={i: {"Present_Voltage": 41, "Present_Current": 60} for i in m}, rating=7.4, hold_test=True)

    m = healthy_motors()
    for r in m.values():
        r.update(Present_Voltage=76, Max_Voltage_Limit=70)
    sc["overvoltage"] = _mk("overvoltage", "과전압 보호에 걸림", "Reachy Mini #589 — 모터 상한 7.0V에 7.59V 공급",
                            m, ["volt.over"])

    m = healthy_motors()
    for r in m.values():
        r.update(Present_Voltage=121)
    sc["wrong_supply"] = _mk("wrong_supply", "7.4V 서보에 12V 어댑터", "Arduino 포럼 907110 — 정격 초과 전압으로 서보 연기",
                             m, ["volt.rating_high"], rating=7.4)

    m = healthy_motors()
    for i, v in zip(range(1, 7), (50, 49, 48, 47, 45, 44)):
        m[i]["Present_Voltage"] = v
    sc["chain_drop"] = _mk("chain_drop", "체인 뒤로 갈수록 전압 하락", "헐거운 커넥터·가는 케이블 (통신·전원 범주 다수)",
                           m, ["volt.chain_drop"])

    m = healthy_motors()
    m[3]["Present_Temperature"] = 45
    sc["hot_outlier"] = _mk("hot_outlier", "한 서보만 유독 뜨거움", "ROBOTIS 포럼 6064 — 같은 전류인데 한 모터만 10°C 이상 뜨거움",
                            m, ["temp.outlier"])

    m = healthy_motors()
    m[4].update(Min_Position_Limit=2100, Max_Position_Limit=2100)
    sc["bad_limits"] = _mk("bad_limits", "위치 한계 설정 오류", "Pollen 포럼 82 — 모터 내부 한계가 잘못 들어가 -20°에서 멈춤",
                           m, ["limit.invalid"])

    m = healthy_motors()
    m[5]["Present_Position"] = 4060
    m[3]["Present_Position"] = 450
    sc["calibration"] = _mk("calibration", "캘리브레이션 경계·범위 이탈",
        "LeRobot #3193(손목 회전 0/4095 경계) · #1026(혼 어긋남)", m, ["calib.wrap", "calib.outside"], calib=_calib())

    m = healthy_motors()
    m[6].update(Torque_Enable=1, Present_Load=850, Present_Current=120, Present_Temperature=52, Status=0b100000)
    sc["gripper_squeeze"] = _mk("gripper_squeeze", "물체를 쥔 채 계속 닫히는 그리퍼",
        "LeRobot #2819 — 물체를 쥔 채 리더 그리퍼를 끝까지 닫아 서보 파손", m, ["load.gripper", "status.flags"])

    return sc


def make_bus(scenario: dict) -> FakeBus:
    return FakeBus(deepcopy(scenario["motors"]), deepcopy(scenario["hold_effects"]))
