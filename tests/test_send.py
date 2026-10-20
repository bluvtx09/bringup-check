"""Opt-in result sending: asks only when set up and interactive, sends only on "y", never breaks the tool."""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bringup_check import cli, send
from bringup_check.checks import take_snapshot
from bringup_check.messages import set_lang
from bringup_check.scenarios import build, make_bus


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    set_lang("en")
    monkeypatch.delenv("BRINGUP_CHECK_NO_SEND", raising=False)
    monkeypatch.setenv("BRINGUP_CHECK_SEND_URL", "http://example.invalid/formResponse")
    monkeypatch.setenv("BRINGUP_CHECK_SEND_FIELD", "entry.1")


class Recorder:
    def __init__(self, ok=True, exc=None):
        self.calls, self.ok, self.exc = [], ok, exc

    def __call__(self, url, field, text):
        self.calls.append((url, field, text))
        if self.exc:
            raise self.exc
        return self.ok


def _offer(answer="y", sender=None, **kw):
    asked, printed = [], []

    def ask(prompt):
        asked.append(prompt)
        if isinstance(answer, BaseException):
            raise answer
        return answer
    sender = sender or Recorder()
    kw.setdefault("interactive", True)
    sent = send.offer("diagnosis", kw.pop("data", {"x": 1}), ask=ask, out=printed.append, sender=sender, **kw)
    return sent, asked, printed, sender


def test_sends_only_after_yes_and_shows_what_is_sent():
    sent, asked, printed, rec = _offer("y", data={"motors": {"1": {"V": 12.1}}})
    assert sent and len(asked) == 1 and len(rec.calls) == 1
    url, field, text = rec.calls[0]
    assert (url, field) == ("http://example.invalid/formResponse", "entry.1")
    payload = json.loads(text)
    assert payload["kind"] == "diagnosis" and payload["data"]["motors"]["1"]["V"] == 12.1
    shown = "\n".join(printed)
    assert '"V":12.1' in shown and "Sent" in shown


@pytest.mark.parametrize("answer", ["", "n", "no", "N "])
def test_default_is_no(answer):
    sent, asked, _, rec = _offer(answer)
    assert not sent and asked and not rec.calls


@pytest.mark.parametrize("answer", [EOFError(), KeyboardInterrupt()])
def test_closed_input_or_ctrl_c_means_no(answer):
    sent, _, _, rec = _offer(answer)
    assert not sent and not rec.calls


def test_not_asked_without_a_terminal():
    sent, asked, printed, rec = _offer("y", interactive=False)
    assert not sent and not asked and not printed and not rec.calls


def test_not_asked_when_turned_off(monkeypatch):
    sent, asked, _, _ = _offer("y", enabled=False)
    assert not sent and not asked
    monkeypatch.setenv("BRINGUP_CHECK_NO_SEND", "1")
    sent, asked, _, _ = _offer("y")
    assert not sent and not asked


def test_not_asked_when_no_form_is_set_up(monkeypatch):
    monkeypatch.delenv("BRINGUP_CHECK_SEND_URL")
    monkeypatch.setattr(send, "FORM_ID", "")
    sent, asked, printed, _ = _offer("y")
    assert not sent and not asked and not printed


@pytest.mark.parametrize("rec", [Recorder(exc=OSError("offline")), Recorder(exc=KeyboardInterrupt()),
                                 Recorder(ok=False)])
def test_send_failure_is_reported_and_swallowed(rec):
    sent, _, printed, _ = _offer("y", sender=rec)
    assert not sent and "Could not send" in "\n".join(printed)


def test_broken_data_never_raises_and_never_asks():
    def boom():
        raise RuntimeError("bug while building the payload")
    sent, asked, _, rec = _offer("y", data=boom)
    assert not sent and not asked and not rec.calls


def test_oversized_payload_is_cut_to_metadata():
    sent, _, _, rec = _offer("y", data={"blob": "x" * (send.MAX_CHARS + 10)})
    payload = json.loads(rec.calls[0][2])
    assert sent and "data" not in payload and payload["truncated"] > send.MAX_CHARS


def test_diagnosis_payload_has_readings_but_no_text_or_paths():
    sc = next(iter(build().values()))
    snap, findings, hold, _ = cli.diagnose(make_bus(sc), [1, 2, 3, 4, 5, 6], {}, sc["calib"], sc["servo_rating"],
                                           2.0 if sc["hold_test"] else 0.0, sleep=lambda _: None)
    data = send.diagnosis_data(snap, findings, hold)
    text = send.encode(send.envelope("diagnosis", data))
    assert data["motors"] and all("V" in m for m in data["motors"].values())
    assert all(set(f) <= {"code", "level", "motors", "data"} for f in data["findings"])
    assert "/dev/" not in text and "COM" not in text and len(text) < send.MAX_CHARS


def _run_cli(monkeypatch, capsys, extra=(), sender=None, answer="y"):
    sc = next(iter(build().values()))
    monkeypatch.setattr(cli, "FeetechBus", lambda port, baud: make_bus(sc))
    monkeypatch.setattr(send, "_interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    if sender:
        monkeypatch.setattr(send, "post", sender)
    rc = cli.main(["--port", "/dev/ttyFAKE", "--lang", "en", *extra])
    return rc, capsys.readouterr().out


def test_cli_result_and_exit_code_are_the_same_with_or_without_sending(monkeypatch, capsys):
    rc_off, out_off = _run_cli(monkeypatch, capsys, ["--no-send"])
    rec = Recorder(exc=OSError("offline"))
    rc_fail, out_fail = _run_cli(monkeypatch, capsys, sender=rec)
    ok = Recorder()
    rc_ok, out_ok = _run_cli(monkeypatch, capsys, sender=ok)
    assert rc_off == rc_fail == rc_ok
    assert out_fail.startswith(out_off.rstrip("\n")) and out_ok.startswith(out_off.rstrip("\n"))
    assert "Could not send" in out_fail and "Sent" in out_ok
    assert "ttyFAKE" not in ok.calls[0][2]
    assert "You can send" not in out_off


def test_first_run_offers_after_the_run(monkeypatch, capsys):
    from bringup_check.firstrun_sim import scenarios
    sc = scenarios()["healthy"]
    monkeypatch.setattr(cli, "FeetechBus", lambda port, baud: sc["make"]())
    monkeypatch.setattr("bringup_check.firstrun.time.sleep", lambda s: None, raising=False)
    seen = []
    monkeypatch.setattr(send, "offer", lambda kind, data, **kw: seen.append((kind, data(), kw)) or False)
    rc = cli.main(["--first-run", "--port", "/dev/ttyFAKE", "--yes", "--lang", "en", "--calib", "auto"])
    assert seen and seen[0][0] == "first_run" and "completed" in seen[0][1]
    assert rc in (0, 1)


class _Handler(BaseHTTPRequestHandler):
    got = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        _Handler.got.append(urllib.parse.parse_qs(body))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def test_post_reaches_a_real_http_endpoint(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/formResponse"
        monkeypatch.setenv("BRINGUP_CHECK_SEND_URL", url)
        sent = send.offer("diagnosis", {"motors": {"1": {"V": 5.0}}}, ask=lambda p: "y", out=lambda *a: None,
                          interactive=True)
    finally:
        server.shutdown()
    assert sent
    payload = json.loads(_Handler.got[-1]["entry.1"][0])
    assert payload["data"]["motors"]["1"]["V"] == 5.0


def test_guard_validation_payload(tmp_path):
    from bringup_check.loganalyze import judge, load_rows
    from test_loganalyze import squeeze_rows, write
    log = tmp_path / "guard_validation.csv"
    write(log, squeeze_rows())
    verdict = judge("gripper_squeeze", load_rows(log))
    data = send.guard_validation_data(log, [verdict], {"wrist_block": "skipped"})
    text = send.encode(send.envelope("guard_validate", data))
    assert data["verdicts"][0]["passed"] is True
    assert data["log"]["motors"]["gripper"]["events"] == {"light_hold": 1}
    assert str(tmp_path) not in text and len(text) < send.MAX_CHARS
