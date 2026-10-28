"""The tool must not crash on a console that cannot show some characters.

On Windows, output redirected to a file uses the system code page (cp1252, cp949, gbk, ...), which cannot
encode characters like "→", "—" or Korean text. Unencodable characters should print as "?" instead.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "cp949", "gbk"])
@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("args", [["--demo", "all"], ["--guard-demo"], ["--first-run-demo", "all"]])
def test_demos_survive_limited_console_encodings(encoding, lang, args):
    env = dict(os.environ, PYTHONIOENCODING=encoding, BRINGUP_CHECK_NO_SEND="1")
    r = subprocess.run([sys.executable, "-m", "bringup_check.cli", *args, "--lang", lang],
                       cwd=ROOT, env=env, capture_output=True, timeout=120)
    err = r.stderr.decode(encoding, errors="replace")
    assert "Traceback" not in err and "UnicodeEncodeError" not in err, err[-500:]
    assert r.returncode in (0, 1)
    assert len(r.stdout) > 200   # it really printed the report, not just an empty screen


def test_safe_console_turns_unencodable_text_into_question_marks():
    code = ("from bringup_check.messages import safe_console; safe_console(); "
            "print('motor 3 → 4 — 7.4 V, 정상')")
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode(errors="replace")
    assert r.stdout.decode("ascii").strip() == "motor 3 ? 4 ? 7.4 V, ??"
