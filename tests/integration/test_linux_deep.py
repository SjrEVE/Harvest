import json

import pytest
from test_linux_cli import command

from santhau_studio.storage.warehouse import connect_read_only


def test_cli_deep_configuration_is_explicit_bounded_and_persistent(tmp_path):
    root = tmp_path / "data"
    assert command("init", "--data-root", root).returncode == 0
    initial = command("deep-status", "--data-root", root)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    assert not json.loads(initial.stdout)["configuration"]["enabled"]
    enabled = command("deep-config", "--enable", "--batch-requests", 6, "--data-root", root)
    assert enabled.returncode == 0, enabled.stdout + enabled.stderr
    status = json.loads(command("deep-status", "--data-root", root).stdout)
    assert status["configuration"]["enabled"] and status["configuration"]["batch_requests"] == 6
    assert status["states"] == {} and status["configuration"]["pacing_seconds"] == 3
    assert (
        command("deep-config", "--enable", "--batch-requests", 31, "--data-root", root).returncode
        == 2
    )
    assert command("deep-config", "--disable", "--data-root", root).returncode == 0
    assert not json.loads(command("deep-status", "--data-root", root).stdout)["configuration"][
        "enabled"
    ]


def test_compiled_compatible_deep_self_test_preserves_lanes_and_checkpoints(tmp_path):
    root = tmp_path / "deep"
    result = command("self-test-deep", "--data-root", root)
    assert result.returncode == 0, result.stdout + result.stderr
    value = json.loads(result.stdout)
    assert value["counts"]["deep_captures"] == 9
    assert value["counts"]["lots"] == 3 and value["counts"]["goods_rows"] == 67
    assert value["counts"]["contractor_memberships"] == 5
    assert value["counts"]["documents"] == 3 and value["counts"]["relation_edges"] == 3
    assert value["first_requests"] == 9 and value["rerun_requests"] == 0
    assert not value["serving_created"] and not value["forbidden_imports"]
    assert value["evidence_class"] == "SYNTHETIC_OFFLINE" and value["network_requests"] == 0
    conn = connect_read_only(root / "warehouse.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM goods_rows WHERE lot_id IS NULL").fetchone()[0] == 39
    assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    conn.close()
    assert command("self-test-deep", "--data-root", root).returncode == 2


def test_linux_normal_worker_has_public_fetcher_and_refuses_synthetic_live(tmp_path):
    from santhau_studio.collectors.public_deep import PublicDeepFetcher
    from santhau_studio.linux.cli import make_loop
    from santhau_studio.linux.fixture import initialize_fixture

    root = tmp_path / "synthetic"
    initialize_fixture(root)
    with pytest.raises(ValueError, match="offline"):
        make_loop(root)
    offline = make_loop(root, offline=True)
    assert offline._deep_factory is not PublicDeepFetcher
    live = make_loop(tmp_path / "real")
    assert live._deep_factory is PublicDeepFetcher
    assert live._deep_specs("TBMT", "HSMT") is not None
    assert live._deep_specs("KQLCNT", "KQLCNT_FULL") is None
    assert not live._publish_serving and live._resume_on_shutdown


def test_deep_configuration_cannot_write_while_collector_owns_root(tmp_path):
    from santhau_studio.storage.warehouse import WarehouseWriter

    root = tmp_path / "owned"
    assert command("init", "--data-root", root).returncode == 0
    writer = WarehouseWriter.open(root, role="running-collector")
    try:
        result = command("deep-config", "--enable", "--data-root", root)
        assert result.returncode == 2
        assert not writer.conn.execute("SELECT 1 FROM settings WHERE key='automatic_public_deep'").fetchone()
    finally:
        writer.close()
