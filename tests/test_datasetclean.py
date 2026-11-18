import json

import pytest

np = pytest.importorskip("numpy")
pa = pytest.importorskip("pyarrow")
pq = pytest.importorskip("pyarrow.parquet")

from bringup_check.cli import main  # noqa: E402
from bringup_check.datasetclean import clean, relabel  # noqa: E402
from bringup_check.datasetscan import find_holds, scan  # noqa: E402

FPS = 30
JOINTS = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"]
G = 5


def episode(seconds=12.0):
    n = int(seconds * FPS)
    tt = np.arange(n) / FPS
    state = np.zeros((n, 6))
    state[:, 1] = 20 * np.sin(tt / 2)
    state[:, G] = 30.0
    action = state.copy()
    action[:, 1] += 3.0
    return action, state


def put(arr, a, b, value):
    arr[int(a * FPS):int(b * FPS)] = value


def squeeze(seconds=12.0, start=2, end=6, contact=18.0, command=0.0):
    a, s = episode(seconds)
    put(s[:, G], start, end, contact)
    put(a[:, G], start, end, command)
    return a, s


def write_v3(root, episodes, with_stats=True):
    (root / "meta" / "episodes" / "chunk-000").mkdir(parents=True)
    (root / "data" / "chunk-000").mkdir(parents=True)
    (root / "videos").mkdir()
    (root / "videos" / "placeholder.mp4").write_bytes(b"video")
    names = [f"{j}.pos" for j in JOINTS]
    info = {"codebase_version": "v3.0", "fps": FPS,
            "features": {"action": {"shape": [6], "names": names},
                         "observation.state": {"shape": [6], "names": names}}}
    (root / "meta" / "info.json").write_text(json.dumps(info))
    act, st, ep, fr = [], [], [], []
    for i, (a, s) in enumerate(episodes):
        act += a.tolist(); st += s.tolist(); ep += [i] * len(a); fr += list(range(len(a)))
    f32 = pa.list_(pa.float32())
    tb = pa.table({"action": pa.array(act, type=f32), "observation.state": pa.array(st, type=f32),
                   "episode_index": ep, "frame_index": fr})
    pq.write_table(tb, root / "data" / "chunk-000" / "file-000.parquet")
    if with_stats:
        allx = np.asarray(act)
        stats = {"action": {"min": allx.min(0).tolist(), "max": allx.max(0).tolist(),
                            "mean": allx.mean(0).tolist(), "std": allx.std(0).tolist(), "count": [len(allx)]}}
        (root / "meta" / "stats.json").write_text(json.dumps(stats))
        rows = {"episode_index": list(range(len(episodes))),
                "stats/action/mean": [a.mean(0).tolist() for a, _ in episodes],
                "stats/action/min": [a.min(0).tolist() for a, _ in episodes]}
        pq.write_table(pa.table(rows), root / "meta" / "episodes" / "chunk-000" / "file-000.parquet")


def test_relabel_caps_the_gap_and_leaves_everything_else():
    a, s = squeeze()
    holds = find_holds(a, s, FPS, JOINTS)
    na, changed = relabel(a, s, holds, {"gripper": G}, margin=3.0, fps=FPS)
    gap = na[:, G] - s[:, G]
    assert changed.sum() > 2.5 * FPS
    assert np.abs(gap[changed]).max() == pytest.approx(3.0)
    assert (gap[changed] < 0).all()                    # still pushes to close, just less
    assert np.array_equal(na[~changed], a[~changed])
    assert np.array_equal(na[:, :G], a[:, :G])         # arm joints untouched


def test_margin_zero_means_command_equals_position():
    a, s = squeeze()
    na, changed = relabel(a, s, find_holds(a, s, FPS, JOINTS), {"gripper": G}, margin=0.0, fps=FPS)
    assert np.allclose(na[changed, G], s[changed, G])


def test_clean_writes_a_consistent_copy(tmp_path, capsys):
    a0, s0 = episode()
    a1, s1 = squeeze(start=1, end=8)
    src = tmp_path / "ds"
    write_v3(src, [(a0, s0), (a1, s1)])
    rep = clean(str(src), str(tmp_path / "out"), margin=3.0)
    out = tmp_path / "out"

    assert rep.episodes == 2 and rep.episodes_changed == 1 and rep.keep_episodes == [0]
    assert rep.frames_changed > 6 * FPS
    assert rep.mean_gap_before == pytest.approx(18.0, abs=0.5) and rep.mean_gap_after == pytest.approx(3.0)
    assert (out / "videos" / "placeholder.mp4").read_bytes() == b"video"
    # the original is untouched
    assert scan(str(src)).stats["gripper"].holds == 1
    # the copy no longer has a gripper hold, and its schema is unchanged
    assert scan(str(out)).stats["gripper"].holds == 0
    tb = pq.read_table(out / "data" / "chunk-000" / "file-000.parquet")
    assert tb.schema.field("action").type == pa.list_(pa.float32())
    # stats follow the new actions
    stats = json.loads((out / "meta" / "stats.json").read_text())
    new_act = np.asarray(tb.column("action").to_pylist())
    assert stats["action"]["mean"] == pytest.approx(new_act.mean(0).tolist(), rel=1e-5)
    assert stats["action"]["count"] == [len(new_act)]
    ep_stats = pq.read_table(out / "meta" / "episodes" / "chunk-000" / "file-000.parquet").to_pydict()
    assert ep_stats["stats/action/min"][1][G] == pytest.approx(new_act[len(a0):, G].min(), abs=1e-4)
    saved = json.loads((out / "cleaning_report.json").read_text())
    assert saved["keep_episodes"] == [0] and "meta/stats.json" in saved["stats_updated"]


def test_calibration_offset_is_left_alone(tmp_path):
    a, s = episode()
    n = len(s)
    s[:, 4] = 40 * np.sin(np.arange(n) / FPS)
    put(s[:, 4], 4, 7, s[int(4 * FPS), 4])
    a[:, 4] = s[:, 4] + 12.0
    write_v3(tmp_path / "ds", [(a, s)])
    rep = clean(str(tmp_path / "ds"), str(tmp_path / "out"), joints="all")
    assert rep.frames_changed == 0


def test_refuses_to_overwrite(tmp_path):
    write_v3(tmp_path / "ds", [episode()])
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_text("keep me")
    with pytest.raises(SystemExit):
        clean(str(tmp_path / "ds"), str(tmp_path / "out"))
    assert (tmp_path / "out" / "x").read_text() == "keep me"


def test_cli(tmp_path, capsys):
    write_v3(tmp_path / "ds", [squeeze()])
    assert main(["--lang", "en", "--clean-dataset", str(tmp_path / "ds"), "--out", str(tmp_path / "out"),
                 "--margin", "2"]) == 0
    text = capsys.readouterr().out
    assert "Frames changed:" in text and "margin 2" in text


def test_one_frame_episode_is_copied_not_judged(tmp_path):
    a, s = squeeze()
    write_v3(tmp_path / "ds", [(a, s), (a[:1], s[:1])])
    rep = clean(str(tmp_path / "ds"), str(tmp_path / "out"))
    assert rep.episodes == 2 and 1 in rep.keep_episodes and rep.episodes_changed == 1
