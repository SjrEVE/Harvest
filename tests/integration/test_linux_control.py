import json
import os
import socket
import sys
import threading

import pytest

from santhau_studio.linux.control import ControlServer, request, socket_path

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux Unix socket / peer UID")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    root = tmp_path / "root"
    service = ControlServer(root, lambda value, uid: {"ok": True, "echo": value, "uid": uid})
    stop = threading.Event()
    thread = threading.Thread(target=service.serve, args=(stop,))
    thread.start()
    try:
        yield root, service
    finally:
        stop.set()
        thread.join(4)
        service.close()


def test_control_is_private_and_duplicate_listener_cannot_steal_it(server):
    root, service = server
    assert request(root, {"op": "ping"})["ok"]
    assert service.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises((OSError, RuntimeError)):
        ControlServer(root, lambda *_: {})
    assert request(root, {"op": "ping"})["ok"]


def test_malformed_message_and_wrong_uid_fail_closed(server, monkeypatch):
    from santhau_studio.linux import control

    root, _ = server
    with socket.socket(socket.AF_UNIX) as client:
        client.connect(str(socket_path(root)))
        client.sendall(b"[]\n")
        assert json.loads(client.recv(4096))["ok"] is False
    monkeypatch.setattr(control, "peer_uid", lambda _: os.getuid() + 1)
    with pytest.raises(PermissionError):
        request(root, {"op": "ping"})


def test_socket_name_uses_case_sensitive_root(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    assert socket_path(tmp_path / "A") != socket_path(tmp_path / "a")


def test_insecure_runtime_directory_is_rejected(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "santhau-collector").mkdir(mode=0o777)
    (runtime / "santhau-collector").chmod(0o777)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    with pytest.raises(PermissionError):
        socket_path(tmp_path / "root")
