import json

import pytest

np = pytest.importorskip("numpy")
pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from bringup_check.cli import main  # noqa: E402
from bringup_check.datasetscan import find_holds, scan  # noqa: E402

FPS = 30
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]


def episode(seconds=12.0):
    """A calm episode: the follower tracks the command with a small lag."""
    n = int(seconds * FPS)
    tt = np.arange(n) / FPS
    state = np.zeros((n, 6))
    state[:, 1] = 20 * np.sin(tt / 2)
    state[:, 5] = 30.0
    action = state.copy()
    action[:, 1] += 3.0          # gravity sag: steady small gap, should never count
    return action, state


def put(arr, a, b, value):
    arr[int(a * FPS):int(b * FPS)] = value


def test_gripper_squeeze_found_and_short_grasp_ignored():
    action, state = episode()
    # 3 s squeeze: commanded closed (0) while stuck on an object at 18
    put(state[:, 5], 2, 5, 18.0); put(action[:, 5], 2, 5, 0.0)
    # 1 s squeeze: shorter than the guard's 1.5 s, ignored
    put(state[:, 5], 8, 9, 18.0); put(action[:, 5], 8, 9, 0.0)
    holds = find_holds(action, state, FPS, JOINTS)
    assert [(h.joint, h.direction) for h in holds] == [("gripper", "close")]
    assert holds[0].seconds == pytest.approx(3.0, abs=0.4)   # the jump into contact counts as motion
    assert holds[0].max_gap == pytest.approx(18.0)


def test_arm_pressed_into_table_found_but_fast_motion_is_not():
    action, state = episode()
    # elbow pressed into the table: command 15 past a still position for 2 s
    put(state[:, 2], 3, 5, -10.0); put(action[:, 2], 3, 5, -25.0)
    # wrist moving fast with a big lag: a gap, but not still, so not blocked
    n = len(state)
    state[:, 3] = 60 * np.sin(np.arange(n) / FPS * 3)
    action[:, 3] = 60 * np.sin(np.arange(n) / FPS * 3 + 0.4)
    holds = find_holds(action, state, FPS, JOINTS)
    assert [(h.joint, h.direction) for h in holds] == [("elbow_flex", "-")]


def write_v3(root, episodes):
    (root / "meta").mkdir(parents=True)
    (root / "data" / "chunk-000").mkdir(parents=True)
    names = [f"{j}.pos" for j in JOINTS]
    info = {"codebase_version": "v3.0", "fps": FPS,
            "features": {"action": {"shape": [6], "names": names},
                         "observation.state": {"shape": [6], "names": names}}}
    (root / "meta" / "info.json").write_text(json.dumps(info))
    act, st, ep, fr = [], [], [], []
    for i, (a, s) in enumerate(episodes):
        act += a.tolist(); st += s.tolist(); ep += [i] * len(a); fr += list(range(len(a)))
    tb = pa.table({"action": act, "observation.state": st, "episode_index": ep, "frame_index": fr})
    pq.write_table(tb, root / "data" / "chunk-000" / "file-000.parquet")


def test_scan_local_dataset_and_cli(tmp_path, capsys):
    a0, s0 = episode()
    a1, s1 = episode()
    put(s1[:, 5], 1, 8, 15.0); put(a1[:, 5], 1, 8, 0.0)       # 7 s squeeze in episode 1
    write_v3(tmp_path / "ds", [(a0, s0), (a1, s1)])
    res = scan(str(tmp_path / "ds"))
    assert res.episodes == 2 and res.units == "normalized"
    g = res.stats["gripper"]
    assert (g.holds, g.episodes, g.long_holds, g.very_long_holds) == (1, 1, 1, 0)
    assert res.stats["elbow_flex"].holds == 0

    out = tmp_path / "scan.json"
    assert main(["--lang", "en", "--scan-dataset", str(tmp_path / "ds"), "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert "episode 1 · gripper" in text and "pushing to close" in text
    assert json.loads(out.read_text())[0]["stats"]["gripper"]["holds"] == 1


def test_constant_leader_follower_offset_is_not_called_blocked():
    action, state = episode()
    n = len(state)
    state[:, 4] = 40 * np.sin(np.arange(n) / FPS)          # wrist_roll moves back and forth...
    put(state[:, 4], 4, 7, state[int(4 * FPS), 4])          # ...and pauses for 3 s
    action[:, 4] = state[:, 4] + 12.0                       # but the leader reads 12 higher all the time
    holds = find_holds(action, state, FPS, JOINTS)
    assert holds and all(h.kind == "offset" for h in holds if h.joint == "wrist_roll")
