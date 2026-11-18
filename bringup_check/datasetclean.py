"""Write a cleaned copy of a LeRobot dataset for robot-learning experiments.

A policy trained on teleoperation data copies the commands it sees. During a blocked
gripper hold the recorded command keeps asking to close further than the gripper can go,
so a policy learns to push past contact. This module rewrites only those frames: inside
each blocked hold that the scanner finds, the command is pulled back to the recorded
position plus a small margin, in the same direction. Everything else is copied unchanged.

The margin is kept on purpose: a position-controlled servo squeezes in proportion to the
gap between command and position, so a gap of zero would also mean no grip at all.

Also writes cleaning_report.json with what changed and the list of episodes that had no
blocked hold (for the "drop whole episodes" baseline, e.g. LeRobot's --dataset.episodes).

Holds that look like a leader/follower calibration offset are left alone: the scanner
cannot tell what the right command would have been.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .datasetscan import _need, find_holds, guess_units, _joint_names
from .guard import GuardConfig
from .messages import t


@dataclass
class CleanReport:
    source: str
    out: str
    margin: float
    joints: list[str]
    units: str
    frames: int = 0
    frames_changed: int = 0
    episodes: int = 0
    episodes_changed: int = 0
    holds_used: int = 0
    mean_gap_before: float = 0.0      # mean |command - position| over the changed frames
    mean_gap_after: float = 0.0
    keep_episodes: list[int] = field(default_factory=list)   # episodes with no blocked hold on these joints
    stats_updated: list[str] = field(default_factory=list)


def fetch_full(source: str, cache_dir: str | None = None) -> Path:
    """A local folder as is; otherwise the whole dataset (videos too) from the Hub."""
    p = Path(source).expanduser()
    if p.exists():
        return p
    import os
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    hub = _need("huggingface_hub")
    kw = {"cache_dir": cache_dir} if cache_dir else {}
    return Path(hub.snapshot_download(source, repo_type="dataset", **kw))


def relabel(action, state, holds, joint_index: dict[str, int], margin: float, fps: float):
    """Return (new action, changed-frame mask). Only blocked holds on the given joints."""
    import numpy as np
    new = action.copy()
    changed = np.zeros(len(action), dtype=bool)
    for h in holds:
        if h.kind != "blocked" or h.joint not in joint_index:
            continue
        j = joint_index[h.joint]
        a = int(round(h.start_s * fps))
        b = min(len(action), a + int(round(h.seconds * fps)))
        gap = action[a:b, j] - state[a:b, j]
        over = np.abs(gap) > margin
        new[a:b, j] = np.where(over, state[a:b, j] + np.sign(gap) * margin, action[a:b, j])
        changed[a:b] |= over
    return new, changed


def _stats(np, x, keys):
    """Recompute the statistics a LeRobot stats entry carries, for the keys it already has."""
    out = {}
    for k in keys:
        if k == "min":
            out[k] = x.min(axis=0).tolist()
        elif k == "max":
            out[k] = x.max(axis=0).tolist()
        elif k == "mean":
            out[k] = x.mean(axis=0).tolist()
        elif k == "std":
            out[k] = x.std(axis=0).tolist()
        elif k == "count":
            out[k] = [int(len(x))]
        elif len(k) == 3 and k[0] == "q" and k[1:].isdigit():      # q01, q10, q50, ...
            out[k] = np.quantile(x, int(k[1:]) / 100, axis=0).tolist()
    return out


def clean(source: str, out: str, margin: float = 3.0, joints: str = "gripper",
          cache_dir: str | None = None, config: GuardConfig | None = None) -> CleanReport:
    np = _need("numpy")
    pa = _need("pyarrow")
    pq = _need("pyarrow.parquet")
    src = fetch_full(source, cache_dir)
    dst = Path(out).expanduser()
    if dst.exists() and any(dst.iterdir()):
        raise SystemExit(t("clean.out_exists", path=dst))
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".cache", ".git"))

    info_path = dst / "meta" / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    fps = float(info.get("fps") or 30.0)
    files = sorted((dst / "data").glob("**/*.parquet")) if (dst / "data").exists() else []
    if not files:
        raise SystemExit(t("scan.no_files", path=dst))

    # Read every data file once, remember where each frame lives.
    tables, eps = {}, {}
    for fi, f in enumerate(files):
        tb = pq.read_table(f)
        if "action" not in tb.column_names or "observation.state" not in tb.column_names:
            continue
        tables[fi] = tb
        act = tb.column("action").to_pylist()
        st = tb.column("observation.state").to_pylist()
        n = len(act)
        ep_col = tb.column("episode_index").to_pylist() if "episode_index" in tb.column_names else [fi] * n
        fr_col = tb.column("frame_index").to_pylist() if "frame_index" in tb.column_names else list(range(n))
        for i in range(n):
            eps.setdefault(int(ep_col[i]), []).append((fr_col[i], fi, i, act[i], st[i]))
    if not eps:
        raise SystemExit(t("scan.no_columns", path=dst))

    width = len(next(iter(eps.values()))[0][3])
    names = _joint_names(info, width)
    pick = {n: j for j, n in enumerate(names) if joints == "all" or "gripper" in n}
    if not pick:
        raise SystemExit(t("clean.no_joint", joints=joints))

    new_actions = {fi: tb.column("action").to_pylist() for fi, tb in tables.items()}
    touched: set[int] = set()
    arrays = {}
    rep = CleanReport(source=source, out=str(dst), margin=margin, joints=sorted(pick), units="")
    before, after = [], []
    for ep in sorted(eps):
        rows = sorted(eps[ep], key=lambda r: r[0])
        a = np.asarray([r[3] for r in rows], dtype=float)
        s = np.asarray([r[4] for r in rows], dtype=float)
        arrays[ep] = (a, s)
        rep.frames += len(rows)
        if len(rows) < 2 or a.shape != s.shape:      # too short to judge; copied as is
            rep.keep_episodes.append(ep)
            continue
        holds = find_holds(a, s, fps, names, ep, config)
        used = [h for h in holds if h.kind == "blocked" and h.joint in pick]
        if not used:
            rep.keep_episodes.append(ep)
            continue
        na, changed = relabel(a, s, used, pick, margin, fps)
        rep.holds_used += len(used)
        if changed.any():
            rep.episodes_changed += 1
            rep.frames_changed += int(changed.sum())
            cols = list(pick.values())
            before.append(np.abs(a[changed][:, cols] - s[changed][:, cols]).max(axis=1))
            after.append(np.abs(na[changed][:, cols] - s[changed][:, cols]).max(axis=1))
            arrays[ep] = (na, s)
            for k, r in enumerate(rows):
                if changed[k]:
                    new_actions[r[1]][r[2]] = na[k].tolist()
                    touched.add(r[1])
    rep.episodes = len(eps)
    rep.units = guess_units(arrays, names)
    if before:
        rep.mean_gap_before = float(np.concatenate(before).mean())
        rep.mean_gap_after = float(np.concatenate(after).mean())

    for fi, tb in tables.items():
        if fi not in touched:
            continue
        col = pa.array(new_actions[fi], type=tb.schema.field("action").type)
        tb = tb.set_column(tb.column_names.index("action"), "action", col)
        pq.write_table(tb, files[fi])

    _update_stats(np, pa, pq, dst, arrays, rep)
    (dst / "cleaning_report.json").write_text(json.dumps(asdict(rep), indent=1), encoding="utf-8")
    return rep


def _update_stats(np, pa, pq, dst: Path, arrays: dict, rep: CleanReport) -> None:
    """Keep the action statistics in step with the new actions, so normalisation matches."""
    if not rep.frames_changed:
        return
    allx = np.concatenate([a for a, _ in arrays.values()])
    sp = dst / "meta" / "stats.json"
    if sp.exists():
        stats = json.loads(sp.read_text(encoding="utf-8"))
        if isinstance(stats.get("action"), dict):
            stats["action"].update(_stats(np, allx, list(stats["action"])))
            sp.write_text(json.dumps(stats, indent=4), encoding="utf-8")
            rep.stats_updated.append("meta/stats.json")
    # v3.0: per-episode stats live in meta/episodes/*.parquet as stats/action/<key> columns
    for f in sorted((dst / "meta" / "episodes").glob("**/*.parquet")) if (dst / "meta" / "episodes").exists() else []:
        tb = pq.read_table(f)
        keys = [c.split("/")[-1] for c in tb.column_names if c.startswith("stats/action/")]
        if not keys or "episode_index" not in tb.column_names:
            continue
        ep_col = tb.column("episode_index").to_pylist()
        cols = {k: tb.column(f"stats/action/{k}").to_pylist() for k in keys}
        for i, ep in enumerate(ep_col):
            if ep in arrays:
                for k, v in _stats(np, arrays[ep][0], keys).items():
                    cols[k][i] = v
        for k in keys:
            name = f"stats/action/{k}"
            tb = tb.set_column(tb.column_names.index(name), name, pa.array(cols[k], type=tb.schema.field(name).type))
        pq.write_table(tb, f)
        rep.stats_updated.append(str(f.relative_to(dst)))
    # v2.1: one JSON line per episode
    jl = dst / "meta" / "episodes_stats.jsonl"
    if jl.exists():
        lines = []
        for line in jl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            ep = row.get("episode_index")
            st = row.get("stats", {}).get("action")
            if ep in arrays and isinstance(st, dict):
                st.update(_stats(np, arrays[ep][0], list(st)))
            lines.append(json.dumps(row))
        jl.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rep.stats_updated.append("meta/episodes_stats.jsonl")


def render(rep: CleanReport) -> str:
    share = 100 * rep.frames_changed / rep.frames if rep.frames else 0.0
    out = [
        t("clean.head", out=rep.out),
        t("clean.frames", changed=rep.frames_changed, total=rep.frames, share=share,
          eps=rep.episodes_changed, all=rep.episodes),
    ]
    if rep.frames_changed:
        out.append(t("clean.gap", before=rep.mean_gap_before, after=rep.mean_gap_after, margin=rep.margin))
    out.append(t("clean.keep", n=len(rep.keep_episodes), all=rep.episodes))
    out.append(t("clean.note"))
    return "\n".join(out)
