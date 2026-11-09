"""Interactive image tools: Crop, Add BG, Annotate, Redact, Edit."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QPushButton,
                               QSpinBox, QToolButton, QVBoxLayout, QWidget)

from .. import icons, theme
from ..engines import images as IM
from .base import Chips, LabeledSlider, ToolDialog, pil_to_qimage, qimage_to_pil, section
from .canvas import AnnotateView, BoxView, CropView, ImageView

PREVIEW_MAX = 1400


def preview_of(im: Image.Image, max_side=PREVIEW_MAX) -> tuple[Image.Image, float]:
    """-> (preview, factor) where full = preview * factor"""
    if max(im.size) <= max_side:
        return im.copy(), 1.0
    p = im.copy()
    p.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return p, im.width / p.width


RATIOS = {"Free": None, "Original": "orig", "1:1": 1, "16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4, "3:2": 3 / 2}


# ------------------------------------------------------------------ crop
class CropDialog(ToolDialog):
    def __init__(self, im: Image.Image, name: str, real_size=None, allow_rotate=True, title="Crop Image"):
        super().__init__(title, name, "Apply", reset=True)
        self.src = im
        self.real = real_size or im.size
        self.rot = 0
        self.flip_h = self.flip_v = False
        self.allow_rotate = allow_rotate
        self._build_view()
        row = QHBoxLayout()
        lab = QLabel("Aspect ratio")
        lab.setObjectName("Muted")
        row.addWidget(lab)
        self.chips = Chips(list(RATIOS.keys()), "Free")
        self.chips.changed.connect(self._ratio)
        row.addWidget(self.chips, 1)
        self.body.addLayout(row)
        row2 = QHBoxLayout()
        for t in ("W",):
            pass
        self.w_spin = QSpinBox()
        self.h_spin = QSpinBox()
        for s in (self.w_spin, self.h_spin):
            s.setRange(1, 100000)
            s.setSuffix(" px")
            s.setFixedWidth(110)
            s.editingFinished.connect(self._typed)
        lw = QLabel("W")
        lw.setObjectName("Muted")
        lh = QLabel("H")
        lh.setObjectName("Muted")
        row2.addWidget(lw)
        row2.addWidget(self.w_spin)
        row2.addSpacing(6)
        row2.addWidget(lh)
        row2.addWidget(self.h_spin)
        row2.addStretch(1)
        if allow_rotate:
            for ic, tip, fn in (("rotate", "Rotate left", lambda: self._rotate(-90)), ("reverse", "Rotate right", lambda: self._rotate(90)),
                                ("flip", "Flip horizontal", self._flip_h)):
                b = QToolButton()
                b.setIcon(icons.icon(ic, theme.TEXT, 18))
                b.setToolTip(tip)
                b.clicked.connect(fn)
                row2.addWidget(b)
        self.body.addLayout(row2)
        self.resize(760, 720)
        self._sync()

    def _working(self) -> Image.Image:
        im = self.src
        if self.rot:
            im = im.rotate(-self.rot, expand=True)
        if self.flip_h:
            im = ImageOps.mirror(im)
        if self.flip_v:
            im = ImageOps.flip(im)
        return im

    def _build_view(self):
        im = self._working()
        self.prev, self.factor = preview_of(im)
        # real pixels per preview pixel (video frames are previewed scaled)
        rw = self.real[0] if self.rot % 180 == 0 else self.real[1]
        self.factor = rw / self.prev.width
        if hasattr(self, "view"):
            self.view.set_image(pil_to_qimage(self.prev))
            self.view.pm = QPixmap.fromImage(pil_to_qimage(self.prev))
            self.view.reset()
        else:
            self.view = CropView(pil_to_qimage(self.prev))
            self.view.changed.connect(self._sync)
            self.body.addWidget(self.view, 1)

    def _rotate(self, d):
        self.rot = (self.rot + d) % 360
        self._build_view()
        self._sync()

    def _flip_h(self):
        self.flip_h = not self.flip_h
        self._build_view()
        self._sync()

    def _ratio(self, key):
        r = RATIOS[key]
        if r == "orig":
            r = self.prev.width / self.prev.height
        self.view.set_ratio(r)

    def _sync(self):
        c = self.view.crop
        for s in (self.w_spin, self.h_spin):
            s.blockSignals(True)
        self.w_spin.setValue(max(1, round(c.width() * self.factor)))
        self.h_spin.setValue(max(1, round(c.height() * self.factor)))
        for s in (self.w_spin, self.h_spin):
            s.blockSignals(False)

    def _typed(self):
        self.view.set_size(self.w_spin.value() / self.factor, self.h_spin.value() / self.factor)
        self._sync()

    def on_reset(self):
        self.rot = 0
        self.flip_h = self.flip_v = False
        self._build_view()
        self.chips.set("Free")
        self._sync()

    def result(self) -> dict:
        c = self.view.crop
        f = self.factor
        return {"rect": (round(c.x() * f), round(c.y() * f), round(c.width() * f), round(c.height() * f)),
                "rotate": self.rot, "flip_h": self.flip_h, "flip_v": self.flip_v}


def apply_crop(src: str, res: dict) -> Path:
    im = IM.load(src)
    if res["rotate"]:
        im = im.rotate(-res["rotate"], expand=True)
    if res["flip_h"]:
        im = ImageOps.mirror(im)
    if res["flip_v"]:
        im = ImageOps.flip(im)
    x, y, w, h = res["rect"]
    im = im.crop((x, y, x + w, y + h))
    return IM.save_like(src, im, "Cropped")


# ------------------------------------------------------------------ add background
GRADIENTS = [
    ("#ff9a3c", "#ff3d77"), ("#f83600", "#f9d423"), ("#7f00ff", "#e100ff"), ("#2e3192", "#1bffff"),
    ("#00b09b", "#96c93d"), ("#ee9ca7", "#ffdde1"), ("#a1c4fd", "#c2e9fb"), ("#ff512f", "#dd2476"),
    ("#134e5e", "#71b280"), ("#141e30", "#243b55"), ("#ffecd2", "#fcb69f"), ("#232526", "#414345"),
]
SOLIDS = ["#ffffff", "#f2f2f7", "#d1d1d6", "#1c1c1e", "#000000", "#ff7a1a", "#ff3b30", "#007aff", "#34c759", "#af52de"]
BG_RATIOS = {"Auto": None, "1:1": 1, "4:3": 4 / 3, "16:9": 16 / 9, "9:16": 9 / 16, "4:5": 4 / 5}


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def make_background(size, spec, src_img=None) -> Image.Image:
    w, h = size
    kind, val = spec
    if kind == "solid":
        return Image.new("RGB", (w, h), _hex(val))
    if kind == "blur" and src_img is not None:
        bg = ImageOps.fit(src_img.convert("RGB"), (w, h), Image.Resampling.BILINEAR)
        bg = bg.filter(ImageFilter.GaussianBlur(max(w, h) / 25))
        return ImageEnhance.Brightness(bg).enhance(0.85)
    import numpy as np
    a, b = np.array(_hex(val[0]), dtype=np.float32), np.array(_hex(val[1]), dtype=np.float32)
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    t = ((xs / max(1, w - 1)) + (ys / max(1, h - 1))) / 2.0   # top-left -> bottom-right
    arr = a[None, None, :] * (1 - t[..., None]) + b[None, None, :] * t[..., None]
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGB")


def compose(im: Image.Image, spec, pad_pct: float, radius_pct: float, shadow: float, ratio, inset_border=False) -> Image.Image:
    im = im.convert("RGBA")
    w, h = im.size
    pad = int(max(w, h) * pad_pct / 100)
    cw, ch = w + 2 * pad, h + 2 * pad
    if ratio:
        if cw / ch < ratio:
            cw = int(ch * ratio)
        else:
            ch = int(cw / ratio)
    canvas = make_background((cw, ch), spec, im).convert("RGBA")
    x, y = (cw - w) // 2, (ch - h) // 2
    rad = int(min(w, h) / 2 * radius_pct / 100)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=rad, fill=255)
    mask = ImageChops.multiply(mask, im.getchannel("A"))
    if shadow > 0:
        blur = max(4, int(max(w, h) * 0.035 * (0.5 + shadow / 100)))
        sh = Image.new("L", (cw, ch), 0)
        off = int(blur * 0.35)
        sh.paste(mask.point(lambda v: int(v * shadow / 100 * 0.7)), (x, y + off))
        sh = sh.filter(ImageFilter.GaussianBlur(blur))
        black = Image.new("RGBA", (cw, ch), (0, 0, 0, 255))
        canvas = Image.composite(black, canvas, sh)
    canvas.paste(im, (x, y), mask)
    return canvas


class AddBgDialog(ToolDialog):
    def __init__(self, im: Image.Image, name: str, batch: int = 1):
        super().__init__("Add BG", name, "Save with Background", reset=True)
        self.src_full = im
        self.prev, _ = preview_of(im, 900)
        self.spec = ("gradient", GRADIENTS[0])
        self.view = ImageView()
        self.view.checker = False
        self.body.addWidget(self.view, 1)
        self.body.addWidget(section("Background"))
        grid = QGridLayout()
        grid.setSpacing(6)
        col = 0
        row = 0
        self.swatches = []
        for g in GRADIENTS:
            self._swatch(grid, row, col, ("gradient", g))
            col += 1
            if col == 12:
                col, row = 0, row + 1
        row, col = 1, 0
        for c in SOLIDS:
            self._swatch(grid, row, col, ("solid", c))
            col += 1
        self._swatch(grid, row, col, ("blur", None))
        col += 1
        pick = QToolButton()
        pick.setText("+")
        pick.setToolTip("Custom colour")
        pick.setFixedSize(30, 30)
        pick.clicked.connect(self._custom)
        grid.addWidget(pick, row, col)
        gw = QWidget()
        gw.setLayout(grid)
        self.body.addWidget(gw)
        self.pad = LabeledSlider("Padding", 0, 40, 10, "%")
        self.radius = LabeledSlider("Corners", 0, 50, 6, "%")
        self.shadow = LabeledSlider("Shadow", 0, 100, 45, "%")
        for s in (self.pad, self.radius, self.shadow):
            s.changed.connect(self._later)
            self.body.addWidget(s)
        r = QHBoxLayout()
        lab = QLabel("Size")
        lab.setObjectName("Muted")
        lab.setFixedWidth(92)
        r.addWidget(lab)
        self.ratio = Chips(list(BG_RATIOS.keys()), "Auto")
        self.ratio.changed.connect(lambda _: self._later())
        r.addWidget(self.ratio, 1)
        self.body.addLayout(r)
        self.apply_all = QCheckBox(f"Use these settings for the other {batch - 1} image(s)")
        self.apply_all.setVisible(batch > 1)
        self.apply_all.setChecked(batch > 1)
        self.body.addWidget(self.apply_all)
        self._timer = QTimer(self, singleShot=True, interval=40, timeout=self._render)
        self.resize(720, 820)
        self._render()

    def _swatch(self, grid, row, col, spec):
        b = QToolButton()
        b.setFixedSize(30, 30)
        b.setCheckable(True)
        kind, val = spec
        if kind == "gradient":
            css = f"qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {val[0]}, stop:1 {val[1]})"
        elif kind == "solid":
            css = val
        else:
            css = "qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #555, stop:1 #999)"
            b.setText("blur")
            b.setStyleSheet("font-size:9px;")
        b.setStyleSheet(b.styleSheet() + f"QToolButton {{ background: {css}; border: 1px solid #3a3a40; border-radius: 8px; }}"
                        f"QToolButton:checked {{ border: 2px solid {theme.accent()}; }}")
        b.clicked.connect(lambda _=False, s=spec, b=b: self._pick(s, b))
        self.swatches.append(b)
        if spec == self.spec:
            b.setChecked(True)
        grid.addWidget(b, row, col)

    def _pick(self, spec, btn):
        for s in self.swatches:
            s.setChecked(s is btn)
        self.spec = spec
        self._later()

    def _custom(self):
        c = QColorDialog.getColor(QColor("#ffffff"), self, "Background colour")
        if c.isValid():
            for s in self.swatches:
                s.setChecked(False)
            self.spec = ("solid", c.name())
            self._later()

    def _later(self, *_):
        self._timer.start()

    def params(self):
        return (self.spec, self.pad.value(), self.radius.value(), self.shadow.value(), BG_RATIOS[self.ratio.value()])

    def _render(self):
        out = compose(self.prev, *self.params())
        self.view.set_image(pil_to_qimage(out))

    def on_reset(self):
        self.pad.reset()
        self.radius.reset()
        self.shadow.reset()
        self.ratio.set("Auto")
        self._render()


def apply_addbg(src: str, params) -> Path:
    im = IM.load(src)
    out = compose(im, *params)
    dest = IM.out_path(src, "png", "With Background")
    IM.save(out, dest, "PNG")
    return dest


# ------------------------------------------------------------------ annotate
COLORS = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#007aff", "#af52de", "#ffffff", "#000000"]


class AnnotateDialog(ToolDialog):
    def __init__(self, im: Image.Image, name: str):
        super().__init__("Annotate", name, "Save", reset=True)
        self.full = im
        self.prev, self.factor = preview_of(im, 2200)
        self.view = AnnotateView(pil_to_qimage(self.prev))
        self.view.want_text.connect(self._text)
        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.tool_btns = {}
        for t, ic, tip in (("arrow", "arrow", "Arrow"), ("rect", "rect", "Box"), ("ellipse", "ellipse", "Circle"),
                           ("number", "number", "Numbered step"), ("text", "text", "Text"), ("pen", "annotate", "Pen"),
                           ("marker", "marker", "Highlighter")):
            b = QToolButton()
            b.setIcon(icons.icon(ic, theme.TEXT, 18))
            b.setToolTip(tip)
            b.setCheckable(True)
            b.clicked.connect(lambda _=False, t=t: self._tool(t))
            self.tool_btns[t] = b
            bar.addWidget(b)
        bar.addSpacing(10)
        self.color_btns = []
        for c in COLORS:
            b = QToolButton()
            b.setFixedSize(24, 24)
            b.setCheckable(True)
            b.setStyleSheet(f"QToolButton {{ background:{c}; border:1px solid #444; border-radius:12px; }}"
                            f"QToolButton:checked {{ border:2px solid #fff; }}")
            b.clicked.connect(lambda _=False, c=c, b=b: self._color(c, b))
            self.color_btns.append(b)
            bar.addWidget(b)
        self.color_btns[0].setChecked(True)
        bar.addSpacing(10)
        self.size = QSpinBox()
        self.size.setRange(1, 40)
        self.size.setValue(6)
        self.size.setSuffix(" px")
        self.size.setFixedWidth(80)
        self.size.valueChanged.connect(lambda v: setattr(self.view, "width_px", v))
        bar.addWidget(self.size)
        bar.addStretch(1)
        for ic, tip, fn in (("undo", "Undo (Ctrl+Z)", self.view.undo), ("reverse", "Redo", self.view.redo_)):
            b = QToolButton()
            b.setIcon(icons.icon(ic, theme.TEXT, 18))
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bar.addWidget(b)
        self.body.addLayout(bar)
        self.body.addWidget(self.view, 1)
        self._tool("arrow")
        self.resize(980, 760)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Z and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.view.redo_()
            else:
                self.view.undo()
            return
        super().keyPressEvent(e)

    def _tool(self, t):
        for k, b in self.tool_btns.items():
            b.setChecked(k == t)
        self.view.tool = t

    def _color(self, c, btn):
        for b in self.color_btns:
            b.setChecked(b is btn)
        self.view.color = c

    def _text(self, pt: QPointF):
        text, ok = QInputDialog.getText(self, "Text", "Label:")
        if ok and text:
            self.view.add_text(pt, text)

    def on_reset(self):
        self.view.shapes.clear()
        self.view.update()

    def result_image(self) -> Image.Image:
        # re-render shapes at full resolution
        from PySide6.QtGui import QPainter, QTransform
        from .canvas import draw_shapes
        full_q = pil_to_qimage(self.full).convertToFormat(QImage.Format.Format_ARGB32)
        p = QPainter(full_q)
        p.scale(self.factor, self.factor)
        draw_shapes(p, self.view.shapes)
        p.end()
        out = qimage_to_pil(full_q)
        return out if IM.has_alpha(self.full) else out.convert("RGB")


# ------------------------------------------------------------------ redact (image)
def apply_redaction(im: Image.Image, boxes, style: str) -> Image.Image:
    im = im.copy()
    for (x, y, w, h) in boxes:
        box = (int(x), int(y), int(x + w), int(y + h))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        region = im.crop(box)
        if style == "black":
            region = Image.new(im.mode, region.size, (0, 0, 0, 255) if im.mode == "RGBA" else (0, 0, 0) if im.mode == "RGB" else 0)
        elif style == "pixelate":
            bs = max(6, min(region.size) // 8)
            small = region.resize((max(1, region.width // bs), max(1, region.height // bs)), Image.Resampling.BILINEAR)
            region = small.resize(region.size, Image.Resampling.NEAREST)
        else:
            region = region.filter(ImageFilter.GaussianBlur(max(8, min(region.size) / 5)))
            region = region.filter(ImageFilter.GaussianBlur(max(8, min(region.size) / 5)))
        im.paste(region, box)
    return im


class RedactImageDialog(ToolDialog):
    def __init__(self, im: Image.Image, name: str):
        super().__init__("Redact", name, "Save", reset=True)
        self.full = im
        self.prev, self.factor = preview_of(im)
        r = QHBoxLayout()
        lab = QLabel("Drag over anything to hide it · right-click a box to remove")
        lab.setObjectName("Muted")
        r.addWidget(lab, 1)
        self.style = Chips(["black", "pixelate", "blur"], "black", {"black": "Black box", "pixelate": "Pixelate", "blur": "Blur"})
        self.style.changed.connect(self._restyle)
        r.addWidget(self.style)
        self.body.addLayout(r)
        self.view = BoxView(pil_to_qimage(self.prev))
        self.view.changed.connect(self._refresh)
        self.body.addWidget(self.view, 1)
        self.resize(900, 720)

    def _restyle(self, s):
        self.view.style = s
        self._refresh()

    def _refresh(self):
        boxes = [(b.x(), b.y(), b.width(), b.height()) for b in self.view.boxes]
        if boxes:
            out = apply_redaction(self.prev.convert("RGBA"), boxes, self.view.style)
            self.view.preview = QPixmap.fromImage(pil_to_qimage(out))
        else:
            self.view.preview = None
        self.view.update()

    def on_reset(self):
        self.view.boxes.clear()
        self._refresh()

    def boxes_full(self):
        f = self.factor
        return [(b.x() * f, b.y() * f, b.width() * f, b.height() * f) for b in self.view.boxes]


# ------------------------------------------------------------------ edit / adjust
def adjust(im: Image.Image, v: dict) -> Image.Image:
    alpha = im.getchannel("A") if IM.has_alpha(im) else None
    out = im.convert("RGB")
    if v.get("auto"):
        out = ImageOps.autocontrast(out, cutoff=1)
        out = ImageEnhance.Color(out).enhance(1.08)
    if v.get("brightness"):
        out = ImageEnhance.Brightness(out).enhance(1 + v["brightness"] / 100)
    if v.get("contrast"):
        out = ImageEnhance.Contrast(out).enhance(1 + v["contrast"] / 100)
    if v.get("saturation"):
        out = ImageEnhance.Color(out).enhance(max(0, 1 + v["saturation"] / 100))
    if v.get("warmth"):
        k = v["warmth"] / 400
        r, g, b = out.split()
        r = r.point(lambda x: min(255, int(x * (1 + k))))
        b = b.point(lambda x: min(255, int(x * (1 - k))))
        out = Image.merge("RGB", (r, g, b))
    if v.get("highlights") or v.get("shadows"):
        hi, sh = v.get("highlights", 0) / 100, v.get("shadows", 0) / 100
        lut = []
        for x in range(256):
            t = x / 255
            y = t + sh * (1 - t) ** 2 * t * 1.5 + hi * t ** 2 * (1 - t) * 1.5
            lut.append(max(0, min(255, int(y * 255))))
        out = out.point(lut * 3)
    if v.get("sharpness"):
        out = ImageEnhance.Sharpness(out).enhance(1 + v["sharpness"] / 40)
    if v.get("bw"):
        out = ImageOps.grayscale(out).convert("RGB")
    if v.get("vignette"):
        w, h = out.size
        mask = Image.radial_gradient("L").resize((w, h), Image.Resampling.BILINEAR)
        strength = v["vignette"] / 100
        mask = mask.point(lambda x: int(max(0, (x - 90)) * strength * 1.6))
        out = Image.composite(Image.new("RGB", (w, h), (0, 0, 0)), out, mask)
    if alpha is not None:
        out.putalpha(alpha)
    rot = v.get("rotate", 0)
    if rot:
        out = out.rotate(-rot, expand=True)
    if v.get("flip_h"):
        out = ImageOps.mirror(out)
    if v.get("flip_v"):
        out = ImageOps.flip(out)
    return out


class EditDialog(ToolDialog):
    def __init__(self, im: Image.Image, name: str, batch: int = 1):
        super().__init__("Edit", name, "Save", reset=True)
        self.full = im
        self.prev, _ = preview_of(im, 1000)
        self.state = {"rotate": 0, "flip_h": False, "flip_v": False, "auto": False, "bw": False}
        top = QHBoxLayout()
        self.view = ImageView()
        top.addWidget(self.view, 1)
        side = QVBoxLayout()
        side.setSpacing(8)
        quick = QHBoxLayout()
        self.auto_btn = QPushButton("  Auto")
        self.auto_btn.setIcon(icons.icon("wand", theme.TEXT, 16))
        self.auto_btn.setCheckable(True)
        self.auto_btn.toggled.connect(lambda v: self._set("auto", v))
        self.bw_btn = QPushButton("  B&&W")
        self.bw_btn.setIcon(icons.icon("droplet", theme.TEXT, 16))
        self.bw_btn.setCheckable(True)
        self.bw_btn.toggled.connect(lambda v: self._set("bw", v))
        quick.addWidget(self.auto_btn)
        quick.addWidget(self.bw_btn)
        side.addLayout(quick)
        side.addWidget(section("Light"))
        self.sl = {}
        for key, lab, lo, hi in (("brightness", "Brightness", -100, 100), ("contrast", "Contrast", -100, 100),
                                 ("highlights", "Highlights", -100, 100), ("shadows", "Shadows", -100, 100)):
            self._slider(side, key, lab, lo, hi)
        side.addWidget(section("Colour"))
        for key, lab, lo, hi in (("saturation", "Saturation", -100, 100), ("warmth", "Warmth", -100, 100)):
            self._slider(side, key, lab, lo, hi)
        side.addWidget(section("Detail"))
        for key, lab, lo, hi in (("sharpness", "Sharpness", 0, 100), ("vignette", "Vignette", 0, 100)):
            self._slider(side, key, lab, lo, hi)
        side.addWidget(section("Orientation"))
        orow = QHBoxLayout()
        for ic, tip, fn in (("rotate", "Rotate left", lambda: self._rot(-90)), ("reverse", "Rotate right", lambda: self._rot(90)),
                            ("flip", "Flip horizontal", lambda: self._toggle("flip_h")),
                            ("split", "Flip vertical", lambda: self._toggle("flip_v"))):
            b = QToolButton()
            b.setIcon(icons.icon(ic, theme.TEXT, 18))
            b.setToolTip(tip)
            b.clicked.connect(fn)
            orow.addWidget(b)
        orow.addStretch(1)
        side.addLayout(orow)
        side.addStretch(1)
        self.compare = QPushButton("Hold to compare")
        self.compare.pressed.connect(lambda: self.view.set_image(pil_to_qimage(self.prev)))
        self.compare.released.connect(self._render)
        side.addWidget(self.compare)
        self.apply_all = QCheckBox(f"Apply to all {batch} images")
        self.apply_all.setVisible(batch > 1)
        self.apply_all.setChecked(batch > 1)
        side.addWidget(self.apply_all)
        sw = QWidget()
        sw.setLayout(side)
        sw.setFixedWidth(330)
        top.addWidget(sw)
        self.body.addLayout(top, 1)
        self._timer = QTimer(self, singleShot=True, interval=30, timeout=self._render)
        self.resize(1080, 700)
        self._render()

    def _slider(self, lay, key, lab, lo, hi):
        s = LabeledSlider(lab, lo, hi, 0)
        s.changed.connect(lambda v, k=key: self._set(k, v))
        self.sl[key] = s
        lay.addWidget(s)

    def _set(self, k, v):
        self.state[k] = v
        self._timer.start()

    def _rot(self, d):
        self.state["rotate"] = (self.state["rotate"] + d) % 360
        self._render()

    def _toggle(self, k):
        self.state[k] = not self.state[k]
        self._render()

    def values(self) -> dict:
        return dict(self.state)

    def _render(self):
        self.view.set_image(pil_to_qimage(adjust(self.prev, self.state)))

    def on_reset(self):
        for s in self.sl.values():
            s.reset()
        self.auto_btn.setChecked(False)
        self.bw_btn.setChecked(False)
        self.state.update({"rotate": 0, "flip_h": False, "flip_v": False})
        self._render()
