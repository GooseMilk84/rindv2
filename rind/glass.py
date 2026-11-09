"""Liquid glass: blurred backdrops, painted glass panels and the Windows blur/Mica hooks.

Three tricks, best one available wins:
  * wheel  - snapshot the screen under it right before it opens, blur + saturate it, paint it back
             through the glass (with a magnified rim so the edge "bends" light like a lens)
  * shelf / toasts - DWM blur-behind (SetWindowCompositionAttribute), clipped to a rounded mask
  * dialogs / settings - Windows 11 acrylic system backdrop
Anything that fails falls back to a darker tint, so text is always readable.
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRegion)
from PySide6.QtWidgets import QWidget

from .config import settings

IS_WIN = sys.platform == "win32"


def enabled() -> bool:
    return bool(settings.get("glass", True))


def win_build() -> int:
    try:
        return sys.getwindowsversion().build if IS_WIN else 0
    except Exception:
        return 0


# ------------------------------------------------------------------ backdrop snapshots
def blur_pixmap(pm: QPixmap, radius: float = 14, saturate: float = 1.35) -> QPixmap:
    """Gaussian blur + a saturation bump (that 'vibrancy' look). Pillow does the heavy lifting."""
    try:
        from PIL import Image, ImageEnhance, ImageFilter
    except Exception:
        return _blur_qt(pm)
    dpr = pm.devicePixelRatio()
    img = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    if w < 2 or h < 2:
        return pm
    im = Image.frombuffer("RGBA", (w, h), bytes(img.constBits()), "raw", "RGBA", img.bytesPerLine(), 1)
    small = im.resize((max(1, w // 3), max(1, h // 3)), Image.BILINEAR)
    small = small.filter(ImageFilter.GaussianBlur(radius * dpr / 3))
    if saturate != 1:
        small = ImageEnhance.Color(small.convert("RGB")).enhance(saturate).convert("RGBA")
    big = small.resize((w, h), Image.BICUBIC)
    data = big.tobytes()
    out = QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    res = QPixmap.fromImage(out)
    res.setDevicePixelRatio(dpr)
    return res


def _blur_qt(pm: QPixmap) -> QPixmap:
    dpr = pm.devicePixelRatio()
    s = pm.size()
    tiny = pm.scaled(max(1, s.width() // 12), max(1, s.height() // 12), Qt.AspectRatioMode.IgnoreAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)
    out = tiny.scaled(s, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    out.setDevicePixelRatio(dpr)
    return out


def grab_behind(global_rect: QRect, radius: float = 14) -> QPixmap | None:
    """Blurred picture of whatever is on screen inside global_rect (call it before you show)."""
    if not enabled() or not settings.get("glass_backdrop", True):
        return None
    try:
        scr = QGuiApplication.screenAt(global_rect.center()) or QGuiApplication.primaryScreen()
        g = scr.geometry()
        pm = scr.grabWindow(0, global_rect.x() - g.x(), global_rect.y() - g.y(), global_rect.width(), global_rect.height())
        if pm.isNull() or pm.width() < 2:
            return None
        return blur_pixmap(pm, radius)
    except Exception:
        return None


# ------------------------------------------------------------------ painting
def rim_pen(rect: QRectF, strength: float = 1.0, width: float = 1.2) -> QPen:
    """The specular edge: bright top-left, faint sides, a softer glint bottom-right."""
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    g.setColorAt(0.0, QColor(255, 255, 255, int(165 * strength)))
    g.setColorAt(0.28, QColor(255, 255, 255, int(48 * strength)))
    g.setColorAt(0.62, QColor(255, 255, 255, int(16 * strength)))
    g.setColorAt(1.0, QColor(255, 255, 255, int(95 * strength)))
    return QPen(g, width)


def sheen(rect: QRectF, top: int = 30) -> QLinearGradient:
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    g.setColorAt(0.0, QColor(255, 255, 255, top))
    g.setColorAt(0.38, QColor(255, 255, 255, int(top * 0.25)))
    g.setColorAt(0.62, QColor(255, 255, 255, 0))
    g.setColorAt(1.0, QColor(255, 255, 255, int(top * 0.2)))
    return g


def shadow(p: QPainter, rect: QRectF, radius: float, steps: int = 8, alpha: int = 7, dy: float = 3):
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(0, 0, 0, alpha))
    for i in range(steps, 0, -1):
        p.drawRoundedRect(rect.adjusted(-i, -i + dy, i, i + dy), radius + i, radius + i)
    p.restore()


def panel(p: QPainter, rect: QRectF, radius: float, *, backdrop: QPixmap | None = None,
          backdrop_at: QPointF = QPointF(0, 0), tint: QColor | None = None, path: QPainterPath | None = None,
          rim: float = 1.0, shine: int = 30):
    """One pane of liquid glass: backdrop -> tint -> sheen -> specular rim."""
    if path is None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
    p.save()
    p.setClipPath(path, Qt.ClipOperation.IntersectClip)
    if backdrop is not None:
        p.drawPixmap(backdrop_at, backdrop)
    p.fillPath(path, tint if tint is not None else QColor(16, 16, 20, 150 if backdrop is not None else 236))
    p.fillPath(path, sheen(rect, shine))
    p.restore()
    if rim:
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(rim_pen(rect, rim))
        p.drawPath(path)
        p.restore()
    return path


# ------------------------------------------------------------------ Windows hooks
class _ACCENT(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class _WCAD(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


def blur_behind(w: QWidget, radius: float = 14) -> bool:
    """Frameless translucent windows: real live blur of whatever's behind them (Win10/11)."""
    if not (IS_WIN and enabled()):
        return False
    try:
        hwnd = int(w.winId())
        acc = _ACCENT(3, 0, 0, 0)                     # ACCENT_ENABLE_BLURBEHIND
        data = _WCAD(19, ctypes.cast(ctypes.pointer(acc), ctypes.c_void_p), ctypes.sizeof(acc))
        ok = bool(ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
        if ok:
            round_mask(w, radius)
        return ok
    except Exception:
        return False


def round_mask(w: QWidget, radius: float):
    """Clip the window (and the DWM blur with it) to a rounded rectangle."""
    path = QPainterPath()
    path.addRoundedRect(QRectF(w.rect()), radius, radius)
    w.setMask(QRegion(path.toFillPolygon().toPolygon()))


def wants_backdrop() -> bool:
    """Windows 11 22H2+ can put acrylic behind normal framed windows."""
    return IS_WIN and enabled() and win_build() >= 22621


def prepare_window(w: QWidget):
    """Call in __init__ (before the native window exists) for framed windows that should be glass."""
    if wants_backdrop():
        w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        w.setProperty("glass", True)
        w._glass = True
    else:
        w._glass = False


def apply_backdrop(w: QWidget) -> bool:
    if not getattr(w, "_glass", False):
        return False
    try:
        hwnd = int(w.winId())
        dwm = ctypes.windll.dwmapi

        class MARGINS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(-1, -1, -1, -1)))
        one = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(one), 4)              # dark mode
        kind = ctypes.c_int(3 if settings.get("glass_style", "acrylic") == "acrylic" else 2)   # 3 acrylic, 2 mica
        return dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(kind), 4) == 0
    except Exception:
        return False
