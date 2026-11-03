r"""Named-pipe transport with per-connection SID authentication (§2.4).

Rules this module implements, in order of what they protect:

1. **SDDL owner-only.** The security descriptor grants GENERIC_ALL to exactly one SID string.
   A peer that cannot pass it never gets a handle, so it can neither talk nor connect-storm.
2. **PID is not identity.** `GetNamedPipeClientProcessId` only names a process; the handle is
   authenticated by opening that process's token and comparing the *user SID* (§2.4 rule 2).
   Any failure along PID → OpenProcess → token → SID refuses the connection — never accepts it.
3. **First-instance claim.** The listener is created with FILE_FLAG_FIRST_PIPE_INSTANCE, so a
   second bind of the same name (another worker, or an impersonating squat) fails loudly
   instead of queueing behind the impostor. This detects the conflict; rule 1 and 2 do the
   actual guarding.
4. **No network.** Everything is `\\.\pipe\...` on the local machine — there is no bind call in
   this file and there must never be one.

Framing is line-delimited JSON over a byte-mode pipe; nothing here interprets payloads beyond
"one object per line".
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

if sys.platform != "win32":  # pragma: no cover - the product ships on Windows only
    raise ImportError("santhau_studio.workers.ipc is Windows-only")

import ctypes
import getpass
from pathlib import Path

from santhau_studio.platform_win.process import sid_from_token

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
PIPE_ACCESS_DUPLEX = 0x3
PIPE_TYPE_BYTE = 0x0
PIPE_READMODE_BYTE = 0x0
PIPE_WAIT = 0x0
PIPE_REJECT_REMOTE_CLIENTS = 0x8
PIPE_UNLIMITED_INSTANCES = 255
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
ERROR_ACCESS_DENIED = 5
ERROR_PIPE_BUSY = 231
ERROR_MORE_DATA = 234
ERROR_NO_DATA = 232
ERROR_PIPE_CONNECTED = 535
SDDL_REVISION_1 = 1
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
PIPE_BUFFER_BYTES = 64 * 1024
MAX_MESSAGE_BYTES = 1 << 20
READ_CHUNK = 64 * 1024


class _SecurityAttributes(ctypes.Structure):
    _fields_ = (
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    )


kernel32.CreateNamedPipeW.argtypes = (
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
)
kernel32.CreateNamedPipeW.restype = wintypes.HANDLE
kernel32.ConnectNamedPipe.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
kernel32.ConnectNamedPipe.restype = wintypes.BOOL
kernel32.DisconnectNamedPipe.argtypes = (wintypes.HANDLE,)
kernel32.DisconnectNamedPipe.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.PeekNamedPipe.argtypes = (
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
)
kernel32.PeekNamedPipe.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = (wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
kernel32.FlushFileBuffers.restype = wintypes.BOOL
kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.WaitNamedPipeW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
kernel32.WaitNamedPipeW.restype = wintypes.BOOL
kernel32.GetNamedPipeClientProcessId.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
kernel32.GetNamedPipeClientProcessId.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.LocalFree.argtypes = (wintypes.HANDLE,)
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD),
)
advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL


class PipeTransportError(RuntimeError):
    """The named-pipe transport failed in a way the caller should see."""


class PipeNameConflictError(PipeTransportError):
    """Another process already owns this pipe name: a second worker, or a squatter."""


class PipeClosedError(PipeTransportError):
    """The peer closed the pipe before answering."""


Handler = Callable[[dict, str], dict]


def worker_pipe_name(user: str | None = None, *, data_root: str | Path | None = None) -> str:
    """Per-user, per-data-root control pipe name.

    Two workers on the same machine must not collide when they serve different data roots
    (§2.2 allows one writer *per root*, not per machine), so the root path is folded into the
    name. The SDDL still restricts the pipe to one user; the name never carries the path itself.
    """
    raw = user if user is not None else getpass.getuser()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:40] or "local"
    scope = ""
    if data_root is not None:
        folded = str(Path(data_root).resolve()).casefold().encode("utf-8")
        scope = "-" + hashlib.sha256(folded).hexdigest()[:12]
    return f"\\\\.\\pipe\\santhau-studio-{safe}{scope}-worker"


def client_sid(handle: wintypes.HANDLE) -> str | None:
    """The user SID of the process at the other end of `handle`, or None — never a guess."""
    pid = wintypes.ULONG()
    if not kernel32.GetNamedPipeClientProcessId(handle, ctypes.byref(pid)):
        return None
    process = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return None
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(process, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            return None
        try:
            return sid_from_token(token)
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.CloseHandle(process)


class _LineReader:
    """Accumulates pipe reads into newline-terminated JSON lines with a hard size cap."""

    def __init__(self, handle: wintypes.HANDLE, max_bytes: int = MAX_MESSAGE_BYTES, timeout_seconds: float = 5.0) -> None:
        self._handle = handle
        self._max = max_bytes
        self._buffer = b""
        self._timeout = timeout_seconds

    def read_line(self) -> bytes | None:
        """The next line without its newline, or None when the peer is gone or overflowed."""
        deadline = time.monotonic() + self._timeout
        while b"\n" not in self._buffer:
            available = wintypes.DWORD()
            if not kernel32.PeekNamedPipe(self._handle, None, 0, None, ctypes.byref(available), None):
                return None
            if not available.value:
                if time.monotonic() >= deadline:
                    return None
                time.sleep(0.005)
                continue
            chunk = self._read_chunk()
            if chunk is None:
                return None
            self._buffer += chunk
            if len(self._buffer) > self._max:
                return None
        line, _, rest = self._buffer.partition(b"\n")
        self._buffer = rest
        return line

    def _read_chunk(self) -> bytes | None:
        buffer = ctypes.create_string_buffer(READ_CHUNK)
        read = wintypes.DWORD()
        if not kernel32.ReadFile(self._handle, buffer, READ_CHUNK, ctypes.byref(read), None):
            if ctypes.get_last_error() == ERROR_MORE_DATA and read.value:
                return buffer.raw[: read.value]
            return None
        if read.value == 0:
            return None
        return buffer.raw[: read.value]


def _write_all(handle: wintypes.HANDLE, data: bytes) -> bool:
    while data:
        written = wintypes.DWORD()
        if not kernel32.WriteFile(handle, data, len(data), ctypes.byref(written), None):
            return False
        if written.value == 0:
            return False
        data = data[written.value :]
    return True  # WriteFile queues bytes; FlushFileBuffers waits indefinitely for a stalled peer


class PipeServer:
    """One listening pipe instance; each connection is authenticated then served inline."""

    def __init__(
        self,
        name: str,
        *,
        allowed_sid: str,
        handler: Handler,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
        sddl_sid: str | None = None,
    ) -> None:
        """`sddl_sid` exists so tests can split the OS-layer grant from the app-layer check.

        In production they are the same SID: the descriptor lets exactly one user connect, and
        the token check re-proves the same fact from the client process's own token.
        """
        self.name = name
        self.allowed_sid = allowed_sid.lower()
        self._handler = handler
        self._max = max_message_bytes
        descriptor = ctypes.c_void_p()
        size = wintypes.DWORD()
        sddl = f"D:P(A;;GA;;;{sddl_sid or allowed_sid})"
        if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, SDDL_REVISION_1, ctypes.byref(descriptor), ctypes.byref(size)
        ):
            raise PipeTransportError(f"cannot build security descriptor (error {ctypes.get_last_error()})")
        try:
            attributes = _SecurityAttributes(
                nLength=ctypes.sizeof(_SecurityAttributes),
                lpSecurityDescriptor=descriptor.value,
                bInheritHandle=False,
            )
            handle = kernel32.CreateNamedPipeW(
                name,
                PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                PIPE_UNLIMITED_INSTANCES,
                PIPE_BUFFER_BYTES,
                PIPE_BUFFER_BYTES,
                0,
                ctypes.byref(attributes),
            )
        finally:
            kernel32.LocalFree(descriptor)
        if not handle or handle == INVALID_HANDLE_VALUE:
            error = ctypes.get_last_error()
            if error == ERROR_ACCESS_DENIED:
                raise PipeNameConflictError(f"pipe {name} is already owned by another process")
            raise PipeTransportError(f"CreateNamedPipeW failed (error {error})")
        self._handle: wintypes.HANDLE | None = handle

    def serve_until(self, stop: threading.Event) -> None:
        """Accept, authenticate and serve connections until `stop` is set.

        A stop while blocked in ConnectNamedPipe needs `wake()` — the dummy client it admits
        is disconnected without being served.
        """
        handle = self._handle
        try:
            while not stop.is_set():
                connected = kernel32.ConnectNamedPipe(handle, None)
                if not connected:
                    error = ctypes.get_last_error()
                    if stop.is_set():
                        break
                    if error not in (ERROR_PIPE_CONNECTED, ERROR_NO_DATA):
                        raise PipeTransportError(f"ConnectNamedPipe failed (error {error})")
                if stop.is_set():
                    break
                sid = client_sid(handle)
                if sid is None or sid.lower() != self.allowed_sid:
                    # Fail closed: an unauthenticated peer gets no bytes at all, not even an error.
                    kernel32.DisconnectNamedPipe(handle)
                    continue
                try:
                    self._serve_connection(handle, sid)
                except OSError:
                    pass
                finally:
                    kernel32.DisconnectNamedPipe(handle)
        finally:
            if self._handle is not None:
                kernel32.CloseHandle(self._handle)
                self._handle = None

    def _serve_connection(self, handle: wintypes.HANDLE, sid: str) -> None:
        reader = _LineReader(handle, self._max)
        while True:
            line = reader.read_line()
            if line is None:
                return
            try:
                payload = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict):
                try:
                    response = self._handler(payload, sid)
                except Exception:
                    response = {"ok": False, "error": "INTERNAL"}
            else:
                response = {"ok": False, "error": "BAD_REQUEST"}
            if not _write_all(handle, json.dumps(response).encode("utf-8") + b"\n"):
                return

    def wake(self) -> None:
        """Unblock a serve loop waiting in ConnectNamedPipe; best-effort by design."""
        if self._handle is None:
            return
        handle = kernel32.CreateFileW(
            self.name, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
        )
        if handle and handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(handle)


class PipeClient:
    """A JSON-line client for the control pipe; one request, one response."""

    def __init__(self, handle: wintypes.HANDLE, timeout_seconds: float = 5.0) -> None:
        self._handle = handle
        self._reader = _LineReader(handle, timeout_seconds=timeout_seconds)

    @classmethod
    def connect(cls, name: str, *, timeout_seconds: float = 5.0) -> PipeClient:
        deadline = time.monotonic() + timeout_seconds
        while True:
            handle = kernel32.CreateFileW(
                name, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None
            )
            if handle and handle != INVALID_HANDLE_VALUE:
                return cls(handle, timeout_seconds=timeout_seconds)
            error = ctypes.get_last_error()
            if error != ERROR_PIPE_BUSY or time.monotonic() >= deadline:
                raise PipeTransportError(f"cannot connect to {name} (error {error})")
            kernel32.WaitNamedPipeW(name, 250)

    def request(self, payload: dict) -> dict:
        message = json.dumps(payload).encode("utf-8") + b"\n"
        if not _write_all(self._handle, message):
            raise PipeClosedError("pipe closed while sending")
        line = self._reader.read_line()
        if line is None:
            raise PipeClosedError("pipe closed before a response")
        return json.loads(line.decode("utf-8"))

    def close(self) -> None:
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None
