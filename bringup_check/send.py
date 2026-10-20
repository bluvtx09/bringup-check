"""Optional, opt-in sending of a result to the developer.

Nothing is sent unless the person running the tool sees exactly what would be sent and types "y".
The question is never asked without a terminal (scripts, CI, piped output), and it can be turned off
for good with --no-send or BRINGUP_CHECK_NO_SEND=1. Sending never raises and never changes what the
tool printed or its exit code: if anything goes wrong here, the tool simply says it could not send.

What is sent: servo readings, finding codes, test verdicts, the tool/Python version and the OS name.
What is never sent: port names, user names, file paths, robot ids, calibration files, raw logs.
Results go to a Google Form whose responses only the developer can see.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import urllib.parse
import urllib.request

from . import __version__
from .messages import MSG, get_lang, t

# The Google Form that collects results. Empty = the feature is off and nothing is ever asked.
FORM_ID = "1FAIpQLSfWeBFzBmJySit88HKNHlt0x6t_mPRoHtXubH_PxVEUT0LriA"  # the long id in https://docs.google.com/forms/d/e/<FORM_ID>/viewform
FIELD = "entry.883803047"  # the answer field, e.g. "entry.123456789"
TIMEOUT_S = 5.0
MAX_CHARS = 20_000

MSG["ko"].update({
    "send.intro": "이 결과를 개발자에게 익명으로 보낼 수 있습니다. 보낼 내용은 아래가 전부입니다"
                  "(포트 이름, 사용자 이름, 파일 경로, 로봇 ID는 보내지 않습니다):",
    "send.ask": "보낼까요? 진단 기준을 다듬는 데만 씁니다. [y/N]: ",
    "send.ok": "보냈습니다. 고맙습니다!",
    "send.fail": "보내지 못했습니다(인터넷 연결을 확인하세요). 위 결과에는 영향이 없습니다.",
    "send.skipped": "보내지 않았습니다. 이 질문을 끄려면 --no-send 를 붙이거나 BRINGUP_CHECK_NO_SEND=1 로 설정하세요.",
})
MSG["en"].update({
    "send.intro": "You can send this result to the developer anonymously. This is everything that would be sent "
                  "(no port names, user names, file paths or robot ids):",
    "send.ask": "Send it? It is only used to tune the diagnostics. [y/N]: ",
    "send.ok": "Sent, thank you!",
    "send.fail": "Could not send (check your internet connection). The result above is not affected.",
    "send.skipped": "Not sent. To stop this question, add --no-send or set BRINGUP_CHECK_NO_SEND=1.",
})


# --- where and whether ------------------------------------------------------------------------

def endpoint() -> tuple[str, str] | None:
    """(url, field) to send to, or None when sending is not set up."""
    url = os.environ.get("BRINGUP_CHECK_SEND_URL", "")
    field = os.environ.get("BRINGUP_CHECK_SEND_FIELD", FIELD)
    if not url and FORM_ID:
        url = f"https://docs.google.com/forms/d/e/{FORM_ID}/formResponse"
    return (url, field) if url and field else None


def turned_off(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("BRINGUP_CHECK_NO_SEND", "").strip().lower() not in ("", "0", "false", "no")


def _interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


# --- what --------------------------------------------------------------------------------------

def _num(v):
    """Keep numbers, booleans and lists/dicts of them; drop everything else (text could hold paths)."""
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return round(v, 3) if isinstance(v, float) else v
    if isinstance(v, (list, tuple)):
        out = [_num(x) for x in v]
        return [x for x in out if x is not None]
    if isinstance(v, dict):
        out = {str(k): _num(x) for k, x in v.items()}
        return {k: x for k, x in out.items() if x is not None}
    return None


def _finding(f) -> dict:
    d = {"code": f.code, "level": f.level, "motors": list(f.motors)}
    data = _num(getattr(f, "data", {}) or {})
    if data:
        d["data"] = data
    return d


def diagnosis_data(snap, findings, hold=None) -> dict:
    from .registers import load_percent, milliamps, volts
    motors = {}
    for i in snap.expected:
        if i not in snap.responding:
            continue
        g = lambda k: snap.get(i, k)
        conv = lambda k, f: None if g(k) is None else round(f(g(k)), 2)
        motors[str(i)] = _num({
            "model": snap.responding.get(i) if isinstance(snap.responding, dict) else None,
            "V": conv("Present_Voltage", volts), "T": g("Present_Temperature"),
            "load": conv("Present_Load", load_percent), "mA": conv("Present_Current", milliamps),
            "pos": g("Present_Position"), "lim": [g("Min_Position_Limit"), g("Max_Position_Limit")],
            "Tmax": g("Max_Temperature_Limit"), "Vmin": g("Min_Voltage_Limit"), "Vmax": g("Max_Voltage_Limit"),
            "mode": g("Operating_Mode"), "torque": g("Torque_Enable"), "status": g("Status"),
        })
    out = {"expected": list(snap.expected), "missing": list(snap.missing), "motors": motors,
           "protocol_errors": _num(dict(snap.protocol_errors or {})),
           "findings": [_finding(f) for f in findings]}
    if hold:
        out["hold"] = _num({k: hold.get(k) for k in ("seconds", "median_current_mA", "total_current_mA")})
    return out


def first_run_data(res) -> dict:
    return {"completed": res.completed, "stage": res.stage,
            "findings": [_finding(f) for f in res.findings],
            "trials": [_num(dict(tr.__dict__)) for tr in res.trials]}


def guard_validation_data(log_path, verdicts, notes=None) -> dict:
    from .loganalyze import load_rows, summarize
    out = {"verdicts": [{"test": v.test, "passed": bool(v.passed), "reasons": [str(r)[:200] for r in v.reasons]}
                        for v in verdicts],
           "notes": {str(k): str(v) for k, v in (notes or {}).items()}}
    rows = load_rows(log_path)
    s = summarize(rows)
    motors = {}
    for m, ms in s.motors.items():
        counts: dict[str, int] = {}
        for _, kind in ms.events:
            counts[kind] = counts.get(kind, 0) + 1
        motors[m] = _num({"cycles": ms.cycles, "max_load": ms.max_load, "s_high": ms.seconds_high,
                          "s_released": ms.seconds_released, "max_temp": ms.max_temp, "events": counts})
    out["log"] = {"duration_s": round(s.duration, 2), "mode": str(s.mode), "motors": motors}
    return out


def envelope(kind: str, data: dict) -> dict:
    return {"kind": kind, "version": __version__, "python": platform.python_version(),
            "os": platform.system(), "arch": platform.machine(), "lang": get_lang(), "data": data}


def encode(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) > MAX_CHARS:
        slim = {k: v for k, v in payload.items() if k != "data"}
        slim["truncated"] = len(text)
        text = json.dumps(slim, ensure_ascii=False, separators=(",", ":"))
    return text


def preview(payload: dict) -> str:
    """Everything in the payload, one short line per item, so people can read what they send."""
    c = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)
    lines = []

    def walk(d, depth):
        for k, v in d.items():
            pad = "  " * (depth + 1)
            if isinstance(v, dict) and v and depth < 2:
                lines.append(f"{pad}{k}:")
                walk(v, depth + 1)
            elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v) and depth < 2:
                lines.append(f"{pad}{k}:")
                lines.extend(f"{pad}  - {c(x)}" for x in v)
            else:
                lines.append(f"{pad}{k}: {c(v)}")
    walk(payload, 0)
    return "\n".join(lines)


# --- sending ------------------------------------------------------------------------------------

def post(url: str, field: str, text: str, timeout: float = TIMEOUT_S) -> bool:
    body = urllib.parse.urlencode({field: text}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": f"bringup-check/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return 200 <= r.status < 400


def offer(kind: str, data, *, enabled: bool = True, ask=None, out=None, sender=None,
          interactive: bool | None = None) -> bool:
    """Ask once whether to send; send only on "y". `data` may be a dict or a function returning one.

    Returns True only when the result was sent. Never raises.
    """
    ask, out, sender = ask or input, out or print, sender or post
    try:
        if not enabled or turned_off():
            return False
        ep = endpoint()
        if ep is None:
            return False
        if not (_interactive() if interactive is None else interactive):
            return False
        payload = envelope(kind, data() if callable(data) else data)
        text = encode(payload)
        out("")
        out(t("send.intro"))
        out(preview(json.loads(text)))
        try:
            answer = ask(t("send.ask"))
        except (EOFError, KeyboardInterrupt):
            out("")
            return False
        if str(answer).strip().lower() not in ("y", "yes"):
            out(t("send.skipped"))
            return False
        try:
            ok = bool(sender(ep[0], ep[1], text))
        except (Exception, KeyboardInterrupt):
            ok = False
        out(t("send.ok") if ok else t("send.fail"))
        return ok
    except (Exception, KeyboardInterrupt):
        return False
