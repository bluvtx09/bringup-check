"""Find the LeRobot calibration file that matches the arm on the bus."""

from __future__ import annotations

import json
import os
from pathlib import Path


def calibration_roots() -> list[Path]:
    roots = []
    if os.environ.get("HF_LEROBOT_CALIBRATION"):
        roots.append(Path(os.environ["HF_LEROBOT_CALIBRATION"]))
    if os.environ.get("HF_HOME"):
        roots.append(Path(os.environ["HF_HOME"]) / "lerobot" / "calibration")
    roots.append(Path.home() / ".cache" / "huggingface" / "lerobot" / "calibration")
    seen, out = set(), []
    for r in roots:
        r = r.expanduser()
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def load_calibration(path: Path) -> dict | None:
    """Return the file as a LeRobot calibration dict, or None if it isn't one."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data:
        return None
    ok = [v for v in data.values() if isinstance(v, dict) and {"id", "range_min", "range_max"} <= v.keys()]
    return data if len(ok) == len(data) else None


def candidates(roots: list[Path] | None = None) -> list[tuple[Path, dict]]:
    out = []
    for root in roots or calibration_roots():
        if not root.is_dir():
            continue
        for p in root.rglob("*.json"):
            c = load_calibration(p)
            if c is not None:
                out.append((p, c))
    return out


def score(calib: dict, positions: dict[int, int | None]) -> int:
    """How many motors on the bus sit inside this file's calibrated ranges."""
    n = 0
    for c in calib.values():
        pos = positions.get(c["id"])
        if pos is not None and c["range_min"] - 100 <= pos <= c["range_max"] + 100:
            n += 1
    return n


def pick(cands: list[tuple[Path, dict]], positions: dict[int, int | None]) -> tuple[Path | None, dict | None, int]:
    """Best match by (motors in range, motor ids present, newest file). Returns (path, calib, n_candidates)."""
    if not cands:
        return None, None, 0
    ids = {i for i, p in positions.items() if p is not None}

    def key(item):
        p, c = item
        overlap = len(ids & {v["id"] for v in c.values()})
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0
        return (score(c, positions), overlap, mtime)

    best = max(cands, key=key)
    return best[0], best[1], len(cands)
