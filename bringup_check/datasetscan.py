"""Find moments in recorded LeRobot datasets where the follower was blocked.

A recorded teleoperation dataset stores, for every frame, what the leader commanded
(`action`) and where the follower actually was (`observation.state`). When a follower
joint is blocked (the gripper is already closed on an object, or a joint is pressed
into the table) the command stays far from the real position while the joint does not
move. That is the situation the overload guard targets, and the one a kit seller named
as the most common cause of burnt follower servos.

This scan uses the guard's own position conditions (gap, stillness, duration) so its
results say how often the guard would have been in play. Datasets carry no load or
temperature, so a "blocked hold" here is a candidate, not proof that the servo strained.

Works on a local dataset folder or a Hugging Face dataset id (needs `huggingface_hub`).
Needs `numpy` and `pyarrow`: pip install "bringup-check[dataset]".
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .guard import GuardConfig
from .messages import t

LONG_S = 5.0        # a squeeze longer than this is unusual for a normal grasp
VERY_LONG_S = 15.0  # long enough to heat a servo noticeably at high load


@dataclass
class Hold:
    episode: int
    joint: str
    start_s: float
    seconds: float
    max_gap: float      # largest |command - position| during the hold, dataset units
    direction: str      # "close"/"open" for a gripper, "+"/"-" for an arm joint
    kind: str = "blocked"   # "offset": the whole episode sits off by this much, so it is
                            # more likely a leader/follower calibration mismatch than a block


@dataclass
class JointStats:
    joint: str
    holds: int = 0
    episodes: int = 0
    seconds: float = 0.0
    longest: float = 0.0
    long_holds: int = 0
    very_long_holds: int = 0
    offset_episodes: int = 0      # episodes where the joint is off for the whole episode
    worst_offset: float = 0.0


@dataclass
class ScanResult:
    source: str
    fps: float
    episodes: int
    seconds: float
    units: str
    joints: list[str]
    holds: list[Hold] = field(default_factory=list)
    stats: dict[str, JointStats] = field(default_factory=dict)

    def episodes_with_holds(self) -> int:
        return len({h.episode for h in self.holds})


# --- loading ----------------------------------------------------------------------

def _need(module: str):
    import importlib
    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise SystemExit(t("scan.need_deps")) from e


def resolve(source: str, cache_dir: str | None = None) -> Path:
    """A local folder as is; otherwise download meta/info.json and data/ from the Hub."""
    p = Path(source).expanduser()
    if p.exists():
        return p
    import os
    # Windows without developer mode cannot make symlinks; the cache still works, so skip the warning.
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    hub = _need("huggingface_hub")
    kw = {"cache_dir": cache_dir} if cache_dir else {}
    return Path(hub.snapshot_download(source, repo_type="dataset",
                                      allow_patterns=["meta/info.json", "data/**/*.parquet"], **kw))


def _joint_names(info: dict, n: int) -> list[str]:
    names = (info.get("features", {}).get("action", {}) or {}).get("names")
    if isinstance(names, dict):          # some older datasets: {"motors": [...]}
        names = next(iter(names.values()), None)
    if not isinstance(names, list) or len(names) != n:
        return [f"joint{i}" for i in range(n)]
    return [str(x).removesuffix(".pos") for x in names]


def load(root: Path):
    """Return (fps, joint names, {episode: (action[N,J], state[N,J])})."""
    np = _need("numpy")
    pq = _need("pyarrow.parquet")
    info_path = root / "meta" / "info.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    files = sorted(root.glob("data/**/*.parquet")) or sorted(root.glob("**/*.parquet"))
    if not files:
        raise SystemExit(t("scan.no_files", path=root))
    eps: dict[int, list] = {}
    for fi, f in enumerate(files):
        cols = pq.read_schema(f).names
        if "action" not in cols or "observation.state" not in cols:
            continue
        want = ["action", "observation.state"] + [c for c in ("episode_index", "frame_index") if c in cols]
        tb = pq.read_table(f, columns=want).to_pydict()
        n = len(tb["action"])
        ep_col = tb.get("episode_index") or [fi] * n
        fr_col = tb.get("frame_index") or list(range(n))
        for i in range(n):
            eps.setdefault(int(ep_col[i]), []).append((fr_col[i], tb["action"][i], tb["observation.state"][i]))
    if not eps:
        raise SystemExit(t("scan.no_columns", path=root))
    out = {}
    for ep, rows in eps.items():
        rows.sort(key=lambda r: r[0])
        a = np.asarray([r[1] for r in rows], dtype=float)
        s = np.asarray([r[2] for r in rows], dtype=float)
        if a.ndim == 2 and a.shape == s.shape and len(a) > 1:
            out[ep] = (a, s)
    fps = float(info.get("fps") or 30.0)
    width = next(iter(out.values()))[0].shape[1]
    return fps, _joint_names(info, width), out


# --- detection --------------------------------------------------------------------

def _runs(mask) -> list[tuple[int, int]]:
    out, start = [], None
    for i, m in enumerate(mask):
        if m and start is None:
            start = i
        elif not m and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def find_holds(action, state, fps: float, joints: list[str], episode: int = 0,
               config: GuardConfig | None = None) -> list[Hold]:
    """Stretches where |command - position| stays above the guard's gap threshold while
    the joint does not move, for at least the guard's act_after time."""
    import numpy as np
    cfg = config or GuardConfig()
    win = max(1, int(round(0.3 * fps)))
    kernel = np.ones(win) / win
    holds = []
    for j, name in enumerate(joints):
        grip = "gripper" in name
        gap_thr = cfg.grip["gap_threshold"] if grip else cfg.arm["gap_threshold"]
        gap = action[:, j] - state[:, j]
        speed = np.abs(np.gradient(state[:, j])) * fps
        speed = np.convolve(speed, kernel, mode="same")
        # A blocked joint is still. If the gap is just as large while the joint moves
        # freely, the leader and follower are calibrated differently instead.
        moving = speed >= cfg.still_speed
        offset = moving.sum() >= fps and abs(float(np.median(gap[moving]))) >= gap_thr
        mask = (np.abs(gap) >= gap_thr) & (speed < cfg.still_speed)
        for a, b in _runs(mask):
            secs = (b - a) / fps
            if secs < cfg.act_after:
                continue
            g = gap[a:b]
            sign = float(np.median(g))
            if grip:
                direction = "close" if sign < 0 else "open"
            else:
                direction = "+" if sign > 0 else "-"
            holds.append(Hold(episode, name, a / fps, secs, float(np.abs(g).max()), direction,
                              "offset" if offset else "blocked"))
    return holds


def guess_units(episodes: dict, joints: list[str]) -> str:
    import numpy as np
    body = [j for j, n in enumerate(joints) if "gripper" not in n]
    peak = max(float(np.abs(s[:, body]).max()) for _, s in episodes.values()) if body else 0.0
    return "degrees" if peak > 110 else "normalized"


def scan(source: str, cache_dir: str | None = None, config: GuardConfig | None = None) -> ScanResult:
    root = resolve(source, cache_dir)
    fps, joints, episodes = load(root)
    res = ScanResult(source=source, fps=fps, episodes=len(episodes),
                     seconds=sum(len(a) for a, _ in episodes.values()) / fps,
                     units=guess_units(episodes, joints), joints=joints,
                     stats={n: JointStats(n) for n in joints})
    for ep in sorted(episodes):
        a, s = episodes[ep]
        res.holds.extend(find_holds(a, s, fps, joints, ep, config))
    for n in joints:
        st = res.stats[n]
        off = {}
        for h in res.holds:
            if h.joint == n and h.kind == "offset":
                off[h.episode] = max(off.get(h.episode, 0.0), h.max_gap)
        st.offset_episodes = len(off)
        st.worst_offset = max(off.values(), default=0.0)
        hs = [h for h in res.holds if h.joint == n and h.kind == "blocked"]
        st.holds = len(hs)
        st.episodes = len({h.episode for h in hs})
        st.seconds = sum(h.seconds for h in hs)
        st.longest = max((h.seconds for h in hs), default=0.0)
        st.long_holds = sum(1 for h in hs if h.seconds >= LONG_S)
        st.very_long_holds = sum(1 for h in hs if h.seconds >= VERY_LONG_S)
    return res


# --- output -----------------------------------------------------------------------

def blocked(res: ScanResult) -> list[Hold]:
    return [h for h in res.holds if h.kind == "blocked"]


def render(res: ScanResult, top: int = 10) -> str:
    lines = [f"== {res.source}",
             t("scan.head", eps=res.episodes, minutes=res.seconds / 60, fps=res.fps,
               units=t("scan.units_" + res.units))]
    if res.units == "degrees":
        lines.append(t("scan.units_note"))
    lines.append("")
    w = max(16, max(len(n) for n in res.joints) + 1)
    lines.append(f"{t('scan.col_joint'):{w - 2}s}" + t("scan.cols"))
    for n in res.joints:
        st = res.stats[n]
        share = 100.0 * st.episodes / res.episodes if res.episodes else 0.0
        lines.append(f"{n:{w}s} {st.holds:5d} {st.episodes:5d} ({share:3.0f}%) {st.seconds:8.1f} "
                     f"{st.longest:7.1f} {st.long_holds:6d} {st.very_long_holds:6d}")
    lines.append("")
    holds = blocked(res)
    if holds:
        lines.append(t("scan.worst", n=min(top, len(holds))))
        for h in sorted(holds, key=lambda h: (-h.seconds, -h.max_gap))[:top]:
            lines.append("  " + t("scan.hold_line", ep=h.episode, joint=h.joint, start=h.start_s,
                                    secs=h.seconds, gap=h.max_gap, dir=t("scan.dir_" + h.direction)))
        lines.append("")
    offsets = [(n, res.stats[n]) for n in res.joints if res.stats[n].offset_episodes]
    if offsets:
        lines.append(t("scan.offset_title"))
        for n, st in offsets:
            eps = sorted({h.episode for h in res.holds if h.joint == n and h.kind == "offset"})
            shown = ", ".join(str(e) for e in eps[:8]) + (" …" if len(eps) > 8 else "")
            lines.append("  " + t("scan.offset_line", joint=n, n=st.offset_episodes, gap=st.worst_offset, eps=shown))
        lines.append("")
    grip = [n for n in res.joints if "gripper" in n]
    arm = [n for n in res.joints if "gripper" not in n]
    g_holds = sum(res.stats[n].holds for n in grip)
    g_long = sum(res.stats[n].long_holds for n in grip)
    a_holds = sum(res.stats[n].holds for n in arm)
    if not holds:
        lines.append(t("scan.none"))
    else:
        lines.append(t("scan.read_grip", n=g_holds, long=g_long))
        lines.append(t("scan.read_arm_none") if a_holds == 0 else t("scan.read_arm", n=a_holds))
    lines.append(t("scan.caveat"))
    return "\n".join(lines)


def render_summary(results: list[ScanResult]) -> str:
    lines = [t("scan.sum_title"), t("scan.sum_cols")]
    for r in results:
        grip = [n for n in r.joints if "gripper" in n]
        arm = [n for n in r.joints if "gripper" not in n]
        hs = blocked(r)
        g = sum(r.stats[n].holds for n in grip)
        g_eps = len({h.episode for h in hs if h.joint in grip})
        g_long = sum(r.stats[n].long_holds for n in grip)
        a = sum(r.stats[n].holds for n in arm)
        off = sum(r.stats[n].offset_episodes for n in r.joints)
        longest = max((h.seconds for h in hs), default=0.0)
        share = 100.0 * g_eps / r.episodes if r.episodes else 0.0
        lines.append(f"{r.source[:44]:44s} {r.episodes:5d} {g:6d} {share:5.0f}% {g_long:6d} {a:6d} {off:6d} {longest:7.1f}")
    return "\n".join(lines)


def to_json(results: list[ScanResult]) -> str:
    return json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2)
