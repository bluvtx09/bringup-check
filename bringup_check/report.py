"""Plain-language report and JSON log."""

from __future__ import annotations

import json

from .checks import INFO, PROBLEM, WARN, Finding, Snapshot
from .messages import get_lang, t
from .registers import decode_status, load_percent, milliamps, volts


def _fmt(v, f, unit=""):
    return "-" if v is None else f"{f(v)}{unit}"


def motor_table(s: Snapshot, names: dict[int, str]) -> str:
    rows = [tuple(t("report.head").split("|"))]
    for i in s.expected:
        if i not in s.responding:
            rows.append((str(i), names.get(i, ""), t("report.no_answer"), "", "", "", "", "", ""))
            continue
        g = lambda k: s.get(i, k)
        bits = decode_status(g("Status") or 0)
        rows.append((str(i), names.get(i, ""),
                     _fmt(g("Present_Voltage"), lambda x: f"{volts(x):.1f}", "V"),
                     _fmt(g("Present_Temperature"), str, "°C"),
                     _fmt(g("Present_Load"), lambda x: f"{load_percent(x):.0f}", "%"),
                     _fmt(g("Present_Current"), lambda x: f"{milliamps(x):.0f}", "mA"),
                     _fmt(g("Present_Position"), str),
                     t("report.on") if g("Torque_Enable") else t("report.off"),
                     ", ".join(t(f"bit.{b}") for b in bits) or t("report.normal")))
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    return "\n".join("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip() for r in rows)


def render(s: Snapshot, findings: list[Finding], names: dict[int, str], title: str = "") -> str:
    n_p = sum(f.level == PROBLEM for f in findings)
    n_w = sum(f.level == WARN for f in findings)
    lines = []
    if title:
        lines += [f"== {title} ==", ""]
    lines.append(t("report.ok") if n_p == 0 and n_w == 0 else t("report.counts", p=n_p, w=n_w))
    lines += ["", motor_table(s, names), ""]
    for f in findings:
        if f.level == INFO:
            continue
        lines.append(f"[{t('level.' + f.level)}] {f.title}")
        if f.detail:
            lines.append(f"    {t('report.what')}: {f.detail}")
        if f.fix:
            lines.append(f"    {t('report.fix')}: {f.fix}")
        lines.append("")
    info = [f.title for f in findings if f.level == INFO]
    if info:
        lines.append(f"{t('report.note')}: " + " / ".join(info))
    return "\n".join(lines).rstrip() + "\n"


def to_json(s: Snapshot, findings: list[Finding], hold: dict | None = None, meta: dict | None = None) -> str:
    meta = {**(meta or {}), "lang": get_lang()}
    return json.dumps({"meta": meta, "snapshot": s.to_dict(),
                       "findings": [f.to_dict() for f in findings], "hold_test": hold},
                      ensure_ascii=False, indent=2, default=str)


def share_summary(s: Snapshot, findings: list[Finding], meta: dict) -> str:
    """A compact, copy-pasteable block for a GitHub issue (no file paths or port names)."""
    lines = ["```", f"bringup-check {meta.get('version', '')}  ids={s.expected}"]
    for i in s.expected:
        if i not in s.responding:
            lines.append(f"{i}: no answer")
            continue
        g = lambda k: s.get(i, k)
        lines.append(f"{i}: V={_fmt(g('Present_Voltage'), lambda x: f'{volts(x):.1f}')} "
                     f"T={_fmt(g('Present_Temperature'), str)} "
                     f"load={_fmt(g('Present_Load'), lambda x: f'{load_percent(x):.0f}')}% "
                     f"I={_fmt(g('Present_Current'), lambda x: f'{milliamps(x):.0f}')}mA "
                     f"pos={_fmt(g('Present_Position'), str)} "
                     f"lim={g('Min_Position_Limit')}-{g('Max_Position_Limit')} "
                     f"torque={g('Torque_Enable')} status={_fmt(g('Status'), lambda x: f'{x:#04x}')}")
    lines.append("findings: " + (", ".join(f"{f.code}{f.motors}" for f in findings if f.level != INFO) or "none"))
    lines.append("```")
    return "\n".join(lines)
