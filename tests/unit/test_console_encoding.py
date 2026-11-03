"""Vietnamese output must survive a Windows codepage console.

The packaged spike crashed on its own status print under cp1258, which is the
default console codepage for Vietnamese Windows. Every log line in the product will
carry Vietnamese, so this is pinned as a test rather than fixed by hand once.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

SNIPPET = """
import sys
sys.path.insert(0, {src!r})
from santhau_studio.app_main import force_utf8_stdout
force_utf8_stdout()
print("worker giả lập — đã chứng minh: PARTIAL, không có terminal evidence")
"""


def run_with_codepage(codepage: str, apply_fix: bool) -> subprocess.CompletedProcess[str]:
    if apply_fix:
        body = SNIPPET.format(src=str(SRC))
    else:
        body = "print('worker giả lập — đã chứng minh: PARTIAL')\n"
    env = dict(os.environ, PYTHONIOENCODING=codepage)
    return subprocess.run(
        [sys.executable, "-c", body],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )


def test_fix_prevents_crash_on_vietnamese_console() -> None:
    result = run_with_codepage("cp1258", apply_fix=True)
    assert result.returncode == 0, result.stderr
    assert "PARTIAL" in result.stdout


def test_without_the_fix_the_same_write_fails() -> None:
    """Negative control: proves the guard is what makes it work, not luck."""
    result = run_with_codepage("cp1258", apply_fix=False)
    assert result.returncode != 0
    assert "UnicodeEncodeError" in result.stderr
