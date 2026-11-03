"""A7 — the pre-commit secret gate must actually block a commit.

Each test builds a throwaway git repository, installs the real hook through
core.hooksPath, and attempts a real `git commit`. A unit test of scan_text alone
would not prove the wiring works, which is the part that fails in practice.

secret-scan:allow-file -- this file necessarily contains secret-shaped literals; the
sandbox tests copy them into unmarked files to prove the gate still blocks them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS = REPO_ROOT / "tools"

# These literals are the gate's own test material; see the file-level directive above.
FAKE_QUERY_TOKEN = "Zk3vQ9xWm2Lp7Rt5Yh8Bn4Js"
FAKE_AWS_KEY = "AKIA" + "Q7ZXD2MKLPR4VTNB"
FAKE_PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtz\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    shutil.copy(TOOLS / "secret_scan.py", repo / "tools" / "secret_scan.py")
    shutil.copy(TOOLS / "pre_commit_secret_gate.py", repo / "tools" / "pre_commit_secret_gate.py")
    hooks = repo / "tools" / "hooks"
    hooks.mkdir()
    shutil.copy(TOOLS / "hooks" / "pre-commit", hooks / "pre-commit")

    git(repo, "init", "-q")
    git(repo, "config", "user.email", "gate-test@example.invalid")
    git(repo, "config", "user.name", "Gate Test")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.hooksPath", "tools/hooks")
    return repo


def write(repo: Path, name: str, content: str) -> Path:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def attempt_commit(repo: Path) -> subprocess.CompletedProcess[str]:
    return git(repo, "commit", "-m", "test", check=False)


def test_clean_commit_is_allowed(sandbox: Path) -> None:
    write(sandbox, "docs/notes.md", "# Notes\n\nNothing secret here, just prose about coverage.\n")
    git(sandbox, "add", "docs/notes.md")
    result = attempt_commit(sandbox)
    assert result.returncode == 0, result.stderr
    assert "secret-gate: OK" in result.stderr


SECRET_CASES = [
    (
        "url_query_token",
        "docs/a.md",
        f"progress board: https://tunnel.example/?t={FAKE_QUERY_TOKEN}\n",
        FAKE_QUERY_TOKEN,
    ),
    ("cloud_access_key", "docs/b.md", f"export AWS_ACCESS_KEY_ID={FAKE_AWS_KEY}\n", FAKE_AWS_KEY),
    (
        "private_key",
        "deploy/c.txt",
        FAKE_PRIVATE_KEY,
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtz",
    ),
    (
        "password_in_url",
        "docs/d.md",
        "postgres://svc:Su93pRkLm2xT@db.internal:5432/wh\n",
        "Su93pRkLm2xT",
    ),
    (
        "assigned_literal",
        "src/conf.py",
        'API_TOKEN = "Q7zXdmK2LpR4VtNb8Yh5Js3W"\n',
        "Q7zXdmK2LpR4VtNb8Yh5Js3W",
    ),
]


@pytest.mark.parametrize(
    ("case_id", "path", "content", "secret"), SECRET_CASES, ids=[c[0] for c in SECRET_CASES]
)
def test_secret_patterns_block_the_commit(
    sandbox: Path, case_id: str, path: str, content: str, secret: str
) -> None:
    write(sandbox, "README.md", "seed\n")
    git(sandbox, "add", "README.md")
    assert attempt_commit(sandbox).returncode == 0

    write(sandbox, path, content)
    git(sandbox, "add", "--all")
    result = attempt_commit(sandbox)
    assert result.returncode != 0, f"{case_id} was allowed through the gate"
    assert "BLOCKED" in result.stderr
    assert secret not in result.stderr, "the gate printed the secret value itself"


def test_allow_marker_is_honoured(sandbox: Path) -> None:
    write(sandbox, "tests/fixture.md", f"?t={FAKE_QUERY_TOKEN}  <!-- secret-scan:allow -->\n")
    git(sandbox, "add", "tests/fixture.md")
    result = attempt_commit(sandbox)
    assert result.returncode == 0, result.stderr


def test_file_level_directive_survives_reformatting(sandbox: Path) -> None:
    """A line marker inside a reformatted data table can drift off the secret line.

    The file-level directive is the mechanism that keeps a test-data module
    committable, so it needs its own test.
    """
    body = f"secret-scan:allow-file\n\nTOKENS = [\n    ?t={FAKE_QUERY_TOKEN},\n    {FAKE_AWS_KEY},\n]\n"
    write(sandbox, "tests/table.py", body)
    git(sandbox, "add", "tests/table.py")
    result = attempt_commit(sandbox)
    assert result.returncode == 0, result.stderr

    # Without the directive the same content is blocked.
    write(sandbox, "tests/table2.py", body.replace("secret-scan:allow-file\n\n", ""))
    git(sandbox, "add", "tests/table2.py")
    blocked = attempt_commit(sandbox)
    assert blocked.returncode != 0


def test_gate_scans_staged_blob_not_worktree(sandbox: Path) -> None:
    """Stage clean content, then dirty the worktree: the commit must still pass.

    This pins the deliberate semantics — the gate judges what is being committed.
    """
    path = write(sandbox, "docs/e.md", "clean content\n")
    git(sandbox, "add", "docs/e.md")
    path.write_text(
        f"clean content\nsecond line https://x.example/?t={FAKE_QUERY_TOKEN}\n", encoding="utf-8"
    )
    result = attempt_commit(sandbox)
    assert result.returncode == 0, result.stderr
    # ...and once the dirty line is staged, it is blocked.
    git(sandbox, "add", "docs/e.md")
    blocked = attempt_commit(sandbox)
    assert blocked.returncode != 0
    assert "BLOCKED" in blocked.stderr


def test_binary_files_are_skipped_not_crashed(sandbox: Path) -> None:
    blob = sandbox / "assets" / "thing.bin"
    blob.parent.mkdir(exist_ok=True)
    blob.write_bytes(bytes(range(256)) * 8)
    git(sandbox, "add", "assets/thing.bin")
    result = attempt_commit(sandbox)
    assert result.returncode == 0, result.stderr
