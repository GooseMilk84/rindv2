"""Image canvases: fit-to-widget view, crop box, multi-box drawer, annotation surface."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QWidget

from .. import theme


class ImageView(QWidget):
    """Shows an image letterboxed inside the widget, with coordinate mapping."""

    def __init__(self, img: QImage | None = None, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        self.setMouseTracking(True)
        self.img = img
        self.pm = QPixmap.fromImage(img) if img is not None else None
        self.margin = 18
        self.checker = True

    def set_image(self, img: QImage):
        self.img = img
        self.pm = QPixmap.fromImage(img)
        self.update()

    def img_rect(self) -> QRectF:
        if not self.pm:
            return QRectF()
        W, H = self.width() - 2 * self.margin, self.height() - 2 * self.margin
        iw, ih = self.pm.width(), self.pm.height()
        s = min(W / iw, H / ih)
        w, h = iw * s, ih * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def scale(self) -> float:
        r = self.img_rect()
        return r.width() / self.pm.width() if self.pm else 1

    def to_img(self, p: QPointF) -> QPointF:
        r = self.img_rect()
        s = self.scale()
        return QPointF((p.x() - r.x()) / s, (p.y() - r.y()) / s)

    def to_widget(self, p: QPointF) -> QPointF:
        r = self.img_rect()
        s = self.scale()
        return QPointF(r.x() + p.x() * s, r.y() + p.y() * s)

    def rect_to_widget(self, r: QRectF) -> QRectF:
        a = self.to_widget(r.topLeft())
        s = self.scale()
        return QRectF(a.x(), a.y(), r.width() * s, r.height() * s)

    def paint_base(self, p: QPainter):
        p.fillRect(self.rect(), QColor("#0d0d0f"))
        if not self.pm:
            return
        r = self.img_rect()
        if self.checker:
            p.save()
            p.setClipRect(r)
            c1, c2 = QColor("#2a2a2e"), QColor("#202024")
            sz = 10
            for yy in range(int(r.top()), int(r.bottom()) + sz, sz):
                for xx in range(int(r.left()), int(r.right()) + sz, sz):
                    p.fillRect(xx, yy, sz, sz, c1 if ((xx // sz + yy // sz) % 2) else c2)
            p.restore()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(r, self.pm, QRectF(self.pm.rect()))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_base(p)
        self.paint_over(p)
        p.end()

    def paint_over(self, p: QPainter):
        pass


# ------------------------------------------------------------------ crop
class CropView(ImageView):
    changed = Signal()

    def __init__(self, img: QImage, parent=None):
        super().__init__(img, parent)
        self.ratio: float | None = None
        self.crop = QRectF(0, 0, img.width(), img.height())
        self.drag = None
        self.start_pt = QPointF()
        self.start_rect = QRectF()

    def set_ratio(self, ratio: float | None):
        self.ratio = ratio
        if ratio:
            W, H = self.pm.width(), self.pm.height()
            c = self.crop.center()
            w = min(W, H * ratio)
            h = w / ratio
            r = QRectF(0, 0, w, h)
            r.moveCenter(c)
            self.crop = self._clamp(r)
        self.changed.emit()
        self.update()

    def reset(self):
        self.crop = QRectF(0, 0, self.pm.width(), self.pm.height())
        self.ratio = None
        self.changed.emit()
        self.update()

    def set_size(self, w: float, h: float):
        r = QRectF(self.crop.x(), self.crop.y(), w, h)
        if r.right() > self.pm.width():
            r.moveRight(self.pm.width())
        if r.bottom() > self.pm.height():
            r.moveBottom(self.pm.height())
        self.crop = self._clamp(r)
        self.update()

    def _clamp(self, r: QRectF) -> QRectF:
        W, H = self.pm.width(), self.pm.height()
        r = QRectF(r)
        r.setWidth(min(r.width(), W))
        r.setHeight(min(r.height(), H))
        if r.left() < 0:
            r.moveLeft(0)
        if r.top() < 0:
            r.moveTop(0)
        if r.right() > W:
            r.moveRight(W)
        if r.bottom() > H:
            r.moveBottom(H)
        return r

    def _handles(self):
        r = self.rect_to_widget(self.crop)
        pts = {"tl": r.topLeft(), "tr": r.topRight(), "bl": r.bottomLeft(), "br": r.bottomRight(),
               "t": QPointF(r.center().x(), r.top()), "b": QPointF(r.center().x(), r.bottom()),
               "l": QPointF(r.left(), r.center().y()), "r": QPointF(r.right(), r.center().y())}
        return pts, r

    def _hit(self, pos: QPointF):
        pts, r = self._handles()
        for k, pt in pts.items():
            if abs(pos.x() - pt.x()) < 12 and abs(pos.y() - pt.y()) < 12:
                return k
        if r.contains(pos):
            return "move"
        return "new"

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.drag is None:
            h = self._hit(pos)
            cur = {"tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                   "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                   "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                   "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
                   "move": Qt.CursorShape.SizeAllCursor}.get(h, Qt.CursorShape.CrossCursor)
            self.setCursor(cur)
            return
        ip = self.to_img(pos)
        sp = self.start_pt
        dx, dy = ip.x() - sp.x(), ip.y() - sp.y()
        r = QRectF(self.start_rect)
        W, H = self.pm.width(), self.pm.height()
        if self.drag == "move":
            r.translate(dx, dy)
            self.crop = self._clamp(r)
        elif self.drag == "new":
            x0, y0 = sp.x(), sp.y()
            x1 = min(max(ip.x(), 0), W)
            y1 = min(max(ip.y(), 0), H)
            if self.ratio:
                w = abs(x1 - x0)
                h = w / self.ratio
                y1 = y0 + (h if y1 >= y0 else -h)
            self.crop = self._clamp(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized())
        else:
            d = self.drag
            if "l" in d:
                r.setLeft(min(r.right() - 8, max(0, r.left() + dx)))
            if "r" in d:
                r.setRight(max(r.left() + 8, min(W, r.right() + dx)))
            if "t" in d:
                r.setTop(min(r.bottom() - 8, max(0, r.top() + dy)))
            if "b" in d:
                r.setBottom(max(r.top() + 8, min(H, r.bottom() + dy)))
            if self.ratio:
                if d in ("l", "r"):
                    h = r.width() / self.ratio
                    c = r.center().y()
                    r.setTop(c - h / 2)
                    r.setHeight(h)
                elif d in ("t", "b"):
                    w = r.height() * self.ratio
                    c = r.center().x()
                    r.setLeft(c - w / 2)
                    r.setWidth(w)
                else:
                    h = r.width() / self.ratio
                    if "t" in d:
                        r.setTop(r.bottom() - h)
                    else:
                        r.setBottom(r.top() + h)
            self.crop = self._clamp(r.normalized())
        self.changed.emit()
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self.drag = self._hit(e.position())
        self.start_pt = self.to_img(e.position())
        self.start_pt = QPointF(min(max(self.start_pt.x(), 0), self.pm.width()), min(max(self.start_pt.y(), 0), self.pm.height()))
        self.start_rect = QRectF(self.crop)

    def mouseReleaseEvent(self, e):
        if self.crop.width() < 4 or self.crop.height() < 4:
            self.crop = QRectF(self.start_rect)
        self.drag = None
        self.update()
        self.changed.emit()

    def paint_over(self, p: QPainter):
        if not self.pm:
            return
        ir = self.img_rect()
        r = self.rect_to_widget(self.crop)
        shade = QPainterPath()
        shade.addRect(ir)
        hole = QPainterPath()
        hole.addRect(r)
        p.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 150))
        acc = QColor(theme.accent())
        if self.drag:
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            for i in (1, 2):
                x = r.left() + r.width() * i / 3
                y = r.top() + r.height() * i / 3
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
                p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        p.setPen(QPen(acc, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r)
        pts, _ = self._handles()
        p.setBrush(acc)
        p.setPen(Qt.PenStyle.NoPen)
        for k, pt in pts.items():
            if len(k) == 2:
                p.drawRoundedRect(QRectF(pt.x() - 6, pt.y() - 6, 12, 12), 3, 3)
            else:
                horiz = k in ("t", "b")
                p.drawRoundedRect(QRectF(pt.x() - (10 if horiz else 3), pt.y() - (3 if horiz else 10),
                                         20 if horiz else 6, 6 if horiz else 20), 3, 3)


# ------------------------------------------------------------------ boxes (redact)
class BoxView(ImageView):
    changed = Signal()

    def __init__(self, img: QImage, parent=None):
        super().__init__(img, parent)
        self.boxes: list[QRectF] = []
        self.cur: QRectF | None = None
        self.start = QPointF()
        self.style = "black"
        self.preview: QPixmap | None = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            ip = self.to_img(e.position())
            for b in reversed(self.boxes):
                if b.contains(ip):
                    self.boxes.remove(b)
                    self.changed.emit()
                    self.update()
                    return
            return
        self.start = self.to_img(e.position())
        self.cur = QRectF(self.start, self.start)

    def mouseMoveEvent(self, e):
        if self.cur is not None:
            ip = self.to_img(e.position())
            ip = QPointF(min(max(ip.x(), 0), self.pm.width()), min(max(ip.y(), 0), self.pm.height()))
            self.cur = QRectF(self.start, ip).normalized()
            self.update()

    def mouseReleaseEvent(self, e):
        if self.cur is not None and self.cur.width() > 3 and self.cur.height() > 3:
            self.boxes.append(self.cur)
            self.changed.emit()
        self.cur = None
        self.update()

    def paint_over(self, p: QPainter):
        if self.preview is not None:
            p.drawPixmap(self.img_rect(), self.preview, QRectF(self.preview.rect()))
        for b in self.boxes + ([self.cur] if self.cur else []):
            r = self.rect_to_widget(b)
            if self.preview is None or b is self.cur:
                p.setBrush(QColor(0, 0, 0, 230) if self.style == "black" else QColor(255, 255, 255, 60))
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(theme.accent()), 1.5, Qt.PenStyle.DashLine))
            p.drawRect(r)


# ------------------------------------------------------------------ annotate
class Shape:
    def __init__(self, kind, color, width, pts=None, text="", n=0):
        self.kind, self.color, self.width = kind, QColor(color), width
        self.pts: list[QPointF] = pts or []
        self.text, self.n = text, n


def draw_shapes(p: QPainter, shapes: list[Shape], scale: float = 1.0):
    """Paint shapes (stored in image coords) with painter already mapped to image space."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for s in shapes:
        w = s.width
        if s.kind == "marker":
            c = QColor(s.color)
            c.setAlpha(110)
            pen = QPen(c, w * 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            if len(s.pts) > 1:
                path = QPainterPath(s.pts[0])
                for pt in s.pts[1:]:
                    path.lineTo(pt)
                p.drawPath(path)
            continue
        if s.kind == "pen":
            p.setPen(QPen(s.color, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            if len(s.pts) > 1:
                path = QPainterPath(s.pts[0])
                for pt in s.pts[1:]:
                    path.lineTo(pt)
                p.drawPath(path)
            continue
        if len(s.pts) < 2 and s.kind not in ("number", "text"):
            continue
        if s.kind == "arrow":
            a, b = s.pts[0], s.pts[-1]
            ang = math.atan2(b.y() - a.y(), b.x() - a.x())
            head = max(12, w * 4.2)
            shaft_end = QPointF(b.x() - math.cos(ang) * head * 0.8, b.y() - math.sin(ang) * head * 0.8)
            p.setPen(QPen(s.color, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(a, shaft_end)
            poly = QPolygonF([b, QPointF(b.x() - head * math.cos(ang - 0.45), b.y() - head * math.sin(ang - 0.45)),
                              QPointF(b.x() - head * math.cos(ang + 0.45), b.y() - head * math.sin(ang + 0.45))])
            p.setPen(QPen(s.color, 1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(s.color)
            p.drawPolygon(poly)
        elif s.kind in ("rect", "ellipse"):
            r = QRectF(s.pts[0], s.pts[-1]).normalized()
            p.setPen(QPen(s.color, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            if s.kind == "rect":
                p.drawRoundedRect(r, w, w)
            else:
                p.drawEllipse(r)
        elif s.kind == "number":
            c = s.pts[0]
            rad = max(14, w * 4.5)
            p.setPen(QPen(QColor("#ffffff"), max(2, w * 0.6)))
            p.setBrush(s.color)
            p.drawEllipse(c, rad, rad)
            f = theme.ui_font(1)
            f.setPixelSize(int(rad * 1.1))
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), Qt.AlignmentFlag.AlignCenter, str(s.n))
        elif s.kind == "text":
            f = theme.ui_font(1)
            f.setPixelSize(int(max(14, w * 6)))
            f.setWeight(QFont.Weight.Bold)
            p.setFont(f)
            path = QPainterPath()
            path.addText(s.pts[0], f, s.text)
            p.setPen(QPen(QColor(0, 0, 0, 160), max(2, w * 0.9), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(s.color)
            p.drawPath(path)


class AnnotateView(ImageView):
    changed = Signal()
    want_text = Signal(QPointF)

    def __init__(self, img: QImage, parent=None):
        super().__init__(img, parent)
        self.shapes: list[Shape] = []
        self.redo: list[Shape] = []
        self.tool = "arrow"
        self.color = "#ff3b30"
        self.width_px = 6
        self.cur: Shape | None = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def next_number(self) -> int:
        return 1 + sum(1 for s in self.shapes if s.kind == "number")

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        ip = self.to_img(e.position())
        w = self.width_px * max(1.0, self.pm.width() / 1400)
        if self.tool == "number":
            self.shapes.append(Shape("number", self.color, w, [ip], n=self.next_number()))
            self.redo.clear()
            self.changed.emit()
            self.update()
            return
        if self.tool == "text":
            self.want_text.emit(ip)
            return
        self.cur = Shape(self.tool, self.color, w, [ip, ip])

    def mouseMoveEvent(self, e):
        if self.cur is None:
            return
        ip = self.to_img(e.position())
        if self.cur.kind in ("pen", "marker"):
            self.cur.pts.append(ip)
        else:
            self.cur.pts[-1] = ip
        self.update()

    def mouseReleaseEvent(self, e):
        if self.cur is not None:
            a, b = self.cur.pts[0], self.cur.pts[-1]
            if self.cur.kind in ("pen", "marker") or math.hypot(a.x() - b.x(), a.y() - b.y()) > 4:
                self.shapes.append(self.cur)
                self.redo.clear()
                self.changed.emit()
        self.cur = None
        self.update()

    def add_text(self, pt: QPointF, text: str):
        w = self.width_px * max(1.0, self.pm.width() / 1400)
        self.shapes.append(Shape("text", self.color, w, [pt], text=text))
        self.redo.clear()
        self.changed.emit()
        self.update()

    def undo(self):
        if self.shapes:
            self.redo.append(self.shapes.pop())
            self.update()

    def redo_(self):
        if self.redo:
            self.shapes.append(self.redo.pop())
            self.update()

    def paint_over(self, p: QPainter):
        r = self.img_rect()
        s = self.scale()
        p.save()
        p.setClipRect(r)
        p.translate(r.topLeft())
        p.scale(s, s)
        draw_shapes(p, self.shapes + ([self.cur] if self.cur else []))
        p.restore()

    def render_image(self) -> QImage:
        out = self.img.convertToFormat(QImage.Format.Format_ARGB32)
        p = QPainter(out)
        draw_shapes(p, self.shapes)
        p.end()
        return out
