"""Command line entry point.

Real robot (read-only):
    python -m bringup_check --port /dev/ttyACM0
With a LeRobot calibration file and the opt-in hold test:
    python -m bringup_check --port /dev/ttyACM0 --calib ~/.cache/huggingface/lerobot/calibration/robots/so101_follower/my_arm.json --hold-test 5
Demo without hardware:
    python -m bringup_check --demo all
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .bus import BusError, FeetechBus
from .checks import PROBLEM, SO101_NAMES, run_all, run_hold_test, take_snapshot
from .report import render, to_json
from .scenarios import build, make_bus


def _parse_ids(text: str) -> list[int]:
    return [int(x) for x in text.split(",") if x.strip()]


def diagnose(bus, ids, names, calib=None, servo_rating=None, hold_seconds=0.0, title="", sleep=None,
             gripper_ids=frozenset({6})):
    snap = take_snapshot(bus, ids)
    findings = run_all(snap, names, calib, servo_rating, gripper_ids=gripper_ids)
    hold = None
    if hold_seconds and snap.responding:
        kw = {"sleep": sleep} if sleep else {}
        hold_findings, hold = run_hold_test(bus, snap, names, hold_seconds, **kw)
        findings = sorted(findings + hold_findings, key=lambda f: {"문제": 0, "주의": 1, "정보": 2}[f.level])
    return snap, findings, hold, render(snap, findings, names, title)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bringup-check", description="SO-101 등 Feetech 서보 로봇의 셋업 진단")
    p.add_argument("--port", help="컨트롤러 포트 (예: /dev/ttyACM0, COM3)")
    p.add_argument("--baud", type=int, default=1_000_000)
    p.add_argument("--ids", default="1,2,3,4,5,6", help="데이지 체인 순서대로 쓴 모터 ID (기본 1~6)")
    p.add_argument("--calib", help="LeRobot 캘리브레이션 JSON 경로 (선택)")
    p.add_argument("--servo-rating", type=float, help="서보 정격 전압 (7.4 또는 12). 넣으면 전원이 맞는지 검사")
    p.add_argument("--gripper-ids", default="6")
    p.add_argument("--hold-test", type=float, default=0.0, metavar="초",
                   help="모터에 힘을 넣고 현재 자세를 N초 유지하며 전원·전류를 검사 (모터가 켜집니다)")
    p.add_argument("--json", help="진단 결과를 JSON 파일로 저장")
    p.add_argument("--demo", help="하드웨어 없이 재현 시나리오 실행: all 또는 시나리오 이름")
    p.add_argument("--version", action="version", version=__version__)
    a = p.parse_args(argv)

    ids = _parse_ids(a.ids)
    names = SO101_NAMES if ids == [1, 2, 3, 4, 5, 6] else {}
    grippers = frozenset(_parse_ids(a.gripper_ids))

    if a.demo:
        scs = build()
        chosen = scs if a.demo == "all" else {a.demo: scs[a.demo]}
        for sc in chosen.values():
            _, _, _, text = diagnose(make_bus(sc), ids, names, sc["calib"], sc["servo_rating"],
                                     2.0 if sc["hold_test"] else 0.0, f"{sc['title']}  ({sc['case']})",
                                     sleep=lambda _: None)
            print(text)
        return 0

    if not a.port:
        p.error("--port 또는 --demo 중 하나가 필요합니다")
    calib = json.loads(Path(a.calib).expanduser().read_text()) if a.calib else None
    if a.hold_test:
        print("주의: 모터에 힘이 들어가 현재 자세를 유지합니다. 팔이 넘어지지 않게 받칠 준비를 하세요.")
        if input("계속하려면 y 입력: ").strip().lower() != "y":
            return 1
    try:
        bus = FeetechBus(a.port, a.baud)
    except BusError as e:
        print(f"[문제] {e}")
        return 2
    try:
        snap, findings, hold, text = diagnose(bus, ids, names, calib, a.servo_rating, a.hold_test,
                                              gripper_ids=grippers)
    finally:
        bus.close()
    print(text)
    if a.json:
        meta = {"tool": "bringup-check", "version": __version__,
                "time": datetime.now(timezone.utc).isoformat(), "port": a.port, "ids": ids}
        Path(a.json).write_text(to_json(snap, findings, hold, meta), encoding="utf-8")
        print(f"JSON 저장: {a.json}")
    return 1 if any(f.level == PROBLEM for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
