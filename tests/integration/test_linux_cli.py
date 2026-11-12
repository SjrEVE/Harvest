import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


def command(*args, timeout=30):
    return subprocess.run(
        [sys.executable, "-m", "santhau_studio.collector_main", *map(str, args)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def test_cli_self_test_needs_no_qt_and_preserves_rerun_counts(tmp_path):
    result = command("self-test", "--data-root", tmp_path / "offline")
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["counts"]["source_records"] == 384
    assert not value["forbidden_imports"] and not value["serving_created"]


def test_cli_init_and_enqueue_do_not_start_collection(tmp_path):
    result = command("init", "--data-root", tmp_path / "real")
    assert result.returncode == 0, result.stdout + result.stderr
    result = command(
        "enqueue", "--data-root", tmp_path / "real", "--from", "2026-01-01", "--to", "2026-01-10"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["cadence"] == "WEEK"
    assert not list((tmp_path / "real").glob("serving*"))


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux worker and Unix IPC")
def test_linux_process_pause_resume_sigterm_and_restart(tmp_path, monkeypatch):
    from santhau_studio.linux import fixture
    from santhau_studio.linux.config import make_jobs
    from santhau_studio.linux.control import request
    from santhau_studio.workers.jobs import enqueue_job

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    root = tmp_path / "data"
    fixture.initialize_fixture(root)
    job = make_jobs(root, first="2026-01-01", last="2026-01-14")[0]
    enqueue_job(root, job)

    def launch():
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "santhau_studio.collector_main",
                "run",
                "--data-root",
                str(root),
                "--offline-test",
            ],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    def wait(check, timeout=15):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if process.poll() is not None:
                raise AssertionError(f"Collector exited {process.returncode}: {process.stderr.read().decode(errors='replace')}")
            try:
                value = check()
                if value:
                    return value
            except (OSError, ValueError):
                pass
            time.sleep(0.02)
        raise AssertionError(f"Timed out waiting for Linux collector state: {request(root, {'op': 'status'})}")

    process = launch()
    try:
        state = wait(lambda: request(root, {"op": "status"}).get("progress") and request(root, {"op": "status"}))
        assert state["runtime"]["phase"] == "COLLECTING"
        assert request(root, {"op": "command", "command": "PAUSE", "job_id": job.job_id})["ok"]
        wait(lambda: job.job_id in request(root, {"op": "status"})["paused_jobs"])
        wait(lambda: request(root, {"op": "status"})["runtime"]["phase"] == "IDLE")
        assert request(root, {"op": "status"})["progress"] == {}
        assert request(root, {"op": "command", "command": "RESUME", "job_id": job.job_id})["ok"]
        wait(lambda: request(root, {"op": "status"}).get("job_id") == job.job_id)
        process.send_signal(signal.SIGTERM)
        assert process.wait(10) == 0
        assert len(list((root / "jobs/queued").glob("*.json"))) == 1
        process = launch()
        wait(lambda: request(root, {"op": "status"}).get("job_id") == job.job_id)
        process.kill()
        process.wait(5)  # real SIGKILL; recovery must reuse committed proof
        process = launch()
        # 14 days x 6 families x 4 pages include 8.4 seconds of intentional pacing,
        # in addition to cold process startup, fsync and crash recovery on hosted runners.
        wait(lambda: len(list((root / "jobs/done").glob("*.json"))) == 1, timeout=45)
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(root / "warehouse.sqlite")) as conn:
            assert conn.execute("SELECT status FROM runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1", (job.job_id,)).fetchone()[0] == "COMPLETED"
            assert conn.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 672
            assert conn.execute("SELECT COUNT(*) FROM coverage_proofs").fetchone()[0] == 84
        assert not list(root.glob("serving*"))
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(10)
