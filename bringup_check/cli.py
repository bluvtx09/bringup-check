"""Command line entry point.

Real robot (read-only):
    python -m bringup_check --port /dev/ttyACM0
Match the LeRobot calibration file automatically and run the opt-in hold test:
    python -m bringup_check --port /dev/ttyACM0 --calib auto --hold-test 5
Demo without hardware:
    python -m bringup_check --demo all
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .bus import BusError, FeetechBus
from .calib import candidates, load_calibration, pick
from .checks import LEVEL_ORDER, PROBLEM, SO101_NAMES, run_all, run_hold_test, take_snapshot
from .messages import get_lang, set_lang, t
from .report import render, share_summary, to_json
from .scenarios import build, heading, make_bus

ISSUE_URL = "https://github.com/bluvtx09/bringup-check/issues/new?template=diagnosis-report.md"


def guard_demo_text() -> str:
    from .guard_sim import run, scenarios
    lines = []
    for sc in scenarios():
        on, off = run(sc, guard=True), run(sc, guard=False)
        lines.append(f"== {sc.name}: {sc.note}")
        lines.append("   " + t("gdemo.verdict_act" if on.acted else "gdemo.verdict_none")
                     + ("   " + t("gdemo.at", s=on.first_action_s) if on.acted else ""))
        lines.append("   " + t("gdemo.over", thr=on_threshold(sc), a=off.seconds_over_threshold, b=on.seconds_over_threshold))
        for ts, e in on.events:
            lines.append(f"   {ts:5.2f}s  {e.message}")
        lines.append("")
    lines.append(t("gdemo.note"))
    return "\n".join(lines)


def on_threshold(sc) -> float:
    from .guard import GuardConfig
    return GuardConfig().param(sc.motor, "load_threshold")


def _parse_ids(text: str) -> list[int]:
    return [int(x) for x in text.split(",") if x.strip()]


def diagnose(bus, ids, names, calib=None, servo_rating=None, hold_seconds=0.0, title="", sleep=None,
             gripper_ids=frozenset({6}), calib_roots=None, log=print):
    """Run the checks. `calib` may be a dict, a path, "auto", or None."""
    snap = take_snapshot(bus, ids)
    if calib == "auto":
        positions = {i: snap.get(i, "Present_Position") for i in snap.responding}
        path, calib, n = pick(candidates(calib_roots), positions)
        if path is None:
            log(t("cli.calib_none"))
        else:
            log(t("cli.calib_many", path=path) if n > 1 else t("cli.calib_found", path=path))
    elif isinstance(calib, (str, Path)):
        calib = load_calibration(Path(calib).expanduser())
    findings = run_all(snap, names, calib, servo_rating, gripper_ids=gripper_ids)
    hold = None
    if hold_seconds and snap.responding:
        kw = {"sleep": sleep} if sleep else {}
        hold_findings, hold = run_hold_test(bus, snap, names, hold_seconds, **kw)
        findings = sorted(findings + hold_findings, key=lambda f: LEVEL_ORDER[f.level])
    return snap, findings, hold, render(snap, findings, names, title)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bringup-check",
                                description="Setup diagnostics for Feetech serial-bus servo robots (SO-100/SO-101)")
    p.add_argument("--port", help="controller port, e.g. /dev/ttyACM0 or COM3")
    p.add_argument("--baud", type=int, default=1_000_000)
    p.add_argument("--ids", default="1,2,3,4,5,6", help="motor IDs in daisy-chain order (default 1-6)")
    p.add_argument("--calib", help='LeRobot calibration JSON path, or "auto" to find the matching one')
    p.add_argument("--servo-rating", type=float, help="servo voltage rating (7.4 or 12) to check the supply against")
    p.add_argument("--gripper-ids", default="6")
    p.add_argument("--hold-test", type=float, default=0.0, metavar="SECONDS",
                   help="power the motors and hold the current pose for N seconds while checking supply and current")
    p.add_argument("--json", help="save the full result as JSON")
    p.add_argument("--share", action="store_true", help="print a short block to paste into a GitHub issue")
    p.add_argument("--lang", choices=["ko", "en"], help="output language (default: system language)")
    p.add_argument("--demo", help='run reproduced incident scenarios without hardware: "all" or a scenario name')
    p.add_argument("--analyze-log", metavar="GUARD_CSV", help="summarise a guard log written with --robot.guard_log")
    p.add_argument("--guard-demo", action="store_true",
                   help="show what the teleop overload guard does on simulated situations (no hardware)")
    p.add_argument("--scan-dataset", nargs="+", metavar="DATASET",
                   help="find moments where the follower was blocked in recorded LeRobot datasets "
                        "(local folders or Hugging Face dataset ids; no robot needed)")
    p.add_argument("--top", type=int, default=10, help="with --scan-dataset: how many of the longest holds to list")
    p.add_argument("--version", action="version", version=__version__)
    a = p.parse_args(argv)
    if a.lang:
        set_lang(a.lang)

    if a.scan_dataset:
        from .datasetscan import render as render_scan, render_summary, scan, to_json as scan_json
        results = []
        for src in a.scan_dataset:
            try:
                r = scan(src)
            except SystemExit:
                raise
            except Exception as e:      # one broken dataset should not stop a batch
                print(f"== {src}\n[{t('level.problem')}] {e}\n")
                continue
            results.append(r)
            print(render_scan(r, a.top))
            print()
        if len(results) > 1:
            print(render_summary(results))
        if a.json:
            Path(a.json).write_text(scan_json(results), encoding="utf-8")
            print(t("cli.json_saved", path=a.json))
        return 0

    ids = _parse_ids(a.ids)
    names = SO101_NAMES if ids == [1, 2, 3, 4, 5, 6] else {}
    grippers = frozenset(_parse_ids(a.gripper_ids))

    if a.guard_demo:
        print(guard_demo_text())
        return 0

    if a.analyze_log:
        from .loganalyze import judge, load_rows, render as render_log, summarize
        rows = load_rows(a.analyze_log)
        print(render_log(summarize(rows)))
        from .loganalyze import TESTS
        for mark in dict.fromkeys(r.get("mark", "") for r in rows):
            if mark in TESTS:
                v = judge(mark, [r for r in rows if r.get("mark") == mark])
                print(f"[{'PASS' if v.passed else 'FAIL'}] {mark}: " + "; ".join(v.reasons))
        return 0

    if a.demo:
        scs = build()
        chosen = scs if a.demo == "all" else {a.demo: scs[a.demo]}
        for sc in chosen.values():
            _, _, _, text = diagnose(make_bus(sc), ids, names, sc["calib"], sc["servo_rating"],
                                     2.0 if sc["hold_test"] else 0.0, heading(sc, get_lang()),
                                     sleep=lambda _: None)
            print(text)
        return 0

    if not a.port:
        p.error(t("cli.need_port"))
    if a.hold_test:
        print(t("cli.hold_warn"))
        if input(t("cli.hold_confirm")).strip().lower() != "y":
            return 1
    try:
        bus = FeetechBus(a.port, a.baud)
    except BusError as e:
        print(f"[{t('level.problem')}] {e}")
        return 2
    try:
        snap, findings, hold, text = diagnose(bus, ids, names, a.calib, a.servo_rating, a.hold_test,
                                              gripper_ids=grippers)
    finally:
        bus.close()
    print(text)
    meta = {"tool": "bringup-check", "version": __version__,
            "time": datetime.now(timezone.utc).isoformat(), "ids": ids}
    if a.json:
        Path(a.json).write_text(to_json(snap, findings, hold, meta), encoding="utf-8")
        print(t("cli.json_saved", path=a.json))
    if a.share:
        print(share_summary(snap, findings, meta))
    print(t("cli.share", url=ISSUE_URL))
    return 1 if any(f.level == PROBLEM for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
