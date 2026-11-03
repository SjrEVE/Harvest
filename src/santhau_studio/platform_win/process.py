"""Process identity on Windows (§2.4): the current user's SID and pid liveness.

Named-pipe authentication only ever gets a PID from `GetNamedPipeClientProcessId`; everything
past that point lives here: open the process, open its token, read the user SID. Every failure
path returns None/False so the caller can fail closed (§2.4 rule 4) instead of guessing.
"""

from __future__ import annotations

import sys

if sys.platform != "win32":  # pragma: no cover - the product ships on Windows only
    raise ImportError("santhau_studio.platform_win.process is Windows-only")

import ctypes
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

ERROR_ACCESS_DENIED = 5
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
_TOKEN_USER_INFO = 1


class _SidAndAttributes(ctypes.Structure):
    _fields_ = (("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD))


class _TokenUser(ctypes.Structure):
    _fields_ = (("User", _SidAndAttributes),)


kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.LocalFree.argtypes = (wintypes.HANDLE,)
kernel32.LocalFree.restype = wintypes.HMODULE
advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = (
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
)
advapi32.GetTokenInformation.restype = wintypes.BOOL
advapi32.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL


def current_process_sid() -> str | None:
    """The SID string of the user this process runs as, or None when the token cannot be read."""
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        return sid_from_token(token)
    finally:
        kernel32.CloseHandle(token)


def sid_from_token(token: wintypes.HANDLE) -> str | None:
    """The user SID string behind an already-open token handle, or None when unreadable."""
    needed = wintypes.DWORD()
    advapi32.GetTokenInformation(token, _TOKEN_USER_INFO, None, 0, ctypes.byref(needed))
    if not needed.value:
        return None
    buffer = ctypes.create_string_buffer(needed.value)
    if not advapi32.GetTokenInformation(token, _TOKEN_USER_INFO, buffer, needed.value, ctypes.byref(needed)):
        return None
    sid = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents.User.Sid
    text = wintypes.LPWSTR()
    if not sid or not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        return None
    try:
        return text.value
    finally:
        kernel32.LocalFree(text)


def pid_alive(pid: int) -> bool:
    """True when a process with this pid exists right now.

    ACCESS_DENIED counts as alive: a protected process exists even though it cannot be opened.
    """
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return ctypes.get_last_error() == ERROR_ACCESS_DENIED
