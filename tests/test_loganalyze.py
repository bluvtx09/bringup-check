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
