"""F10: background repairs coalesce; user jobs and shutdown still publish."""
import pytest

from santhau_studio.workers.jobs import WorkerLoop


class Ok:
    published = True
    reason = None


class Failed:
    published = False
    reason = "serving busy"


@pytest.fixture
def loop(tmp_path):
    return WorkerLoop(tmp_path, publish_serving=True)


def test_background_repairs_coalesce_into_one_publication(loop, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "santhau_studio.storage.serving.build_serving_db",
        lambda *_a, **_k: (calls.append(1), Ok())[1],
    )
    for _ in range(3):
        loop._mark_snapshot_dirty()
        assert loop._maybe_publish_snapshot() is False
    loop._mark_snapshot_dirty()  # 4th change reaches the count threshold
    assert loop._maybe_publish_snapshot() is True
    assert len(calls) == 1
    assert loop.snapshot_attempts == 1
    assert loop._snapshot_dirty is False


def test_failed_publication_stays_dirty_and_retries_on_a_bounded_cadence(loop, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "santhau_studio.storage.serving.build_serving_db",
        lambda *_a, **_k: (calls.append(1), Failed())[1],
    )
    loop._mark_snapshot_dirty()
    assert loop._maybe_publish_snapshot(force=True) is False
    assert loop.snapshot_error and loop._snapshot_dirty
    assert loop._maybe_publish_snapshot() is False  # backoff, no retry every idle tick
    assert len(calls) == 1
    monkeypatch.setattr(
        "santhau_studio.storage.serving.build_serving_db",
        lambda *_a, **_k: (calls.append(1), Ok())[1],
    )
    loop._snapshot_retry_after = 0.0
    loop._snapshot_dirty_since -= 31.0  # time threshold reached
    assert loop._maybe_publish_snapshot() is True
    assert len(calls) == 2 and loop._snapshot_dirty is False


def test_shutdown_flush_publishes_one_pending_change(loop, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "santhau_studio.storage.serving.build_serving_db",
        lambda *_a, **_k: (calls.append(1), Ok())[1],
    )
    loop._mark_snapshot_dirty()
    assert loop._maybe_publish_snapshot() is False
    assert loop._maybe_publish_snapshot(force=True) is True
    assert len(calls) == 1


def test_linux_publish_disabled_never_builds_a_snapshot(tmp_path, monkeypatch):
    linux = WorkerLoop(tmp_path, publish_serving=False)
    monkeypatch.setattr(
        "santhau_studio.storage.serving.build_serving_db",
        lambda *_a, **_k: pytest.fail("Linux must not import/build serving"),
    )
    linux._mark_snapshot_dirty()
    assert linux._maybe_publish_snapshot(force=True) is False
    assert linux.snapshot_attempts == 0
