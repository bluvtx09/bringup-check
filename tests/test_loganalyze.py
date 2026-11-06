import csv

from bringup_check.cli import main
from bringup_check.loganalyze import judge, load_rows, summarize

COLS = ["t", "motor", "goal", "sent", "present", "load", "temp", "released", "strain", "event", "mark", "mode"]


def write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)


def squeeze_rows(intervene_at=2.0, load_after=15.0):
    rows = []
    for k in range(240):
        tt = k / 30
        released = tt >= intervene_at
        event = "light_hold" if abs(tt - intervene_at) < 1e-9 else ""
        load = 50.0 if not released else load_after
        rows.append([f"{10 + tt:.3f}", "gripper", "0", "33.5", "35", f"{load}", "31", int(released), "0", event,
                     "gripper_squeeze", "protect"])
    return rows


def test_gripper_squeeze_pass_and_fail(tmp_path):
    write(tmp_path / "a.csv", squeeze_rows())
    assert judge("gripper_squeeze", load_rows(tmp_path / "a.csv")).passed
    write(tmp_path / "b.csv", squeeze_rows(load_after=40.0))   # still squeezing hard after "intervening"
    assert not judge("gripper_squeeze", load_rows(tmp_path / "b.csv")).passed
    rows = [r[:9] + [""] + r[10:] for r in squeeze_rows()]      # never intervened
    write(tmp_path / "c.csv", rows)
    assert not judge("gripper_squeeze", load_rows(tmp_path / "c.csv")).passed


def test_false_positive_tests_fail_on_any_intervention(tmp_path):
    rows = [[f"{k / 30:.3f}", "elbow_flex", "0", "0", "0", "20", "30", "0", "0",
             "backoff" if k == 50 else ("heavy_hold" if k == 70 else ""), "sweep", "protect"] for k in range(300)]
    write(tmp_path / "s.csv", rows)
    v = judge("sweep", load_rows(tmp_path / "s.csv"))
    assert not v.passed and any("heavy-hold" in r for r in v.reasons)


def test_summary_and_cli(tmp_path, capsys):
    write(tmp_path / "a.csv", squeeze_rows())
    s = summarize(load_rows(tmp_path / "a.csv"))
    g = s.motors["gripper"]
    assert g.max_load == 50.0 and abs(g.seconds_high - 2.0) < 0.1 and g.count({"light_hold"}) == 1
    assert main(["--analyze-log", str(tmp_path / "a.csv"), "--lang", "en"]) == 0
    assert "[PASS] gripper_squeeze" in capsys.readouterr().out


def _row(t, motor, goal, present, load, event="", mark="x"):
    return {"t": f"{t:.3f}", "motor": motor, "goal": str(goal), "sent": str(goal), "present": str(present),
            "load": str(load), "temp": "30", "released": "0", "strain": "0", "event": event, "mark": mark, "mode": "protect"}


def test_sweep_with_a_joint_that_never_moves_says_check_hardware():
    import math
    from bringup_check.loganalyze import judge
    rows = []
    for k in range(300):
        t = k / 30
        g = 15 * math.sin(math.pi * t / 2)
        rows.append(_row(t, "shoulder_pan", g, g, 10))                       # healthy
        rows.append(_row(t, "wrist_roll", g, -0.2, max(-100, min(100, 8.3 * g)),
                         "jammed" if k == 150 else "", "sweep"))             # stuck at -0.2
    v = judge("sweep", rows)
    assert not v.passed and v.status == "hardware" and v.label == "CHECK HARDWARE"
    assert "wrist_roll did not move at all" in v.reasons[0] and "shoulder_pan" not in v.reasons[0]


def test_soft_squeeze_that_never_loads_the_gripper_is_inconclusive_not_fail():
    from bringup_check.loganalyze import judge
    rows = [_row(k / 30, "gripper", 3.0, 7.0 if k > 10 else 30.0, 20.0 if k > 10 else 60.0) for k in range(240)]
    v = judge("gripper_squeeze", rows)
    assert not v.passed and v.status == "inconclusive" and v.label == "INCONCLUSIVE"
    assert "rigid object" in v.reasons[-1]


def test_squeeze_that_stays_loaded_without_the_guard_stepping_in_is_a_real_fail():
    from bringup_check.loganalyze import judge
    rows = [_row(k / 30, "gripper", 3.0, 6.0, 40.0) for k in range(240)]   # 40 % load, gap 3 % (< 5 % threshold)
    v = judge("gripper_squeeze", rows)
    assert not v.passed and v.status == "" and v.label == "FAIL"
