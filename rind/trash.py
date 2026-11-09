"""Deleting files: Recycle Bin by default, permanent if you ask for it."""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote

IS_WIN = sys.platform == "win32"


def _win_recycle(paths: list[str]) -> list[tuple[str, str]]:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    fails = []
    for p in paths:      # one at a time so one locked file doesn't take the rest down with it
        op = SHFILEOPSTRUCTW(None, FO_DELETE, os.path.abspath(p) + "\0\0", None,
                             FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc != 0 or op.fAnyOperationsAborted or os.path.exists(p):
            fails.append((p, "it's open in another program" if rc in (0x20, 0x78, 32) else f"Windows said no (code {rc:#x})"))
    return fails


def _xdg_trash(p: str):
    """freedesktop.org trash spec, enough for a home-drive file"""
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "Trash"
    (base / "files").mkdir(parents=True, exist_ok=True)
    (base / "info").mkdir(parents=True, exist_ok=True)
    name = Path(p).name
    dest, n = base / "files" / name, 1
    while dest.exists() or (base / "info" / f"{dest.name}.trashinfo").exists():
        dest = base / "files" / f"{Path(name).stem}.{n}{Path(name).suffix}"
        n += 1
    (base / "info" / f"{dest.name}.trashinfo").write_text(
        f"[Trash Info]\nPath={quote(os.path.abspath(p))}\nDeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    shutil.move(p, dest)


def delete(paths: list[str], permanent: bool = False) -> list[tuple[str, str]]:
    """Returns [(path, why)] for anything that couldn't be deleted."""
    paths = [p for p in paths if os.path.lexists(p)]
    if not paths:
        return []
    if not permanent and IS_WIN:
        return _win_recycle(paths)
    fails = []
    for p in paths:
        try:
            if permanent:
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
            else:
                _xdg_trash(p)
        except PermissionError:
            fails.append((p, "it's open in another program, or you don't have permission"))
        except Exception as ex:
            fails.append((p, str(ex)))
    return fails
