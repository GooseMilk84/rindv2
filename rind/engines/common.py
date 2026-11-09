"""Shared helpers for engines: output naming, subprocess, progress."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from ..config import settings

Progress = Callable[[float, str], None]


class ConvertError(Exception):
    pass


class Cancelled(Exception):
    pass


def noop(_p: float = 0, _m: str = ""):
    pass


def out_dir_for(src: str) -> Path:
    d = Path(src).resolve().parent
    if settings.get("output_mode") == "subfolder":
        d = d / settings.get("output_subfolder", "Rind")
        d.mkdir(parents=True, exist_ok=True)
    return d


def stem_of(src: str) -> str:
    name = Path(src).name
    low = name.lower()
    for multi in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"):
        if low.endswith(multi):
            return name[: -len(multi)]
    return Path(src).stem


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suf = path.name, ""
    for multi in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if path.name.lower().endswith(multi):
            stem, suf = path.name[: -len(multi)], path.name[-len(multi):]
            break
    else:
        stem, suf = path.stem, path.suffix
    i = 2
    while True:
        cand = path.with_name(f"{stem} {i}{suf}")
        if not cand.exists():
            return cand
        i += 1


def out_path(src: str, ext: str, suffix: str = "", folder: Path | None = None) -> Path:
    ext = ext.lower().lstrip(".")
    d = folder or out_dir_for(src)
    name = stem_of(src) + (f" {suffix}" if suffix else "") + "." + ext
    return unique(d / name)


def out_folder(src: str, suffix: str) -> Path:
    d = out_dir_for(src) / f"{stem_of(src)} {suffix}".strip()
    d = unique(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip() or "file"


NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(cmd, **kw)


def which(*names) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def temp_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="rind_"))


def find_soffice() -> str | None:
    p = which("soffice", "soffice.exe", "libreoffice")
    if p:
        return p
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            cand = Path(base) / "LibreOffice" / "program" / "soffice.exe"
            if cand.exists():
                return str(cand)
    return None


def soffice_convert(src: str, fmt: str, dest: Path) -> Path:
    """Convert with LibreOffice headless, if the user has it installed."""
    exe = find_soffice()
    if not exe:
        raise ConvertError("needs LibreOffice")
    tmp = temp_dir()
    r = run([exe, "--headless", "--convert-to", fmt, "--outdir", str(tmp), str(src)], timeout=600)
    outs = list(tmp.iterdir())
    if not outs:
        raise ConvertError("LibreOffice failed: " + r.stderr.decode(errors="ignore")[-300:])
    shutil.move(str(outs[0]), dest)
    shutil.rmtree(tmp, ignore_errors=True)
    return dest


def office_app(progid: str):
    """Return a COM object for Word/Excel/PowerPoint if installed (Windows only)."""
    if sys.platform != "win32" or not settings.get("use_office", True):
        return None
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        return win32com.client.DispatchEx(progid)
    except Exception:
        return None
