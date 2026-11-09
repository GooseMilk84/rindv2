"""The radial menu. Appears under the cursor mid-drag; drop the file on a slice."""
from __future__ import annotations

import math
import os
import time

from PySide6.QtCore import (QEasingCurve, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen,
                           QRadialGradient, QLinearGradient)
from PySide6.QtWidgets import QWidget

from . import icons, registry, theme
from .config import settings

MODES = ["convert", "tool", "action"]
MODE_LABEL = {"convert": "Convert", "tool": "Tools", "action": "Actions"}
KEY_LABEL = {"shift": "Shift", "ctrl": "Ctrl", "alt": "Alt", "win": "Win"}


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


class Slice:
    __slots__ = ("item", "ring", "a0", "a1", "r0", "r1", "hover", "path", "center")

    def __init__(self, item, ring, a0, a1, r0, r1):
        self.item, self.ring, self.a0, self.a1, self.r0, self.r1 = item, ring, a0, a1, r0, r1
        self.hover = 0.0
        self.path = None
        self.center = QPointF()


class Wheel(QWidget):
    chosen = Signal(object, list)      # Item, paths
    cancelled = Signal()

    def __init__(self, click_mode: bool = False):
        super().__init__(None)
        self.click_mode = click_mode
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint \
            | Qt.WindowType.NoDropShadowWindowHint
        if not click_mode:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not click_mode)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.paths: list[str] = []
        self.mode = "convert"
        self.slices: list[Slice] = []
        self.hovered: Slice | None = None
        self.hub_hover = False
        self.appear = 0.0
        self.size_text = ""
        self.kind_text = ""
        self.waiting = True
        self.backdrop = None
        self._drop_done = False
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=170,
                                       easingCurve=QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_appear)
        self._tick = QTimer(self, interval=16, timeout=self._step)
        self._layout_size()

    # ------------------------------------------------------------ geometry
    def _layout_size(self):
        self.D = int(settings.get("wheel_size", 380))
        pad = 34
        self.setFixedSize(self.D + pad * 2, self.D + pad * 2 + 46)
        self.cx = self.width() / 2
        self.cy = pad + self.D / 2

    def _build(self):
        if self.mode == "convert":
            items = registry.convert_items(self.paths)
        elif self.mode == "tool":
            items = registry.tool_items(self.paths)
        else:
            items = registry.action_items(self.paths)
        R = self.D / 2
        hub = R * 0.30
        self.hub_r = hub
        self.slices = []
        if not items:
            self.update()
            return
        n = len(items)
        if n <= 12:
            rings = [items]
            radii = [(hub + 8, R * (0.86 if n <= 8 else 0.93))]
        else:
            # split so slices in both rings get similar arc length; most-used items stay on the inner ring
            k = max(5, round(n * 0.42))
            rings = [items[:k], items[k:]]
            radii = [(hub + 8, R * 0.63), (R * 0.63 + 5, R)]
        for ri, (ring_items, (r0, r1)) in enumerate(zip(rings, radii)):
            n = len(ring_items)
            span = 2 * math.pi / n
            offset = 0 if ri == 0 else span / 2
            for i, it in enumerate(ring_items):
                c = -math.pi / 2 + i * span + offset
                s = Slice(it, ri, c - span / 2, c + span / 2, r0, r1)
                s.path = self._slice_path(s)
                rm = (r0 + r1) / 2
                s.center = QPointF(self.cx + math.cos(c) * rm, self.cy + math.sin(c) * rm)
                self.slices.append(s)
        self.hovered = None
        self.update()

    def _slice_path(self, s: Slice) -> QPainterPath:
        gap = 3.0
        r0, r1 = s.r0 + gap, s.r1 - gap
        g0, g1 = gap / r0, gap / r1
        p = QPainterPath()
        cx, cy = self.cx, self.cy
        a0o, a1o = s.a0 + g1, s.a1 - g1
        a0i, a1i = s.a0 + g0, s.a1 - g0
        deg = lambda r: -math.degrees(r)
        p.moveTo(cx + math.cos(a0o) * r1, cy + math.sin(a0o) * r1)
        p.arcTo(QRectF(cx - r1, cy - r1, 2 * r1, 2 * r1), deg(a0o), deg(a1o) - deg(a0o))
        p.lineTo(cx + math.cos(a1i) * r0, cy + math.sin(a1i) * r0)
        p.arcTo(QRectF(cx - r0, cy - r0, 2 * r0, 2 * r0), deg(a1i), deg(a0i) - deg(a1i))
        p.closeSubpath()
        return p

    def hit(self, pos: QPointF):
        dx, dy = pos.x() - self.cx, pos.y() - self.cy
        d = math.hypot(dx, dy)
        if d <= self.hub_r + 4:
            return "hub"
        a = math.atan2(dy, dx)
        for s in self.slices:
            if s.r0 - 4 <= d <= s.r1 + 18:
                aa = a
                while aa < s.a0:
                    aa += 2 * math.pi
                while aa >= s.a0 + 2 * math.pi:
                    aa -= 2 * math.pi
                if s.a0 <= aa < s.a1:
                    # with two rings, prefer the ring whose band we're in
                    if s.ring == 0 and len({x.ring for x in self.slices}) > 1 and d > s.r1 + 2:
                        continue
                    return s
        return None

    # ------------------------------------------------------------ public
    def popup(self, global_pos: QPoint, paths: list[str] | None = None, mode: str = "convert"):
        self._layout_size()
        self.mode = mode
        self._drop_done = False
        self.set_paths(paths or [])
        scr = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        x = int(global_pos.x() - self.cx)
        y = int(global_pos.y() - self.cy)
        x = max(g.left() - 20, min(x, g.right() - self.width() + 20))
        y = max(g.top() - 20, min(y, g.bottom() - self.height() + 20))
        self.move(x, y)
        # liquid glass: photograph what's under the wheel *before* it appears, blurred
        from . import glass
        self.backdrop = glass.grab_behind(QRect(x, y, self.width(), self.height()), 16) if not self.isVisible() else self.backdrop
        self.setWindowOpacity(float(settings.get("wheel_opacity", 1.0)))
        self.show()
        self.raise_()
        if self.click_mode:
            self.activateWindow()
            self.setFocus()
        self._anim.stop()
        self._anim.start()
        self._tick.start()

    def set_paths(self, paths: list[str]):
        self.paths = [p for p in paths if p]
        self.waiting = not self.paths
        if self.paths:
            self.size_text = registry.human_size(registry.total_size(self.paths))
            self.kind_text = registry.describe(self.paths)
            self._warm_office()
        self._build()

    def _warm_office(self):
        """Wheel just opened on an Office file: boot Word/PowerPoint/Excel now, while you pick a slice."""
        try:
            from .engines import office
            exts = {registry.ext_of(p) for p in self.paths}
            if exts & {"docx", "doc", "docm", "dotx", "odt", "rtf"}:
                office.warm("Word.Application")
            if exts & {"pptx", "ppt", "pptm", "odp"}:
                office.warm("PowerPoint.Application")
            if exts & {"xlsx", "xls", "xlsm", "ods"}:
                office.warm("Excel.Application")
        except Exception:
            pass

    def set_mode(self, mode: str):
        if mode != self.mode:
            self.mode = mode
            self._build()
            self._tick.start()

    def dismiss(self):
        self._tick.stop()
        self.hide()
        self.backdrop = None
        self.paths = []
        self.slices = []

    # ------------------------------------------------------------ animation
    def _on_appear(self, v):
        self.appear = float(v)
        self.update()

    def _step(self):
        moving = False
        for s in self.slices:
            target = 1.0 if s is self.hovered else 0.0
            if abs(s.hover - target) > 0.01:
                s.hover += (target - s.hover) * 0.35
                moving = True
            else:
                s.hover = target
        self.update()
        if not moving and self._anim.state() != QVariantAnimation.State.Running:
            self._tick.stop()

    def _set_hover(self, s):
        hub = s == "hub"
        s = s if isinstance(s, Slice) else None
        if s is not self.hovered or hub != self.hub_hover:
            self.hovered = s
            self.hub_hover = hub
            self._tick.start()

    # ------------------------------------------------------------ drag & drop
    def _urls(self, mime) -> list[str]:
        out = []
        for u in mime.urls():
            if u.isLocalFile():
                out.append(os.path.normpath(u.toLocalFile()))
        return out

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            paths = self._urls(e.mimeData())
            if paths and paths != self.paths:
                self.set_paths(paths)
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
            self._set_hover(self.hit(e.position()))
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        e.setDropAction(Qt.DropAction.CopyAction)
        e.accept()
        self._set_hover(self.hit(e.position()))

    def dragLeaveEvent(self, e):
        self._set_hover(None)

    def dropEvent(self, e):
        s = self.hit(e.position())
        e.setDropAction(Qt.DropAction.CopyAction)
        e.accept()
        paths = self._urls(e.mimeData()) or self.paths
        self._drop_done = True
        self.dismiss()
        if isinstance(s, Slice):
            self.chosen.emit(s.item, paths)
        else:
            self.cancelled.emit()

    # ------------------------------------------------------------ click mode
    def mouseMoveEvent(self, e):
        self._set_hover(self.hit(e.position()))

    def mousePressEvent(self, e):
        if not self.click_mode:
            return
        s = self.hit(e.position())
        if isinstance(s, Slice):
            paths = list(self.paths)
            self.dismiss()
            self.chosen.emit(s.item, paths)
        elif s is None:
            self.dismiss()
            self.cancelled.emit()

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.dismiss()
            self.cancelled.emit()
        elif k in (Qt.Key.Key_Tab, Qt.Key.Key_Right):
            self.set_mode(MODES[(MODES.index(self.mode) + 1) % 3])
        elif k in (Qt.Key.Key_Backtab, Qt.Key.Key_Left):
            self.set_mode(MODES[(MODES.index(self.mode) - 1) % 3])
        elif k == Qt.Key.Key_Shift:
            self.set_mode("convert")
        elif k == Qt.Key.Key_Control:
            self.set_mode("tool")
        elif k == Qt.Key.Key_Alt:
            self.set_mode("action")

    def focusOutEvent(self, e):
        if self.click_mode and self.isVisible():
            QTimer.singleShot(120, lambda: (self.dismiss(), self.cancelled.emit()) if self.isVisible() and not self.isActiveWindow() else None)

    # ------------------------------------------------------------ painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        # catch-all: near-invisible fill so drops in the dead zone land on us (= cancel), not the desktop
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        t = self.appear
        scale = 0.86 + 0.14 * t
        p.setOpacity(min(1.0, t * 1.4))
        p.translate(self.cx, self.cy)
        p.scale(scale, scale)
        p.translate(-self.cx, -self.cy)

        R = self.D / 2
        accent = QColor(theme.accent())
        text_c = QColor("#e4e4ea")
        muted = QColor(theme.MUTED)

        from . import glass
        bd = getattr(self, "backdrop", None)
        c = QPointF(self.cx, self.cy)
        # shadow
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(10, 0, -1):
            p.setBrush(QColor(0, 0, 0, int(8 * (11 - i) / 11 * 1.5)))
            rr = R + 2 + i * 2.2
            p.drawEllipse(QPointF(self.cx, self.cy + 7), rr, rr)
        # plate: blurred backdrop, a magnified "lens" rim, tint, sheen, specular edge
        outer = (max(s.r1 for s in self.slices) if self.slices else R * 0.9) + 8
        plate = QPainterPath()
        plate.addEllipse(c, outer, outer)
        prect = QRectF(self.cx - outer, self.cy - outer, 2 * outer, 2 * outer)
        if bd is not None:
            p.save()
            p.setClipPath(plate)
            tr = p.transform()
            p.resetTransform()
            p.drawPixmap(0, 0, bd)
            # lens: the outer band shows the backdrop slightly magnified, like light bending through a thick edge
            p.setTransform(tr)
            band = QPainterPath()
            band.addEllipse(c, outer, outer)
            inner = QPainterPath()
            inner.addEllipse(c, outer - 16, outer - 16)
            p.setClipPath(band.subtracted(inner))
            p.resetTransform()
            p.translate(self.cx, self.cy)
            p.scale(1.07, 1.07)
            p.translate(-self.cx, -self.cy)
            p.drawPixmap(0, 0, bd)
            p.restore()
        glass.panel(p, prect, 0, path=plate, tint=QColor(14, 14, 18, 140 if bd is not None else 238), rim=1.0, shine=34)

        # slices
        if not self.slices:
            p.setPen(QPen(QColor(255, 255, 255, 40), 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(self.cx, self.cy), R * 0.62, R * 0.62)
        fm_big = QFont(theme.ui_font(10.5, QFont.Weight.DemiBold))
        fm_small = QFont(theme.ui_font(7.6, QFont.Weight.DemiBold))
        many = len(self.slices) > 9
        for s in self.slices:
            h = s.hover
            p.save()
            if h > 0:
                mid = (s.a0 + s.a1) / 2
                push = 5 * h
                p.translate(math.cos(mid) * push, math.sin(mid) * push)
            br = s.path.boundingRect()
            # frosted pane, flooding with accent as you hover
            g = QLinearGradient(br.topLeft(), br.bottomLeft())
            g.setColorAt(0, _mix(QColor(255, 255, 255, 34), QColor(accent.lighter(128).red(), accent.lighter(128).green(),
                                                                   accent.lighter(128).blue(), 235), h))
            g.setColorAt(1, _mix(QColor(255, 255, 255, 12), QColor(accent.darker(110).red(), accent.darker(110).green(),
                                                                   accent.darker(110).blue(), 225), h))
            p.setBrush(g)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(s.path)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(glass.rim_pen(br, 0.75 + 0.5 * h, 1.1))
            p.drawPath(s.path)
            # content
            tc = _mix(text_c, QColor("#ffffff"), h)
            label = s.item.label
            if s.item.icon:
                isz = 19 if not many else 16
                icons.draw(p, QRectF(s.center.x() - isz / 2, s.center.y() - isz + 1, isz, isz), s.item.icon, tc.name(), 1.9)
                p.setFont(fm_small)
                p.setPen(tc)
                p.drawText(QRectF(s.center.x() - 46, s.center.y() + 4, 92, 16), Qt.AlignmentFlag.AlignCenter, label.upper())
            else:
                f = QFont(fm_big)
                if len(label) > 5:
                    f.setPointSizeF(8.6)
                p.setFont(f)
                p.setPen(tc)
                p.drawText(QRectF(s.center.x() - 46, s.center.y() - 10, 92, 20), Qt.AlignmentFlag.AlignCenter, label)
            p.restore()

        # hub
        hr = self.hub_r
        hub = QPainterPath()
        hub.addEllipse(QPointF(self.cx, self.cy), hr, hr)
        glass.panel(p, QRectF(self.cx - hr, self.cy - hr, 2 * hr, 2 * hr), 0, path=hub,
                    tint=QColor(0, 0, 0, 70) if not self.hub_hover else QColor(255, 255, 255, 22), rim=0.9, shine=26)
        p.setPen(QPen(accent, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.hovered is not None:
            p.drawEllipse(QPointF(self.cx, self.cy), hr - 4, hr - 4)
        hub_rect = QRectF(self.cx - hr + 6, self.cy - hr + 6, 2 * hr - 12, 2 * hr - 12)
        if self.hovered is not None:
            it = self.hovered.item
            title = it.label if not it.icon else it.label
            f = theme.ui_font(12 if len(title) <= 6 else 9.5, QFont.Weight.Bold)
            p.setFont(f)
            p.setPen(QColor("#ffffff"))
            sub = it.hint or ({"convert": "convert to", "tool": "tool", "action": "action"}[it.group])
            p.drawText(QRectF(hub_rect.x(), self.cy - 15, hub_rect.width(), 22), Qt.AlignmentFlag.AlignCenter, title)
            p.setFont(theme.ui_font(7.2))
            p.setPen(muted)
            p.drawText(QRectF(hub_rect.x() - 4, self.cy + 7, hub_rect.width() + 8, 14), Qt.AlignmentFlag.AlignCenter,
                       QFontMetricsF(p.font()).elidedText(sub, Qt.TextElideMode.ElideRight, hub_rect.width() + 6))
        elif self.hub_hover:
            p.setFont(theme.ui_font(10, QFont.Weight.DemiBold))
            p.setPen(muted)
            p.drawText(hub_rect, Qt.AlignmentFlag.AlignCenter, "cancel")
        elif self.waiting:
            p.setFont(theme.ui_font(8.5))
            p.setPen(muted)
            p.drawText(hub_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, "move to\nload")
        else:
            p.setFont(theme.ui_font(11, QFont.Weight.Bold))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(hub_rect.x(), self.cy - 16, hub_rect.width(), 20), Qt.AlignmentFlag.AlignCenter, self.size_text)
            p.setFont(theme.ui_font(7.4))
            p.setPen(muted)
            p.drawText(QRectF(hub_rect.x() - 4, self.cy + 4, hub_rect.width() + 8, 14), Qt.AlignmentFlag.AlignCenter,
                       QFontMetricsF(p.font()).elidedText(self.kind_text, Qt.TextElideMode.ElideRight, hub_rect.width() + 6))
        if self.slices == [] and not self.waiting:
            p.setFont(theme.ui_font(9))
            p.setPen(muted)
            p.drawText(QRectF(self.cx - R, self.cy + hr + 14, 2 * R, 20), Qt.AlignmentFlag.AlignCenter,
                       "nothing here for this file")

        # mode tabs pill
        p.resetTransform()
        p.setOpacity(min(1.0, t * 1.4))
        self._paint_tabs(p)
        p.end()

    def _paint_tabs(self, p: QPainter):
        keys = {"convert": settings.get("key_convert"), "tool": settings.get("key_tools"), "action": settings.get("key_actions")}
        f = theme.ui_font(8.2, QFont.Weight.DemiBold)
        fk = theme.ui_font(7, QFont.Weight.Normal)
        fm, fmk = QFontMetricsF(f), QFontMetricsF(fk)
        parts = []
        for m in MODES:
            lab = MODE_LABEL[m]
            key = KEY_LABEL.get(keys[m], keys[m] or "")
            w = fm.horizontalAdvance(lab) + fmk.horizontalAdvance(key) + 30
            parts.append((m, lab, key, w))
        total = sum(w for *_, w in parts) + 8
        x = self.cx - total / 2
        y = self.height() - 36
        from . import glass
        pill = QRectF(x, y, total, 28)
        bd = getattr(self, "backdrop", None)
        glass.panel(p, pill, 14, backdrop=bd, tint=QColor(14, 14, 18, 130 if bd is not None else 238), shine=30)
        x += 4
        for m, lab, key, w in parts:
            r = QRectF(x, y + 3, w, 22)
            if m == self.mode:
                a = QColor(theme.accent())
                g = QLinearGradient(r.topLeft(), r.bottomLeft())
                g.setColorAt(0, a.lighter(125))
                g.setColorAt(1, a.darker(106))
                p.setPen(glass.rim_pen(r, 0.9, 1))
                p.setBrush(g)
                p.drawRoundedRect(r, 11, 11)
            p.setFont(f)
            p.setPen(QColor("#ffffff") if m == self.mode else QColor("#c9c9d0"))
            lw = fm.horizontalAdvance(lab)
            p.drawText(QRectF(r.x() + 10, r.y(), lw + 2, r.height()), Qt.AlignmentFlag.AlignVCenter, lab)
            p.setFont(fk)
            p.setPen(QColor(255, 255, 255, 170) if m == self.mode else QColor(theme.MUTED))
            p.drawText(QRectF(r.x() + 14 + lw, r.y(), fmk.horizontalAdvance(key) + 4, r.height()), Qt.AlignmentFlag.AlignVCenter, key)
            x += w
