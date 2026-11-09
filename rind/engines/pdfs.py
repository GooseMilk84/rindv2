"""PDF conversion + PDF tools (PyMuPDF, pdf2docx, python-pptx, openpyxl)."""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

import pymupdf

from ..config import settings
from . import htmlconv as H
from .common import ConvertError, noop, out_path, out_folder


def open_pdf(src: str, password: str | None = None) -> pymupdf.Document:
    doc = pymupdf.open(src)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            raise ConvertError("that PDF is password protected - use Unlock first")
    return doc


def _pix_to_pil(pix):
    from PIL import Image
    mode = "RGBA" if pix.alpha else "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def to_images(src: str, fmt: str, progress=noop, dpi: int | None = None, pages=None) -> list[Path]:
    from . import images as IM
    dpi = dpi or settings.get("pdf_dpi", 200)
    doc = open_pdf(src)
    idx = pages if pages is not None else range(doc.page_count)
    idx = list(idx)
    outs = []
    folder = out_folder(src, "Pages") if len(idx) > 1 else None
    ext = "jpg" if fmt == "JPG" else fmt.lower()
    for n, i in enumerate(idx):
        progress(n / max(1, len(idx)), f"Page {i + 1} of {doc.page_count}")
        pix = doc[i].get_pixmap(dpi=dpi, alpha=False)
        if folder:
            dest = folder / f"{Path(src).stem} - page {i + 1:03d}.{ext}"
        else:
            dest = out_path(src, ext)
        if fmt in ("PNG", "JPG"):
            if fmt == "JPG":
                pix.save(str(dest), jpg_quality=settings.get("jpg_quality", 92))
            else:
                pix.save(str(dest))
        else:
            IM.save(_pix_to_pil(pix), dest, fmt)
        outs.append(dest)
    doc.close()
    return [folder] if folder else outs


def to_docx(src: str, progress=noop) -> list[Path]:
    dest = out_path(src, "docx")
    try:
        from pdf2docx import Converter
    except ImportError:
        html = to_html_str(src)
        return [H.html_to_docx(html, dest)]
    progress(0.1, "Analysing layout")
    cv = Converter(src)
    try:
        cv.convert(str(dest), multi_processing=False)
    finally:
        cv.close()
    return [dest]


def to_text(src: str) -> str:
    doc = open_pdf(src)
    parts = [p.get_text("text", sort=True) for p in doc]
    doc.close()
    return "\n\f\n".join(parts).strip() + "\n"


def to_html_str(src: str) -> str:
    doc = open_pdf(src)
    out = []
    for page in doc:
        blocks = page.get_text("dict", sort=True)["blocks"]
        sizes = [s["size"] for b in blocks for l in b.get("lines", []) for s in l["spans"]]
        body = sorted(sizes)[len(sizes) // 2] if sizes else 11
        for b in blocks:
            if b.get("type") == 1:
                try:
                    import base64
                    ext = b.get("ext", "png")
                    data = base64.b64encode(b["image"]).decode()
                    out.append(f'<p><img src="data:image/{ext};base64,{data}"/></p>')
                except Exception:
                    pass
                continue
            text, size, bold, rich = "", 0, False, []
            for l in b.get("lines", []):
                line = "".join(s["text"] for s in l["spans"])
                if l["spans"]:
                    size = max(size, max(s["size"] for s in l["spans"]))
                    bold = bold or any("bold" in s["font"].lower() or s["flags"] & 16 for s in l["spans"])
                for s in l["spans"]:
                    t = H.htmlmod.escape(s["text"])
                    css = []
                    if s["color"] not in (0, None):
                        css.append(f"color:#{s['color']:06x}")
                    if s["flags"] & 16 or "bold" in s["font"].lower():
                        css.append("font-weight:bold")
                    if s["flags"] & 2:
                        css.append("font-style:italic")
                    rich.append(f'<span style="{";".join(css)}">{t}</span>' if css and t.strip() else t)
                rich.append(" ")
                text += line + " "
            text = text.strip()
            if not text:
                continue
            esc = "".join(rich).strip()
            if size >= body * 1.6:
                out.append(f"<h1>{esc}</h1>")
            elif size >= body * 1.3:
                out.append(f"<h2>{esc}</h2>")
            elif size >= body * 1.12 and bold and len(text) < 120:
                out.append(f"<h3>{esc}</h3>")
            elif re.match(r"^[•\-–▪●◦]\s", text):
                out.append(f"<ul><li>{H.htmlmod.escape(text[2:])}</li></ul>")
            else:
                out.append(f"<p>{esc}</p>")
    doc.close()
    return re.sub(r"</ul>\s*<ul>", "", "\n".join(out))


def to_markdown(src: str) -> str:
    return H.html_to_md(to_html_str(src))


def to_xlsx(src: str, progress=noop) -> list[Path]:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    doc = open_pdf(src)
    wb = Workbook()
    wb.remove(wb.active)
    found = 0
    for i, page in enumerate(doc):
        progress(i / doc.page_count, f"Looking for tables on page {i + 1}")
        try:
            tabs = page.find_tables()
        except Exception:
            tabs = []
        for j, t in enumerate(tabs):
            rows = t.extract()
            if not rows:
                continue
            found += 1
            ws = wb.create_sheet(f"P{i + 1} T{j + 1}"[:31])
            for r, row in enumerate(rows, 1):
                for c, val in enumerate(row, 1):
                    v = (val or "").replace("\n", " ")
                    try:
                        num = float(v.replace(",", "")) if re.fullmatch(r"-?[\d,]+(\.\d+)?", v.strip()) else None
                    except ValueError:
                        num = None
                    cell = ws.cell(r, c, num if num is not None else v)
                    if r == 1:
                        cell.font = Font(bold=True)
    if not found:
        # no ruled tables: fall back to one row per text line, split on big gaps
        ws = wb.create_sheet("Text")
        r = 1
        for page in doc:
            for b in page.get_text("dict", sort=True)["blocks"]:
                for l in b.get("lines", []):
                    cells = [s["text"].strip() for s in l["spans"] if s["text"].strip()]
                    for c, v in enumerate(cells, 1):
                        ws.cell(r, c, v)
                    if cells:
                        r += 1
    doc.close()
    dest = out_path(src, "xlsx")
    wb.save(dest)
    return [dest]


def to_pptx(src: str, progress=noop) -> list[Path]:
    from pptx import Presentation
    from pptx.util import Emu
    doc = open_pdf(src)
    prs = Presentation()
    first = doc[0].rect
    prs.slide_width = Emu(int(first.width * 12700))
    prs.slide_height = Emu(int(first.height * 12700))
    blank = prs.slide_layouts[6]
    for i, page in enumerate(doc):
        progress(i / doc.page_count, f"Slide {i + 1}")
        pix = page.get_pixmap(dpi=170)
        s = prs.slides.add_slide(blank)
        s.shapes.add_picture(io.BytesIO(pix.tobytes("png")), 0, 0, prs.slide_width, prs.slide_height)
        txt = page.get_text("text").strip()
        if txt:
            s.notes_slide.notes_text_frame.text = txt[:5000]
    doc.close()
    dest = out_path(src, "pptx")
    prs.save(dest)
    return [dest]


def to_tiff(src: str, progress=noop) -> list[Path]:
    doc = open_pdf(src)
    frames = []
    for i, p in enumerate(doc):
        progress(i / doc.page_count, f"Page {i + 1}")
        frames.append(_pix_to_pil(p.get_pixmap(dpi=settings.get("pdf_dpi", 200), alpha=False)))
    doc.close()
    dest = out_path(src, "tiff")
    frames[0].save(dest, "TIFF", save_all=True, append_images=frames[1:], compression="tiff_lzw")
    return [dest]


def to_svg(src: str, progress=noop) -> list[Path]:
    doc = open_pdf(src)
    if doc.page_count == 1:
        dest = out_path(src, "svg")
        dest.write_text(doc[0].get_svg_image(text_as_path=True), encoding="utf-8")
        doc.close()
        return [dest]
    folder = out_folder(src, "SVG")
    for i, p in enumerate(doc):
        progress(i / doc.page_count, f"Page {i + 1}")
        (folder / f"page {i + 1:03d}.svg").write_text(p.get_svg_image(text_as_path=True), encoding="utf-8")
    doc.close()
    return [folder]


def convert(src: str, fmt: str, progress=noop) -> list[Path]:
    fmt = fmt.upper()
    if fmt in ("JPG", "PNG", "WEBP"):
        return to_images(src, fmt, progress)
    if fmt == "DOCX":
        return to_docx(src, progress)
    if fmt == "TXT":
        d = out_path(src, "txt")
        d.write_text(to_text(src), encoding="utf-8")
        return [d]
    if fmt == "MD":
        d = out_path(src, "md")
        d.write_text(to_markdown(src), encoding="utf-8")
        return [d]
    if fmt == "HTML":
        from .richhtml import pdf_to_layout_html
        d = out_path(src, "html")
        open_pdf(src).close()      # password check
        d.write_text(pdf_to_layout_html(src, progress), encoding="utf-8")
        return [d]
    if fmt == "EPUB":
        d = out_path(src, "epub")
        doc = open_pdf(src)
        title = doc.metadata.get("title") or Path(src).stem
        author = doc.metadata.get("author") or ""
        doc.close()
        H.html_to_epub(to_html_str(src), d, title, author)
        return [d]
    if fmt == "XLSX":
        return to_xlsx(src, progress)
    if fmt == "PPTX":
        return to_pptx(src, progress)
    if fmt == "TIFF":
        return to_tiff(src, progress)
    if fmt == "SVG":
        return to_svg(src, progress)
    if fmt == "PDF":
        return [merge([src])]
    raise ConvertError(f"PDF can't become {fmt}")


# ------------------------------------------------------------------ tools
COMPRESS_LEVELS = {
    # name: (dpi_threshold, dpi_target, jpeg quality)
    "light": (220, 200, 85),
    "balanced": (160, 144, 72),
    "strong": (110, 96, 55),
    "extreme": (80, 72, 35),
}


def _compress_once(src: str, dest: Path, level, gray=False):
    thr, tgt, q = level
    doc = open_pdf(src)
    try:
        doc.rewrite_images(dpi_threshold=thr, dpi_target=tgt, quality=q, lossy=True, lossless=True,
                           bitonal=True, color=True, gray=True, set_to_gray=gray)
    except TypeError:
        doc.rewrite_images(dpi_threshold=thr, dpi_target=tgt, quality=q)
    try:
        doc.subset_fonts()
    except Exception:
        pass
    doc.save(dest, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True, use_objstms=1)
    doc.close()


def compress(src: str, mode: str = "level", value="balanced", gray=False, progress=noop) -> Path:
    dest = out_path(src, "pdf", "Compressed")
    orig = os.path.getsize(src)
    if mode == "level":
        progress(0.2, "Recompressing images")
        _compress_once(src, dest, COMPRESS_LEVELS.get(value, COMPRESS_LEVELS["balanced"]), gray)
        return dest
    target = float(value) * 1024 if mode == "size" else orig * float(value) / 100
    ladder = [(220, 200, 85), (160, 144, 72), (130, 110, 60), (110, 96, 50), (90, 80, 40), (72, 60, 30), (60, 50, 20)]
    for i, lv in enumerate(ladder):
        progress(i / len(ladder), f"Pass {i + 1}")
        _compress_once(src, dest, lv, gray)
        if os.path.getsize(dest) <= target:
            break
    return dest


def merge(srcs: list[str], progress=noop) -> Path:
    from . import images as IM
    out = pymupdf.open()
    for i, s in enumerate(srcs):
        progress(i / len(srcs), Path(s).name)
        if s.lower().endswith(".pdf"):
            with open_pdf(s) as d:
                out.insert_pdf(d)
        else:
            im = IM.load(s)
            buf = io.BytesIO()
            IM.flatten(im).save(buf, "JPEG", quality=92)
            w, h = im.size
            pg = out.new_page(width=w * 72 / 150, height=h * 72 / 150)
            pg.insert_image(pg.rect, stream=buf.getvalue())
    dest = out_path(srcs[0], "pdf", "Merged" if len(srcs) > 1 else "Copy")
    out.save(dest, garbage=3, deflate=True)
    out.close()
    return dest


def parse_ranges(spec: str, n: int) -> list[list[int]]:
    """'1-3, 5, 8-' -> [[0,1,2],[4],[7..n-1]]"""
    groups = []
    for part in re.split(r"[;,]", spec):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            a = int(a) if a.strip() else 1
            b = int(b) if b.strip() else n
        else:
            a = b = int(part)
        a, b = max(1, a), min(n, b)
        if a <= b:
            groups.append(list(range(a - 1, b)))
    return groups


def split(src: str, mode: str = "each", value: str = "", progress=noop) -> Path:
    doc = open_pdf(src)
    n = doc.page_count
    if mode == "each":
        groups = [[i] for i in range(n)]
    elif mode == "every":
        k = max(1, int(value or 1))
        groups = [list(range(i, min(n, i + k))) for i in range(0, n, k)]
    elif mode == "half":
        groups = [list(range(0, (n + 1) // 2)), list(range((n + 1) // 2, n))]
    else:
        groups = parse_ranges(value, n)
    if not groups:
        raise ConvertError("no pages picked")
    folder = out_folder(src, "Split")
    for gi, g in enumerate(groups):
        progress(gi / len(groups), f"Part {gi + 1}")
        part = pymupdf.open()
        for i in g:
            part.insert_pdf(doc, from_page=i, to_page=i)
        label = f"page {g[0] + 1}" if len(g) == 1 else f"pages {g[0] + 1}-{g[-1] + 1}"
        part.save(folder / f"{Path(src).stem} - {label}.pdf", garbage=3, deflate=True)
        part.close()
    doc.close()
    return folder


def organize(src: str, plan: list[tuple[int, int]]) -> Path:
    """plan = [(original_index, rotation_degrees), ...] in the new order; omitted pages are deleted."""
    doc = open_pdf(src)
    out = pymupdf.open()
    for idx, rot in plan:
        out.insert_pdf(doc, from_page=idx, to_page=idx)
        if rot:
            p = out[-1]
            p.set_rotation((p.rotation + rot) % 360)
    dest = out_path(src, "pdf", "Organized")
    out.save(dest, garbage=3, deflate=True)
    out.close()
    doc.close()
    return dest


def rotate(src: str, deg: int = 90, pages=None) -> Path:
    doc = open_pdf(src)
    for i, p in enumerate(doc):
        if pages is None or i in pages:
            p.set_rotation((p.rotation + deg) % 360)
    dest = out_path(src, "pdf", "Rotated")
    doc.save(dest, garbage=3, deflate=True)
    doc.close()
    return dest


def protect(src: str, user_pw: str, owner_pw: str | None = None, allow_print=True, allow_copy=False) -> Path:
    doc = open_pdf(src)
    perm = pymupdf.PDF_PERM_ACCESSIBILITY
    if allow_print:
        perm |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
    if allow_copy:
        perm |= pymupdf.PDF_PERM_COPY
    dest = out_path(src, "pdf", "Protected")
    doc.save(dest, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=user_pw,
             owner_pw=owner_pw or (user_pw + "~owner"), permissions=perm, garbage=3, deflate=True)
    doc.close()
    return dest


def unlock(src: str, password: str) -> Path:
    doc = pymupdf.open(src)
    if doc.needs_pass and not doc.authenticate(password):
        raise ConvertError("wrong password")
    dest = out_path(src, "pdf", "Unlocked")
    doc.save(dest, encryption=pymupdf.PDF_ENCRYPT_NONE, garbage=3, deflate=True)
    doc.close()
    return dest


def extract_images(src: str, progress=noop) -> Path:
    doc = open_pdf(src)
    folder = out_folder(src, "Images")
    seen = set()
    n = 0
    for pi, page in enumerate(doc):
        progress(pi / doc.page_count, f"Page {pi + 1}")
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            if not info or info.get("width", 0) < 16:
                continue
            n += 1
            ext = info.get("ext", "png")
            if ext in ("jpx", "jb2", "jbig2"):
                pix = pymupdf.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                pix.save(str(folder / f"page {pi + 1} - image {n}.png"))
            else:
                (folder / f"page {pi + 1} - image {n}.{ext}").write_bytes(info["image"])
    doc.close()
    if n == 0:
        folder.rmdir()
        raise ConvertError("no images in that PDF")
    return folder


META_KEYS = ["title", "author", "subject", "keywords", "creator", "producer"]


def read_metadata(src: str) -> dict:
    doc = pymupdf.open(src)
    m = dict(doc.metadata or {})
    m["pages"] = str(doc.page_count)
    m["encrypted"] = "yes" if doc.is_encrypted else "no"
    try:
        r = doc[0].rect
        m["page size"] = f"{r.width:.0f} × {r.height:.0f} pt"
    except Exception:
        pass
    m["file size"] = f"{os.path.getsize(src) / 1024:.1f} KB"
    doc.close()
    return {k: (v or "") for k, v in m.items()}


def write_metadata(src: str, values: dict | None, strip=False) -> Path:
    doc = open_pdf(src)
    if strip:
        doc.set_metadata({})
        try:
            doc.del_xml_metadata()
        except Exception:
            pass
    else:
        m = dict(doc.metadata or {})
        for k in META_KEYS:
            if values and k in values:
                m[k] = values[k]
        doc.set_metadata(m)
    dest = out_path(src, "pdf", "No Metadata" if strip else "Edited")
    doc.save(dest, garbage=3, deflate=True)
    doc.close()
    return dest


REDACT_PRESETS = {
    "Emails": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "Phone numbers": r"(?:\+?\d[\d\s().-]{7,}\d)",
    "Card numbers": r"\b(?:\d[ -]?){13,19}\b",
    "IBANs": r"\b[A-Z]{2}\d{2}[A-Z0-9 ]{11,30}\b",
    "URLs": r"https?://\S+",
    "Dates": r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b",
}


def redact(src: str, terms: list[str], patterns: list[str], areas: dict | None = None,
           fill=(0, 0, 0), progress=noop) -> tuple[Path, int]:
    """Real redaction: content under the boxes is removed, not just covered."""
    doc = open_pdf(src)
    total = 0
    regs = [re.compile(p) for p in patterns]
    for pi, page in enumerate(doc):
        progress(pi / doc.page_count, f"Page {pi + 1}")
        hits = []
        for t in terms:
            if t.strip():
                hits += page.search_for(t.strip())
        if regs:
            text = page.get_text("text")
            for rg in regs:
                for m in set(rg.findall(text)):
                    if isinstance(m, tuple):
                        m = m[0]
                    if m.strip():
                        hits += page.search_for(m.strip())
        for r in (areas or {}).get(pi, []):
            hits.append(pymupdf.Rect(*r))
        for r in hits:
            page.add_redact_annot(r, fill=fill)
        if hits:
            total += len(hits)
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS if hasattr(pymupdf, "PDF_REDACT_IMAGE_PIXELS") else 2)
    dest = out_path(src, "pdf", "Redacted")
    doc.save(dest, garbage=4, deflate=True, clean=True)
    doc.close()
    return dest, total


def watermark(src: str, text: str, opacity: float = 0.18, size: int = 54, color=(0.6, 0.6, 0.6)) -> Path:
    doc = open_pdf(src)
    for page in doc:
        r = page.rect
        tw = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
        cx, cy = r.width / 2, r.height / 2
        m = pymupdf.Matrix(1, 1).prerotate(35)
        pt = pymupdf.Point(cx - tw / 2, cy + size / 3)
        page.insert_text(pt, text, fontsize=size, fontname="helv", color=color, fill_opacity=opacity,
                         stroke_opacity=opacity, morph=(pymupdf.Point(cx, cy), m), overlay=True)
    dest = out_path(src, "pdf", "Watermarked")
    doc.save(dest, garbage=3, deflate=True)
    doc.close()
    return dest


def page_numbers(src: str, fmt: str = "{n} / {total}", pos: str = "bottom-center", size: int = 10, start: int = 1) -> Path:
    doc = open_pdf(src)
    total = doc.page_count
    for i, page in enumerate(doc):
        label = fmt.format(n=i + start, total=total + start - 1)
        r = page.rect
        tw = pymupdf.get_text_length(label, fontname="helv", fontsize=size)
        y = r.height - 24 if pos.startswith("bottom") else 30
        if pos.endswith("center"):
            x = (r.width - tw) / 2
        elif pos.endswith("right"):
            x = r.width - tw - 36
        else:
            x = 36
        page.insert_text((x, y), label, fontsize=size, fontname="helv", color=(0.25, 0.25, 0.25), overlay=True)
    dest = out_path(src, "pdf", "Numbered")
    doc.save(dest, garbage=3, deflate=True)
    doc.close()
    return dest


def thumbnails(src: str, width: int = 140):
    """yield (index, PIL image) for the organize dialog"""
    doc = open_pdf(src)
    for i, p in enumerate(doc):
        zoom = width / p.rect.width
        pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        yield i, _pix_to_pil(pix)
    doc.close()


def render_page(src: str, index: int, max_side: int = 1100):
    doc = open_pdf(src)
    p = doc[index]
    zoom = max_side / max(p.rect.width, p.rect.height)
    pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    im = _pix_to_pil(pix)
    n = doc.page_count
    rect = (p.rect.width, p.rect.height)
    doc.close()
    return im, zoom, n, rect
