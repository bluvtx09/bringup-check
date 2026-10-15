"""Plain-language report and JSON log."""

from __future__ import annotations

import json

from .checks import INFO, PROBLEM, WARN, Finding, Snapshot
from .registers import decode_status, load_percent, milliamps, volts

ICON = {PROBLEM: "[문제]", WARN: "[주의]", INFO: "[정보]"}


def _fmt(v, f, unit=""):
    return "-" if v is None else f"{f(v)}{unit}"


def motor_table(s: Snapshot, names: dict[int, str]) -> str:
    rows = [("ID", "관절", "전압", "온도", "부하", "전류", "위치", "토크", "상태")]
    for i in s.expected:
        if i not in s.responding:
            rows.append((str(i), names.get(i, ""), "응답 없음", "", "", "", "", "", ""))
            continue
        g = lambda k: s.get(i, k)
        st = decode_status(g("Status") or 0)
        rows.append((str(i), names.get(i, ""),
                     _fmt(g("Present_Voltage"), lambda x: f"{volts(x):.1f}", "V"),
                     _fmt(g("Present_Temperature"), str, "°C"),
                     _fmt(g("Present_Load"), lambda x: f"{load_percent(x):.0f}", "%"),
                     _fmt(g("Present_Current"), lambda x: f"{milliamps(x):.0f}", "mA"),
                     _fmt(g("Present_Position"), str),
                     "켜짐" if g("Torque_Enable") else "꺼짐",
                     ", ".join(b[1] for b in st) or "정상"))
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip() for r in rows)


def render(s: Snapshot, findings: list[Finding], names: dict[int, str], title: str = "") -> str:
    n_p = sum(f.level == PROBLEM for f in findings)
    n_w = sum(f.level == WARN for f in findings)
    lines = []
    if title:
        lines += [f"== {title} ==", ""]
    if n_p == 0 and n_w == 0:
        lines.append("결과: 셋업에서 문제를 찾지 못했습니다.")
    else:
        lines.append(f"결과: 문제 {n_p}건 · 주의 {n_w}건")
    lines += ["", motor_table(s, names), ""]
    for f in findings:
        if f.level == INFO:
            continue
        lines.append(f"{ICON[f.level]} {f.title}")
        if f.detail:
            lines.append(f"    무슨 일인지: {f.detail}")
        if f.fix:
            lines.append(f"    해볼 것: {f.fix}")
        lines.append("")
    info = [f.title for f in findings if f.level == INFO]
    if info:
        lines.append("참고: " + " / ".join(info))
    return "\n".join(lines).rstrip() + "\n"


def to_json(s: Snapshot, findings: list[Finding], hold: dict | None = None, meta: dict | None = None) -> str:
    return json.dumps({"meta": meta or {}, "snapshot": s.to_dict(),
                       "findings": [f.to_dict() for f in findings], "hold_test": hold},
                      ensure_ascii=False, indent=2, default=str)
