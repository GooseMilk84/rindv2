"""Image conversion + image tools (Pillow, pillow-heif, rawpy, Qt for SVG)."""
from __future__ import annotations

import base64
import io
import math
import os
from pathlib import Path

from PIL import Image, ImageOps, ImageDraw, ImageFont, ImageFilter

from ..config import settings
from .common import ConvertError, noop, out_path, out_folder

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    HAVE_HEIF = True
except Exception:  # pragma: no cover
    HAVE_HEIF = False

Image.MAX_IMAGE_PIXELS = 400_000_000

PIL_FMT = {
    "JPG": "JPEG", "PNG": "PNG", "WEBP": "WEBP", "HEIC": "HEIF", "AVIF": "AVIF", "TIFF": "TIFF",
    "BMP": "BMP", "GIF": "GIF", "ICO": "ICO",
}


# ------------------------------------------------------------------ loading
def render_svg(path: str, max_side: int = 2048) -> Image.Image:
    from PySide6.QtCore import QByteArray, Qt, QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    r = QSvgRenderer(path)
    if not r.isValid():
        raise ConvertError("couldn't read that SVG")
    size = r.defaultSize()
    w, h = max(size.width(), 1), max(size.height(), 1)
    scale = max_side / max(w, h) if max(w, h) < 512 or max(w, h) > max_side else 1
    if max(w, h) < 512:
        scale = 1024 / max(w, h)
    W, H = max(1, int(w * scale)), max(1, int(h * scale))
    img = QImage(W, H, QImage.Format.Format_RGBA8888)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r.render(p, QRectF(0, 0, W, H))
    p.end()
    ptr = img.constBits()
    return Image.frombuffer("RGBA", (W, H), bytes(ptr), "raw", "RGBA", img.bytesPerLine(), 1).copy()


def load(path: str) -> Image.Image:
    ext = Path(path).suffix.lower().lstrip(".")
    if ext == "svg":
        return render_svg(path)
    if ext in ("cr2", "cr3", "nef", "arw", "dng", "orf", "rw2", "raf", "srw", "pef"):
        try:
            import rawpy
        except ImportError:
            raise ConvertError("RAW support needs the optional 'rawpy' package")
        with rawpy.imread(path) as raw:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
        return Image.fromarray(rgb)
    if ext in ("heic", "heif") and not HAVE_HEIF:
        raise ConvertError("HEIC support needs pillow-heif")
    im = Image.open(path)
    try:
        im.load()
    except OSError:
        Image.LOAD_TRUNCATED_IMAGES = True
        im.load()
    if getattr(im, "is_animated", False):
        im.seek(0)
    im = ImageOps.exif_transpose(im) or im
    return im


def has_alpha(im: Image.Image) -> bool:
    return im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)


def flatten(im: Image.Image, bg=(255, 255, 255)) -> Image.Image:
    if has_alpha(im):
        im = im.convert("RGBA")
        base = Image.new("RGB", im.size, bg)
        base.paste(im, mask=im.getchannel("A"))
        return base
    return im.convert("RGB")


def _keep_meta(src_im: Image.Image | None) -> dict:
    kw = {}
    if src_im is None:
        return kw
    icc = src_im.info.get("icc_profile")
    if icc:
        kw["icc_profile"] = icc
    exif = src_im.info.get("exif")
    if exif:
        kw["exif"] = exif
    return kw


def save(im: Image.Image, dest: Path, fmt: str, quality: int | None = None, meta_from=None, **extra) -> Path:
    fmt = fmt.upper()
    kw = _keep_meta(meta_from)
    kw.update(extra)
    if fmt == "JPG":
        q = quality or settings["jpg_quality"]
        flatten(im).save(dest, "JPEG", quality=q, optimize=True, progressive=True, subsampling=0 if q >= 90 else 2, **kw)
    elif fmt == "PNG":
        if im.mode not in ("RGB", "RGBA", "L", "LA", "P", "I;16"):
            im = im.convert("RGBA" if has_alpha(im) else "RGB")
        kw.pop("exif", None) if im.mode == "P" else None
        im.save(dest, "PNG", optimize=True, **kw)
    elif fmt == "WEBP":
        im = im.convert("RGBA" if has_alpha(im) else "RGB")
        im.save(dest, "WEBP", quality=quality or settings["webp_quality"], method=6, **kw)
    elif fmt == "HEIC":
        if not HAVE_HEIF:
            raise ConvertError("HEIC output needs pillow-heif")
        im = im.convert("RGBA" if has_alpha(im) else "RGB")
        im.save(dest, "HEIF", quality=quality or 85, **kw)
    elif fmt == "AVIF":
        im = im.convert("RGBA" if has_alpha(im) else "RGB")
        im.save(dest, "AVIF", quality=quality or settings["avif_quality"], **kw)
    elif fmt == "TIFF":
        if im.mode not in ("RGB", "RGBA", "L", "CMYK", "1"):
            im = im.convert("RGBA" if has_alpha(im) else "RGB")
        im.save(dest, "TIFF", compression="tiff_lzw", **kw)
    elif fmt == "BMP":
        im.convert("RGBA" if has_alpha(im) else "RGB").save(dest, "BMP")
    elif fmt == "GIF":
        if im.mode != "P":
            if has_alpha(im):
                rgba = im.convert("RGBA")
                p = rgba.convert("RGB").quantize(255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
                mask = rgba.getchannel("A").point(lambda a: 255 if a < 128 else 0)
                p.paste(255, mask=mask)
                p.save(dest, "GIF", transparency=255, optimize=True)
                return dest
            im = im.convert("RGB").quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
        im.save(dest, "GIF", optimize=True)
    elif fmt == "ICO":
        im = im.convert("RGBA")
        side = max(im.size)
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= max(side, 16)]
        sq.resize((256, 256) if side > 256 else (side, side), Image.Resampling.LANCZOS).save(dest, "ICO", sizes=sizes)
    elif fmt == "SVG":
        buf = io.BytesIO()
        im.convert("RGBA" if has_alpha(im) else "RGB").save(buf, "PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode()
        w, h = im.size
        dest.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<image width="{w}" height="{h}" xlink:href="data:image/png;base64,{b64}"/></svg>',
            encoding="utf-8")
    elif fmt == "PDF":
        images_to_pdf([im], dest)
    elif fmt == "DOCX":
        images_to_docx([im], dest)
    else:
        raise ConvertError(f"can't write {fmt}")
    return dest


# ------------------------------------------------------------------ multi-image helpers
def images_to_pdf(images, dest: Path, page: str = "fit", dpi: int = 150) -> Path:
    import pymupdf
    doc = pymupdf.open()
    for im in images:
        if isinstance(im, (str, Path)):
            src = str(im)
            if src.lower().endswith(".pdf"):
                with pymupdf.open(src) as other:
                    doc.insert_pdf(other)
                continue
            im = load(src)
        rgb = im.convert("RGBA") if has_alpha(im) else im.convert("RGB")
        buf = io.BytesIO()
        if rgb.mode == "RGBA":
            rgb.save(buf, "PNG")
        else:
            rgb.save(buf, "JPEG", quality=92)
        w, h = rgb.size
        if page == "a4":
            pw, ph = (595, 842) if h >= w else (842, 595)
            margin = 24
            s = min((pw - 2 * margin) / w, (ph - 2 * margin) / h)
            pg = doc.new_page(width=pw, height=ph)
            rw, rh = w * s, h * s
            r = pymupdf.Rect((pw - rw) / 2, (ph - rh) / 2, (pw + rw) / 2, (ph + rh) / 2)
        else:
            pw, ph = w * 72 / dpi, h * 72 / dpi
            pg = doc.new_page(width=pw, height=ph)
            r = pg.rect
        pg.insert_image(r, stream=buf.getvalue())
    doc.save(dest, garbage=3, deflate=True)
    doc.close()
    return dest


def images_to_docx(images, dest: Path) -> Path:
    from docx import Document
    from docx.shared import Inches
    d = Document()
    sec = d.sections[0]
    usable = (sec.page_width - sec.left_margin - sec.right_margin) / 914400  # inches
    usable_h = (sec.page_height - sec.top_margin - sec.bottom_margin) / 914400 - 0.3
    for i, im in enumerate(images):
        if isinstance(im, (str, Path)):
            im = load(str(im))
        buf = io.BytesIO()
        flatten(im).save(buf, "JPEG", quality=92) if not has_alpha(im) else im.convert("RGBA").save(buf, "PNG")
        buf.seek(0)
        w, h = im.size
        ratio = w / h
        width = min(usable, usable_h * ratio)
        d.add_picture(buf, width=Inches(width))
        if i < len(images) - 1:
            d.add_page_break()
    d.save(dest)
    return dest


# ------------------------------------------------------------------ convert entry
def convert(src: str, fmt: str, progress=noop) -> list[Path]:
    fmt = fmt.upper()
    progress(0.1, "Reading")
    im = load(src)
    meta = None
    try:
        meta = Image.open(src) if not src.lower().endswith(".svg") else None
    except Exception:
        meta = None
    dest = out_path(src, "jpg" if fmt == "JPG" else fmt.lower())
    progress(0.4, f"Writing {fmt}")
    save(im, dest, fmt, meta_from=meta)
    progress(1, "Done")
    return [dest]


def convert_animated(src: str, fmt: str, progress=noop) -> list[Path]:
    from . import media
    fmt = fmt.upper()
    if fmt in ("MP4", "WEBM"):
        return media.convert(src, fmt, progress)
    im = Image.open(src)
    frames, durs = [], []
    for i in range(getattr(im, "n_frames", 1)):
        im.seek(i)
        frames.append(im.convert("RGBA"))
        durs.append(im.info.get("duration", 80))
    dest = out_path(src, fmt.lower() if fmt != "JPG" else "jpg")
    if fmt in ("WEBP", "AVIF"):
        frames[0].save(dest, fmt, save_all=True, append_images=frames[1:], duration=durs, loop=0, quality=85)
    elif fmt == "PDF":
        images_to_pdf(frames, dest)
    else:
        save(frames[0], dest, fmt)
    return [dest]


def convert_many_to_pdf(srcs: list[str], progress=noop, page="fit") -> list[Path]:
    dest = out_path(srcs[0], "pdf", "Combined" if len(srcs) > 1 else "")
    progress(0.2, f"Combining {len(srcs)} files")
    images_to_pdf(srcs, dest, page=page)
    return [dest]


# ------------------------------------------------------------------ tools (non-interactive cores)
def encode_size(im: Image.Image, fmt: str, quality: int) -> bytes:
    buf = io.BytesIO()
    f = PIL_FMT.get(fmt, fmt)
    if f == "JPEG":
        flatten(im).save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    elif f == "PNG":
        # quality < 100 => palette quantisation (pngquant-style)
        if quality < 100:
            colors = max(16, int(256 * quality / 100))
            src = im.convert("RGBA")
            q = src.quantize(colors=colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
            q.save(buf, "PNG", optimize=True)
        else:
            im.save(buf, "PNG", optimize=True)
    elif f in ("WEBP", "AVIF", "HEIF"):
        im.convert("RGBA" if has_alpha(im) else "RGB").save(buf, f, quality=quality)
    else:
        im.save(buf, f)
    return buf.getvalue()


def compress(src: str, mode: str = "quality", value: float = 75, fmt: str | None = None,
             max_side: int | None = None, strip: bool = True, progress=noop) -> Path:
    """mode: quality (1-100) | size (target KB) | percent (target % of original)."""
    im = load(src)
    ext = Path(src).suffix.lower().lstrip(".")
    fmt = (fmt or {"jpeg": "JPG", "jpg": "JPG", "png": "PNG", "webp": "WEBP", "avif": "AVIF",
                   "heic": "HEIC", "heif": "HEIC"}.get(ext, "JPG")).upper()
    if max_side and max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    orig = os.path.getsize(src)
    if mode == "quality":
        data = encode_size(im, fmt, int(value))
    else:
        target = value * 1024 if mode == "size" else orig * value / 100
        lo, hi, best = 5, 95, None
        work = im
        for attempt in range(3):
            lo, hi = 5, 95
            while lo <= hi:
                mid = (lo + hi) // 2
                d = encode_size(work, fmt, mid)
                progress(0.2 + 0.6 * attempt / 3, f"Trying quality {mid}")
                if len(d) <= target:
                    best = d
                    lo = mid + 1
                else:
                    hi = mid - 1
            if best is not None:
                break
            work = work.resize((max(1, int(work.width * 0.75)), max(1, int(work.height * 0.75))), Image.Resampling.LANCZOS)
        data = best or encode_size(work, fmt, 5)
    ext_out = "jpg" if fmt == "JPG" else fmt.lower()
    dest = out_path(src, ext_out, "Compressed")
    dest.write_bytes(data)
    return dest


def resize(src: str, w: int | None, h: int | None, percent: float | None = None, keep=True, fmt=None) -> Path:
    im = load(src)
    if percent:
        w, h = round(im.width * percent / 100), round(im.height * percent / 100)
    elif keep:
        if w and not h:
            h = round(im.height * w / im.width)
        elif h and not w:
            w = round(im.width * h / im.height)
        elif w and h:
            s = min(w / im.width, h / im.height)
            w, h = round(im.width * s), round(im.height * s)
    w, h = max(1, int(w or im.width)), max(1, int(h or im.height))
    out = im.resize((w, h), Image.Resampling.LANCZOS)
    ext = Path(src).suffix.lstrip(".").lower()
    fmt = fmt or {"jpeg": "JPG", "jpg": "JPG", "tif": "TIFF"}.get(ext, ext.upper())
    if fmt not in PIL_FMT and fmt not in ("PDF",):
        fmt = "PNG"
    dest = out_path(src, "jpg" if fmt == "JPG" else fmt.lower(), f"{w}x{h}")
    save(out, dest, fmt, meta_from=Image.open(src) if ext != "svg" else None)
    return dest


def same_fmt(src: str) -> str:
    ext = Path(src).suffix.lstrip(".").lower()
    f = {"jpeg": "JPG", "jpg": "JPG", "jfif": "JPG", "tif": "TIFF", "heif": "HEIC"}.get(ext, ext.upper())
    return f if f in PIL_FMT else "PNG"


def save_like(src: str, im: Image.Image, suffix: str, force_png_if_alpha=True) -> Path:
    fmt = same_fmt(src)
    if force_png_if_alpha and has_alpha(im) and fmt in ("JPG", "BMP"):
        fmt = "PNG"
    dest = out_path(src, "jpg" if fmt == "JPG" else fmt.lower(), suffix)
    meta = None
    try:
        meta = Image.open(src)
    except Exception:
        pass
    save(im, dest, fmt, meta_from=meta)
    return dest


def rotate(src: str, deg: int = 90) -> Path:
    im = load(src)
    return save_like(src, im.rotate(-deg, expand=True), "Rotated")


def watermark(src: str, text: str, opacity: float = 0.35, pos: str = "tile", size_pct: float = 4) -> Path:
    im = load(src).convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fs = max(12, int(min(im.size) * size_pct / 100))
    font = _font(fs)
    alpha = int(255 * opacity)
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if pos == "tile":
        txt = Image.new("RGBA", (tw + fs * 3, th + fs * 3), (0, 0, 0, 0))
        ImageDraw.Draw(txt).text((fs, fs), text, font=font, fill=(255, 255, 255, alpha), stroke_width=max(1, fs // 18),
                                 stroke_fill=(0, 0, 0, alpha // 2))
        txt = txt.rotate(30, expand=True)
        for y in range(-txt.height, im.height + txt.height, txt.height):
            for x in range(-txt.width, im.width + txt.width, txt.width):
                layer.paste(txt, (x, y), txt)
    else:
        m = fs
        xy = {"br": (im.width - tw - m, im.height - th - m * 1.4), "bl": (m, im.height - th - m * 1.4),
              "tr": (im.width - tw - m, m), "tl": (m, m), "center": ((im.width - tw) / 2, (im.height - th) / 2)}[pos]
        d.text(xy, text, font=font, fill=(255, 255, 255, alpha), stroke_width=max(1, fs // 18), stroke_fill=(0, 0, 0, alpha // 2))
    out = Image.alpha_composite(im, layer)
    return save_like(src, out if has_alpha(load(src)) else out.convert("RGB"), "Watermarked")


def _font(size: int):
    for name in ("segoeuib.ttf", "segoeui.ttf", "arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def read_qr(src: str) -> list[str]:
    im = load(src).convert("RGB")
    try:
        import zxingcpp
        res = zxingcpp.read_barcodes(im)
        return [f"{r.format.name}: {r.text}" if hasattr(r.format, "name") else r.text for r in res]
    except ImportError:
        pass
    try:
        import cv2
        import numpy as np
        det = cv2.QRCodeDetector()
        ok, texts, _, _ = det.detectAndDecodeMulti(np.array(im)[:, :, ::-1])
        return [t for t in texts if t] if ok else []
    except Exception:
        return []


def collage(srcs: list[str], cols: int = 0, gap: int = 16, bg=(17, 17, 19), cell: int = 900) -> Path:
    ims = [load(s) for s in srcs]
    n = len(ims)
    cols = cols or math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    W = cols * cell + (cols + 1) * gap
    H = rows * cell + (rows + 1) * gap
    canvas = Image.new("RGB", (W, H), bg)
    for i, im in enumerate(ims):
        im = ImageOps.fit(flatten(im, bg), (cell, cell), Image.Resampling.LANCZOS)
        r, c = divmod(i, cols)
        canvas.paste(im, (gap + c * (cell + gap), gap + r * (cell + gap)))
    dest = out_path(srcs[0], "jpg", "Collage")
    save(canvas, dest, "JPG")
    return dest


def make_gif(srcs: list[str], fps: float = 2, max_side: int = 800, fmt="GIF") -> Path:
    frames = []
    for s in srcs:
        im = load(s).convert("RGBA")
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        frames.append(im)
    W = max(f.width for f in frames)
    H = max(f.height for f in frames)
    norm = []
    for f in frames:
        c = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        c.paste(f, ((W - f.width) // 2, (H - f.height) // 2))
        norm.append(c)
    dest = out_path(srcs[0], fmt.lower(), "Animated")
    dur = int(1000 / fps)
    if fmt == "GIF":
        pal = [flatten(n, (255, 255, 255)).quantize(256, method=Image.Quantize.MEDIANCUT) for n in norm]
        pal[0].save(dest, "GIF", save_all=True, append_images=pal[1:], duration=dur, loop=0, optimize=True)
    else:
        norm[0].save(dest, fmt, save_all=True, append_images=norm[1:], duration=dur, loop=0)
    return dest


# ------------------------------------------------------------------ metadata
def read_metadata(src: str) -> dict:
    from PIL.ExifTags import TAGS, GPSTAGS
    out = {}
    try:
        im = Image.open(src)
    except Exception:
        return out
    out["Dimensions"] = f"{im.width} × {im.height}"
    out["Format"] = im.format or ""
    out["Mode"] = im.mode
    out["File size"] = f"{os.path.getsize(src) / 1024:.1f} KB"
    try:
        exif = im.getexif()
    except Exception:
        exif = {}
    for k, v in exif.items():
        name = TAGS.get(k, str(k))
        if isinstance(v, bytes):
            continue
        out[name] = str(v)
    try:
        ifd = exif.get_ifd(0x8769)
        for k, v in ifd.items():
            if isinstance(v, bytes):
                continue
            out[TAGS.get(k, str(k))] = str(v)
    except Exception:
        pass
    try:
        gps = exif.get_ifd(0x8825)
        if gps:
            g = {GPSTAGS.get(k, k): v for k, v in gps.items()}
            lat = _dms(g.get("GPSLatitude"), g.get("GPSLatitudeRef"))
            lon = _dms(g.get("GPSLongitude"), g.get("GPSLongitudeRef"))
            if lat is not None and lon is not None:
                out["GPS"] = f"{lat:.6f}, {lon:.6f}"
    except Exception:
        pass
    for k in ("dpi", "icc_profile"):
        if k in im.info:
            out[k.upper() if k == "dpi" else "Color profile"] = str(im.info[k]) if k == "dpi" else "embedded"
    return out


def _dms(v, ref):
    if not v:
        return None
    try:
        d, m, s = [float(x) for x in v]
        val = d + m / 60 + s / 3600
        return -val if ref in ("S", "W") else val
    except Exception:
        return None


EDITABLE = {"Artist": 0x013B, "Copyright": 0x8298, "ImageDescription": 0x010E, "DateTime": 0x0132,
            "Software": 0x0131, "Make": 0x010F, "Model": 0x0110}


def write_metadata(src: str, mode: str, edits: dict | None = None) -> Path:
    """mode: strip_all | strip_gps | edit"""
    im = load(src)
    raw = Image.open(src)
    exif = raw.getexif()
    if mode == "strip_all":
        exif = Image.Exif()
        suffix = "No Metadata"
    elif mode == "strip_gps":
        if 0x8825 in exif:
            del exif[0x8825]
        suffix = "No Location"
    else:
        for k, v in (edits or {}).items():
            tag = EDITABLE.get(k)
            if tag:
                if v:
                    exif[tag] = v
                elif tag in exif:
                    del exif[tag]
        suffix = "Edited"
    exif[0x0112] = 1  # orientation already applied by load()
    fmt = same_fmt(src)
    dest = out_path(src, "jpg" if fmt == "JPG" else fmt.lower(), suffix)
    kw = {}
    if mode != "strip_all":
        kw["exif"] = exif.tobytes()
        if raw.info.get("icc_profile"):
            kw["icc_profile"] = raw.info["icc_profile"]
    save(im, dest, fmt, meta_from=None, **kw)
    return dest
