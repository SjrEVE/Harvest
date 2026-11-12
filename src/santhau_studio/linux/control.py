"""Owner-only Linux IPC. Unix socket; no HTTP port, token or external listener."""

import contextlib
import hashlib
import json
import os
import socket
import stat
import struct
import tempfile
from pathlib import Path

from santhau_studio.platform_win.os_lock import DataRootLock

MAX_BYTES = 65536


def socket_path(root):
    base = Path(
        os.environ.get("XDG_RUNTIME_DIR") or Path(tempfile.gettempdir()) / f"santhau-{os.getuid()}"
    )
    directory = base / "santhau-collector"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError("Thư mục điều khiển phải thuộc người dùng hiện tại và có quyền 0700.")
    name = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:24] + ".sock"
    path = directory / name
    if len(os.fsencode(path)) >= 104:
        directory = Path("/tmp") / f"santhau-{os.getuid()}"
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError("Thư mục điều khiển dự phòng không an toàn.")
        path = directory / name
    return path


def peer_uid(connection):
    return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


def receive(connection):
    data = bytearray()
    while b"\n" not in data:
        part = connection.recv(min(4096, MAX_BYTES + 1 - len(data)))
        if not part:
            raise ValueError("Kết nối đóng trước khi hoàn tất yêu cầu.")
        data.extend(part)
        if len(data) > MAX_BYTES:
            raise ValueError("Yêu cầu vượt giới hạn kích thước.")
    value = json.loads(bytes(data).split(b"\n", 1)[0])
    if not isinstance(value, dict):
        raise ValueError("Yêu cầu phải là một JSON object.")
    return value


def send(connection, value):
    data = json.dumps(value, ensure_ascii=False, default=str).encode() + b"\n"
    if len(data) > MAX_BYTES:
        data = b'{"ok":false,"error":"RESPONSE_TOO_LARGE"}\n'
    connection.sendall(data)


class ControlServer:
    def __init__(self, root, handler):
        self.path = socket_path(root)
        self.handler = handler
        self.lease = DataRootLock(self.path.parent / self.path.stem, role="control")
        self.lease.acquire()
        self.listener = None
        try:
            if self.path.exists() or self.path.is_symlink():
                info = self.path.lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise PermissionError("Không thay tệp điều khiển không hợp lệ.")
                self.path.unlink()  # lease proves no other collector owns this socket
            self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.listener.bind(str(self.path))
            self.path.chmod(0o600)
            self.listener.listen(8)
            self.listener.settimeout(0.2)
        except BaseException:
            if self.listener:
                self.listener.close()
            self.lease.release()
            raise

    def serve(self, stop):
        while not stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if stop.is_set():
                    break
                raise
            with connection:
                connection.settimeout(2)
                try:
                    if peer_uid(connection) != os.getuid():
                        raise PermissionError("Người dùng điều khiển không khớp.")
                    payload = receive(connection)
                    result = self.handler(payload, f"uid:{os.getuid()}")
                except (OSError, ValueError, TypeError, RecursionError) as exc:
                    result = {"ok": False, "error": str(exc)[:200]}
                with contextlib.suppress(OSError):
                    send(connection, result)

    def close(self):
        if self.listener:
            self.listener.close()
            self.listener = None
        self.path.unlink(missing_ok=True)
        self.lease.release()


def request(root, payload):
    path = socket_path(root)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(3)
        connection.connect(str(path))
        if peer_uid(connection) != os.getuid():
            raise PermissionError("Bộ thu thập thuộc người dùng khác.")
        send(connection, payload)
        return receive(connection)
