"""Archives: ZIP 7Z RAR TAR GZ BZ2 XZ ZST CAB ISO."""
from __future__ import annotations

import bz2
import gzip
import lzma
import os
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

from .common import ConvertError, noop, out_dir_for, out_path, run, stem_of, temp_dir, unique, which
from ..registry import ext_of

TAR_MODES = {"TAR": "w", "TAR.GZ": "w:gz", "TAR.BZ2": "w:bz2", "TAR.XZ": "w:xz"}


def seven_zip() -> str | None:
    p = which("7z", "7z.exe", "7za")
    if p:
        return p
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
            c = Path(base) / "7-Zip" / "7z.exe"
            if base and c.exists():
                return str(c)
    return None


def system_tar() -> str | None:
    if sys.platform == "win32":
        c = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
        if c.exists():
            return str(c)
    return which("bsdtar", "tar")


def extract(src: str, dest: Path, password: str | None = None, progress=noop) -> Path:
    e = ext_of(src)
    dest.mkdir(parents=True, exist_ok=True)
    progress(0.1, "Extracting")
    if e == "zip":
        try:
            import pyzipper
            with pyzipper.AESZipFile(src) as z:
                if password:
                    z.setpassword(password.encode())
                _safe_extract_zip(z, dest)
        except ImportError:
            with zipfile.ZipFile(src) as z:
                if password:
                    z.setpassword(password.encode())
                _safe_extract_zip(z, dest)
        except RuntimeError as ex:
            if "password" in str(ex).lower() or "encrypted" in str(ex).lower():
                raise ConvertError("that zip needs a password")
            raise
        return dest
    if e == "7z":
        import py7zr
        with py7zr.SevenZipFile(src, "r", password=password) as z:
            z.extractall(dest)
        return dest
    if e in ("tar", "tgz", "tbz2", "txz", "tar.gz", "tar.bz2", "tar.xz"):
        with tarfile.open(src) as t:
            try:
                t.extractall(dest, filter="data")
            except TypeError:
                t.extractall(dest)
        return dest
    if e in ("gz", "bz2", "xz"):
        opener = {"gz": gzip.open, "bz2": bz2.open, "xz": lzma.open}[e]
        # might be a tarball with a plain .gz name
        try:
            with tarfile.open(src) as t:
                t.extractall(dest, filter="data")
                return dest
        except Exception:
            pass
        with opener(src, "rb") as f, open(dest / Path(src).stem, "wb") as o:
            shutil.copyfileobj(f, o)
        return dest
    if e == "zst":
        try:
            import zstandard
        except ImportError:
            raise ConvertError(".zst needs the optional 'zstandard' package")
        out = dest / Path(src).stem
        with open(src, "rb") as f, open(out, "wb") as o:
            zstandard.ZstdDecompressor().copy_stream(f, o)
        if out.suffix == ".tar":
            with tarfile.open(out) as t:
                t.extractall(dest, filter="data")
            out.unlink()
        return dest
    if e == "rar":
        try:
            import rarfile
            tool = seven_zip() or which("unrar", "UnRAR.exe")
            if tool and "7z" in Path(tool).name.lower():
                rarfile.SEVENZIP_TOOL = tool
            with rarfile.RarFile(src) as r:
                if password:
                    r.setpassword(password)
                r.extractall(dest)
            return dest
        except Exception:
            pass
    if e == "cab" and sys.platform == "win32":
        r = run(["expand", src, "-F:*", str(dest)])
        if r.returncode == 0:
            return dest
    # generic: 7-Zip, then bsdtar (Windows 10+ tar.exe reads rar/iso/cab via libarchive)
    sz = seven_zip()
    if sz:
        args = [sz, "x", "-y", f"-o{dest}", src] + ([f"-p{password}"] if password else ["-p-"])
        r = run(args)
        if r.returncode == 0:
            return dest
    tar = system_tar()
    if tar:
        r = run([tar, "-xf", src, "-C", str(dest)])
        if r.returncode == 0:
            return dest
    raise ConvertError(f"can't open .{e} here - install 7-Zip for that one")


def _safe_extract_zip(z, dest: Path):
    root = dest.resolve()
    for info in z.infolist():
        name = info.filename
        target = (dest / name).resolve()
        if not str(target).startswith(str(root)):
            continue  # zip-slip
        if name.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with z.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _walk(paths: list[Path]):
    """yield (abs_path, arcname)"""
    for p in paths:
        p = Path(p)
        if p.is_dir():
            for root, _, files in os.walk(p):
                for f in files:
                    fp = Path(root) / f
                    yield fp, str(Path(p.name) / fp.relative_to(p))
        else:
            yield p, p.name


def pack(items: list[Path], fmt: str, dest: Path, password: str | None = None, level: int = 6, progress=noop) -> Path:
    fmt = fmt.upper()
    entries = list(_walk(items))
    total = len(entries) or 1
    if fmt == "ZIP":
        if password:
            import pyzipper
            with pyzipper.AESZipFile(dest, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
                z.setpassword(password.encode())
                for i, (fp, arc) in enumerate(entries):
                    progress(i / total, arc)
                    z.write(fp, arc)
        else:
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=level) as z:
                for i, (fp, arc) in enumerate(entries):
                    progress(i / total, arc)
                    z.write(fp, arc)
    elif fmt == "7Z":
        import py7zr
        with py7zr.SevenZipFile(dest, "w", password=password, header_encryption=bool(password)) as z:
            for i, (fp, arc) in enumerate(entries):
                progress(i / total, arc)
                z.write(fp, arc)
    elif fmt in TAR_MODES:
        with tarfile.open(dest, TAR_MODES[fmt]) as t:
            for i, (fp, arc) in enumerate(entries):
                progress(i / total, arc)
                t.add(fp, arc)
    else:
        raise ConvertError(f"can't pack {fmt}")
    return dest


def convert(src: str, fmt: str, progress=noop) -> list[Path]:
    tmp = temp_dir()
    try:
        extract(src, tmp, progress=lambda p, m: progress(p * 0.5, m))
        items = list(tmp.iterdir())
        dest = out_path(src, fmt.lower())
        # pack contents (not the temp folder itself)
        entries = []
        for it in items:
            entries.append(it)
        pack(entries, fmt, dest, progress=lambda p, m: progress(0.5 + p * 0.5, "Packing"))
        return [dest]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def extract_here(src: str, password=None, progress=noop) -> Path:
    folder = unique(out_dir_for(src) / stem_of(src))
    extract(src, folder, password, progress)
    # if the archive held a single top-level folder, unwrap it
    kids = list(folder.iterdir())
    if len(kids) == 1 and kids[0].is_dir():
        inner = kids[0]
        target = unique(out_dir_for(src) / inner.name) if inner.name != folder.name else None
        if target:
            shutil.move(str(inner), target)
            folder.rmdir()
            return target
        tmpn = folder.with_name(folder.name + "__tmp")
        shutil.move(str(inner), tmpn)
        folder.rmdir()
        tmpn.rename(folder)
    return folder


def contents(src: str) -> list[tuple[str, int]]:
    e = ext_of(src)
    out = []
    if e == "zip":
        with zipfile.ZipFile(src) as z:
            out = [(i.filename, i.file_size) for i in z.infolist()]
    elif e == "7z":
        import py7zr
        with py7zr.SevenZipFile(src, "r") as z:
            out = [(i.filename, i.uncompressed or 0) for i in z.list()]
    elif e in ("tar", "tgz", "tbz2", "txz", "tar.gz", "tar.bz2", "tar.xz"):
        with tarfile.open(src) as t:
            out = [(m.name, m.size) for m in t.getmembers()]
    else:
        tmp = temp_dir()
        try:
            extract(src, tmp)
            for fp, arc in _walk(list(tmp.iterdir())):
                out.append((arc, fp.stat().st_size))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return out


def test(src: str) -> str:
    e = ext_of(src)
    if e == "zip":
        with zipfile.ZipFile(src) as z:
            bad = z.testzip()
        return "All good - every file checks out." if bad is None else f"Corrupt entry: {bad}"
    if e == "7z":
        import py7zr
        with py7zr.SevenZipFile(src, "r") as z:
            ok = z.test()
        return "All good - every file checks out." if ok in (None, True) else "Archive has errors."
    tmp = temp_dir()
    try:
        extract(src, tmp)
        return "Extracted cleanly - looks fine."
    except Exception as ex:
        return f"Problem: {ex}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def protect(src: str, password: str, fmt: str = "ZIP") -> Path:
    tmp = temp_dir()
    try:
        extract(src, tmp)
        dest = out_path(src, fmt.lower(), "Protected")
        return pack(list(tmp.iterdir()), fmt, dest, password=password)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def zip_items(paths: list[str], password=None) -> Path:
    paths = list(dict.fromkeys(paths))
    first = Path(paths[0])
    name = first.name if len(paths) == 1 and first.is_dir() else (stem_of(paths[0]) if len(paths) == 1 else "Archive")
    dest = unique(out_dir_for(paths[0]) / f"{name}.zip")
    return pack([Path(p) for p in paths], "ZIP", dest, password=password)
