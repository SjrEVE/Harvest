"""Restart Manager registration so uninstall/upgrade can close a running GUI gracefully.

Inno Setup's CloseApplications uses Restart Manager; RM only knows about processes that
registered the resources they hold. Without this, an uninstall while the GUI is open leaves
locked DLLs behind (observed, then fixed by registering every deployed file at startup).
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

if sys.platform != "win32":  # pragma: no cover - Windows-only product
    raise ImportError("santhau_studio.platform_win.restart_manager is Windows-only")

_rstrtmgr = ctypes.WinDLL("RstrtMgr", use_last_error=True)

ERROR_SUCCESS = 0
CCH_RM_SESSION_KEY = 32


class _UniqueProcess(ctypes.Structure):
    _fields_ = (("Process", ctypes.c_void_p),)


_rstrtmgr.RmStartSession.argtypes = (ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.c_wchar_p)
_rstrtmgr.RmStartSession.restype = wintypes.DWORD
_rstrtmgr.RmRegisterResources.argtypes = (
    wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR),
    wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p,
)
_rstrtmgr.RmRegisterResources.restype = wintypes.DWORD
_rstrtmgr.RmEndSession.argtypes = (wintypes.DWORD,)
_rstrtmgr.RmEndSession.restype = wintypes.DWORD

_sessions: list[int] = []


def register_with_restart_manager(paths: list[Path]) -> bool:
    """Register the deployed files with RM; call once at GUI startup.

    The session is kept open for the process lifetime. Registration failures are not fatal:
    the GUI works without RM, only automatic shutdown during uninstall is lost.
    """
    if _sessions:
        return True
    handle = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(CCH_RM_SESSION_KEY + 1)
    if _rstrtmgr.RmStartSession(ctypes.byref(handle), 0, key) != ERROR_SUCCESS:
        return False
    unique = [str(path) for path in paths if path.exists()]
    if not unique:
        _rstrtmgr.RmEndSession(handle)
        return False
    array = (wintypes.LPCWSTR * len(unique))(*unique)
    if _rstrtmgr.RmRegisterResources(handle, len(unique), array, 0, None, 0, None) != ERROR_SUCCESS:
        _rstrtmgr.RmEndSession(handle)
        return False
    _sessions.append(int(handle.value))
    return True


def deployed_files(executable: Path, *, limit: int = 6000) -> list[Path]:
    """Every file in the deployment folder: an upgrade replaces nested Qt/PySide6 runtimes too.

    Registering only the top-level exe/dll made Restart Manager blind to the app during
    uninstall, because Inno replaces files several directories deep.
    """
    directory = Path(executable).resolve().parent
    files: list[Path] = []
    for path in directory.rglob("*"):
        if path.is_file():
            files.append(path)
            if len(files) >= limit:
                break
    return files


def end_session() -> None:
    while _sessions:
        _rstrtmgr.RmEndSession(_sessions.pop())
