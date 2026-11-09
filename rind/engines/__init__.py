"""Engine dispatch: (file kind, target format) -> converter."""
from __future__ import annotations

from pathlib import Path

from ..registry import kind_of
from .common import Cancelled, ConvertError, noop


def _engine(kind: str):
    if kind in ("image", "raw"):
        from . import images
        return images.convert
    if kind == "animated":
        from . import images
        return images.convert_animated
    if kind == "pdf":
        from . import pdfs
        return pdfs.convert
    if kind in ("word", "text", "ebook"):
        from . import docs
        return docs.convert
    if kind in ("sheet", "data"):
        from . import sheets
        return sheets.convert
    if kind == "slides":
        from . import slides
        return slides.convert
    if kind in ("video", "audio"):
        from . import media
        return media.convert
    if kind == "subtitle":
        from . import subs
        return subs.convert
    if kind == "archive":
        from . import archives
        return archives.convert
    if kind == "font":
        from . import subs
        return subs.font_convert
    raise ConvertError("no converter for that file type")


def convert(paths: list[str], fmt: str, progress=noop, cancel=None) -> list[Path]:
    kinds = {kind_of(p) for p in paths}
    if fmt == "PDF" and len(paths) > 1 and kinds <= {"image", "pdf", "raw"}:
        from . import images
        return images.convert_many_to_pdf(paths, progress)
    outs = []
    n = len(paths)
    for i, p in enumerate(paths):
        if cancel is not None and cancel.is_set():
            raise Cancelled()

        def sub(frac, msg="", i=i):
            progress((i + max(0.0, min(1.0, frac))) / n, (f"[{i + 1}/{n}] " if n > 1 else "") + (msg or ""))
        fn = _engine(kind_of(p))
        try:
            if kind_of(p) in ("video", "audio"):
                outs += fn(p, fmt, sub, cancel=cancel)
            else:
                outs += fn(p, fmt, sub)
        except (ConvertError, Cancelled):
            raise
        except Exception as ex:
            raise ConvertError(f"{Path(p).name}: {type(ex).__name__}: {ex}") from ex
    return outs
