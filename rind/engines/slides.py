"""Presentations: PPTX/PPT/ODP. Uses PowerPoint or LibreOffice when present,
otherwise a built-in approximate renderer so it always produces something."""
from __future__ import annotations

import io
from pathlib import Path

from .common import ConvertError, noop, out_path, temp_dir, find_soffice, soffice_convert


def powerpoint_save(src: str, dest: Path, fmt_code: int) -> bool:
    from . import office
    src_abs, dest_abs = str(Path(src).resolve()), str(Path(dest).resolve())

    def job(pp):
        pres = pp.Presentations.Open(src_abs, ReadOnly=True, Untitled=False, WithWindow=False)
        try:
            pres.SaveAs(dest_abs, fmt_code)
        finally:
            pres.Close()
        return True

    return bool(office.run("PowerPoint.Application", job)) and dest.exists()


def ensure_pptx(src: str) -> str:
    if src.lower().endswith(".pptx"):
        return src
    tmp = temp_dir() / (Path(src).stem + ".pptx")
    if powerpoint_save(src, tmp, 24):
        return str(tmp)
    if find_soffice():
        soffice_convert(src, "pptx", tmp)
        return str(tmp)
    raise ConvertError(".ppt/.odp need PowerPoint or LibreOffice installed")


# ------------------------------------------------------------------ fallback renderer
def _rgb(colorfmt, default):
    try:
        if colorfmt and colorfmt.type is not None and colorfmt.rgb is not None:
            r = colorfmt.rgb
            return (r[0], r[1], r[2])
    except Exception:
        pass
    return default


def render_slides(src: str, width: int = 1600, progress=noop):
    from PIL import Image, ImageDraw
    from pptx import Presentation
    from pptx.util import Emu
    from .images import _font
    prs = Presentation(ensure_pptx(src))
    sw, sh = prs.slide_width or Emu(9144000), prs.slide_height or Emu(6858000)
    scale = width / sw
    H = int(sh * scale)
    out = []
    for si, slide in enumerate(prs.slides):
        progress(si / max(1, len(prs.slides)), f"Slide {si + 1}")
        bg = (255, 255, 255)
        try:
            if slide.background.fill.type is not None:
                bg = _rgb(slide.background.fill.fore_color, bg)
        except Exception:
            pass
        im = Image.new("RGB", (width, H), bg)
        d = ImageDraw.Draw(im)

        def box(sh_):
            return (int((sh_.left or 0) * scale), int((sh_.top or 0) * scale),
                    int(((sh_.left or 0) + (sh_.width or 0)) * scale), int(((sh_.top or 0) + (sh_.height or 0)) * scale))

        def draw_shape(shp):
            try:
                st = shp.shape_type
            except Exception:
                st = None
            x0, y0, x1, y1 = box(shp)
            if getattr(shp, "shapes", None) is not None and st == 6:  # group
                for s in shp.shapes:
                    draw_shape(s)
                return
            if hasattr(shp, "image"):
                try:
                    pic = Image.open(io.BytesIO(shp.image.blob)).convert("RGBA")
                    if x1 - x0 > 0 and y1 - y0 > 0:
                        im.paste(pic.resize((x1 - x0, y1 - y0)), (x0, y0), pic.resize((x1 - x0, y1 - y0)))
                except Exception:
                    pass
                return
            try:
                if shp.fill.type == 1:
                    d.rounded_rectangle((x0, y0, x1, y1), radius=6, fill=_rgb(shp.fill.fore_color, (220, 220, 220)))
            except Exception:
                pass
            if getattr(shp, "has_table", False) and shp.has_table:
                rows = shp.table.rows
                ncols = len(shp.table.columns)
                rh = (y1 - y0) / max(1, len(rows))
                cw = (x1 - x0) / max(1, ncols)
                f = _font(max(10, int(rh * 0.38)))
                for ri, row in enumerate(rows):
                    for ci, cell in enumerate(row.cells):
                        cx, cy = x0 + ci * cw, y0 + ri * rh
                        d.rectangle((cx, cy, cx + cw, cy + rh), outline=(150, 150, 150))
                        d.text((cx + 6, cy + 4), cell.text[:60], font=f, fill=(20, 20, 20))
                return
            if getattr(shp, "has_text_frame", False) and shp.has_text_frame and shp.text_frame.text.strip():
                y = y0 + 4
                for para in shp.text_frame.paragraphs:
                    size = None
                    color = (25, 25, 25) if sum(bg) > 380 else (240, 240, 240)
                    bold = False
                    for r in para.runs:
                        if r.font.size:
                            size = r.font.size.pt
                        bold = bold or bool(r.font.bold)
                        try:
                            if r.font.color and r.font.color.type is not None:
                                color = _rgb(r.font.color, color)
                        except Exception:
                            pass
                    if size is None:
                        is_title = shp.is_placeholder and getattr(shp.placeholder_format, "idx", 1) == 0
                        size = 36 if is_title else 18
                    px = max(9, int(size * 12700 * scale))
                    f = _font(px)
                    text = ("• " if para.level > 0 or (shp.is_placeholder and getattr(shp.placeholder_format, "idx", 0) == 1) else "") + para.text
                    # crude word wrap
                    line = ""
                    maxw = max(20, x1 - x0 - 8 - para.level * 20)
                    for word in text.split(" "):
                        trial = (line + " " + word).strip()
                        if d.textlength(trial, font=f) > maxw and line:
                            d.text((x0 + 4 + para.level * 20, y), line, font=f, fill=color)
                            y += int(px * 1.25)
                            line = word
                        else:
                            line = trial
                    if line:
                        d.text((x0 + 4 + para.level * 20, y), line, font=f, fill=color)
                    y += int(px * 1.35)
        for shp in slide.shapes:
            draw_shape(shp)
        out.append(im)
    return out


def to_pdf(src: str, dest: Path, progress=noop) -> Path:
    if powerpoint_save(src, dest, 32):
        return dest
    if find_soffice():
        try:
            return soffice_convert(src, "pdf", dest)
        except ConvertError:
            pass
    from .images import images_to_pdf
    progress(0.1, "Rendering slides (approximate - install PowerPoint or LibreOffice for exact)")
    images_to_pdf(render_slides(src, progress=progress), dest, dpi=200)
    return dest


def text_of(src: str, notes=False) -> list[tuple[str, str]]:
    from pptx import Presentation
    prs = Presentation(ensure_pptx(src))
    out = []
    for i, s in enumerate(prs.slides, 1):
        title = ""
        parts = []
        for shp in s.shapes:
            if getattr(shp, "has_text_frame", False) and shp.has_text_frame:
                t = shp.text_frame.text.strip()
                if not t:
                    continue
                if shp.is_placeholder and getattr(shp.placeholder_format, "idx", 1) == 0 and not title:
                    title = t
                else:
                    parts.append(t)
            elif getattr(shp, "has_table", False) and shp.has_table:
                for row in shp.table.rows:
                    parts.append(" | ".join(c.text for c in row.cells))
        if notes and s.has_notes_slide:
            parts = [s.notes_slide.notes_text_frame.text.strip()]
        out.append((title or f"Slide {i}", "\n".join(parts)))
    return out


def convert(src: str, fmt: str, progress=noop) -> list[Path]:
    fmt = fmt.upper()
    if fmt == "PDF":
        return [to_pdf(src, out_path(src, "pdf"), progress)]
    if fmt == "PPTX":
        dest = out_path(src, "pptx")
        p = ensure_pptx(src)
        import shutil
        shutil.copy(p, dest)
        return [dest]
    if fmt in ("JPG", "PNG"):
        from . import pdfs
        tmpd = temp_dir() / (Path(src).stem + ".pdf")
        to_pdf(src, tmpd, progress)
        res = pdfs.to_images(str(tmpd), fmt, progress)
        # move output next to the original
        import shutil
        from .common import out_dir_for, unique, stem_of
        moved = []
        for r in res:
            if r.is_dir():
                target = unique(out_dir_for(src) / f"{stem_of(src)} Slides")
            else:
                target = out_path(src, r.suffix.lstrip("."))
            shutil.move(str(r), target)
            moved.append(target)
        return moved
    if fmt in ("TXT", "MD"):
        slides = text_of(src)
        if fmt == "TXT":
            body = "\n\n".join(f"{t}\n{'-' * len(t)}\n{b}" for t, b in slides)
        else:
            body = "\n\n".join(f"## {t}\n\n" + "\n".join(f"- {l}" for l in b.splitlines() if l.strip()) for t, b in slides)
        dest = out_path(src, fmt.lower())
        dest.write_text(body.strip() + "\n", encoding="utf-8")
        return [dest]
    raise ConvertError(f"can't make {fmt} from a presentation")


def notes(src: str) -> Path:
    slides = text_of(src, notes=True)
    dest = out_path(src, "txt", "Notes")
    dest.write_text("\n\n".join(f"{t}\n{b}" for t, b in slides if b) or "(no speaker notes)", encoding="utf-8")
    return dest


def stats(src: str) -> dict:
    from pptx import Presentation
    prs = Presentation(ensure_pptx(src))
    words = sum(len(b.split()) + len(t.split()) for t, b in text_of(src))
    pics = sum(1 for s in prs.slides for shp in s.shapes if hasattr(shp, "image"))
    cp = prs.core_properties
    return {"Slides": str(len(prs.slides)), "Words": f"{words:,}", "Pictures": str(pics),
            "Size": f"{prs.slide_width / 914400:.2f} × {prs.slide_height / 914400:.2f} in",
            "Title": cp.title or "", "Author": cp.author or "", "Modified": str(cp.modified or "")}


def read_metadata(src: str) -> dict:
    from pptx import Presentation
    cp = Presentation(ensure_pptx(src)).core_properties
    return {k: str(getattr(cp, k) or "") for k in ("title", "subject", "author", "keywords", "comments", "category", "last_modified_by")}


def write_metadata(src: str, values: dict | None, strip=False) -> Path:
    from pptx import Presentation
    prs = Presentation(ensure_pptx(src))
    cp = prs.core_properties
    for k in ("title", "subject", "author", "keywords", "comments", "category", "last_modified_by"):
        if strip:
            setattr(cp, k, "")
        elif values and k in values:
            setattr(cp, k, values[k])
    dest = out_path(src, "pptx", "No Metadata" if strip else "Edited")
    prs.save(dest)
    return dest
