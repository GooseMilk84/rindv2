"""Compress, Resize, Metadata, Merge order, PDF organize, PDF redact, Rename, Checksum."""
from __future__ import annotations

import hashlib
import io
import os
import re
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from .. import icons, theme
from ..registry import human_size, kind_of
from .base import Chips, LabeledSlider, ToolDialog, names, pil_to_qimage, run_bg, section
from .canvas import BoxView


# ------------------------------------------------------------------ compress
class _Est(QObject):
    done = Signal(int, int)


class CompressDialog(ToolDialog):
    def __init__(self, paths: list[str]):
        kind = kind_of(paths[0])
        self.kind = "animated" if kind == "animated" else kind
        total = sum(os.path.getsize(p) for p in paths if os.path.isfile(p))
        super().__init__("Compress", f"{names(paths)} · {human_size(total)}", "Compress")
        self.paths = paths
        self.total = total
        self.mode = Chips(["quality", "size", "percent"], "quality",
                          {"quality": "Quality", "size": "Target size", "percent": "Percent"})
        self.mode.changed.connect(self._mode)
        self.body.addWidget(self.mode)
        self.stack = QStackedWidget()
        # quality page
        qp = QWidget()
        ql = QVBoxLayout(qp)
        ql.setContentsMargins(0, 0, 0, 0)
        if self.kind == "pdf":
            self.level = Chips(["light", "balanced", "strong", "extreme"], "balanced",
                               {"light": "Light", "balanced": "Balanced", "strong": "Strong", "extreme": "Extreme"})
            ql.addWidget(self.level)
        elif self.kind == "video":
            self.q = LabeledSlider("Quality", 18, 40, 28)
            note = QLabel("lower number = better quality, bigger file (CRF)")
            note.setObjectName("Muted")
            ql.addWidget(self.q)
            ql.addWidget(note)
        elif self.kind == "audio":
            self.q = LabeledSlider("Bitrate", 32, 320, 128, " kbps", step=8)
            ql.addWidget(self.q)
        elif self.kind == "animated":
            self.q = LabeledSlider("Strength", 1, 100, 50, "%")
            ql.addWidget(self.q)
        else:
            self.q = LabeledSlider("Quality", 5, 100, 75)
            ql.addWidget(self.q)
        self.stack.addWidget(qp)
        # size page
        sp = QWidget()
        sl = QHBoxLayout(sp)
        sl.setContentsMargins(0, 0, 0, 0)
        lab = QLabel("Max file size")
        lab.setObjectName("Muted")
        self.size = QDoubleSpinBox()
        self.size.setRange(0.01, 100000)
        self.size.setDecimals(2)
        default_mb = max(0.05, round(total / len(paths) / 1024 / 1024 * 0.5, 2))
        self.size.setValue(default_mb)
        self.unit = QComboBox()
        self.unit.addItems(["MB", "KB"])
        presets = QHBoxLayout()
        sl.addWidget(lab)
        sl.addWidget(self.size)
        sl.addWidget(self.unit)
        for mb, txt in ((8, "Discord 8MB"), (10, "10 MB"), (25, "Email 25MB"), (50, "50 MB")):
            b = QPushButton(txt)
            b.setObjectName("Chip")
            b.clicked.connect(lambda _=False, mb=mb: (self.unit.setCurrentText("MB"), self.size.setValue(mb), self._estimate()))
            sl.addWidget(b)
        sl.addStretch(1)
        self.stack.addWidget(sp)
        # percent page
        pp = QWidget()
        pl = QVBoxLayout(pp)
        pl.setContentsMargins(0, 0, 0, 0)
        self.pct = LabeledSlider("Target", 5, 95, 50, "% of original")
        pl.addWidget(self.pct)
        self.stack.addWidget(pp)
        self.body.addWidget(self.stack)
        # extras
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        if self.kind in ("image",):
            self.fmt = QComboBox()
            for f in ("Keep format", "JPG", "WEBP", "AVIF", "PNG"):
                self.fmt.addItem(f)
            self.maxside = QComboBox()
            for f in ("Keep size", "4096", "2560", "1920", "1280", "800"):
                self.maxside.addItem(f if f == "Keep size" else f"Max {f} px", None if f == "Keep size" else int(f))
            form.addRow(self._m("Format"), self.fmt)
            form.addRow(self._m("Dimensions"), self.maxside)
            for w in (self.fmt, self.maxside):
                w.currentIndexChanged.connect(lambda _: self._estimate())
        if self.kind == "video":
            self.res = QComboBox()
            for t, v in (("Keep resolution", None), ("1080p", 1080), ("720p", 720), ("480p", 480), ("360p", 360)):
                self.res.addItem(t, v)
            self.abr = QComboBox()
            for v in (192, 128, 96, 64):
                self.abr.addItem(f"{v} kbps", v)
            self.abr.setCurrentIndex(1)
            form.addRow(self._m("Resolution"), self.res)
            form.addRow(self._m("Audio"), self.abr)
        if self.kind == "pdf":
            self.gray = QCheckBox("Convert images to greyscale")
            form.addRow("", self.gray)
        self.body.addLayout(form)
        self.est = QLabel("")
        self.est.setStyleSheet(f"color:{theme.accent()}; font-weight:600;")
        self.body.addWidget(self.est)
        self._et = QTimer(self, singleShot=True, interval=250, timeout=self._estimate_now)
        if hasattr(self, "q"):
            self.q.changed.connect(lambda _: self._estimate())
        self.size.valueChanged.connect(lambda _: self._estimate())
        self.pct.changed.connect(lambda _: self._estimate())
        self.resize(560, 10)
        self._estimate()

    def _m(self, t):
        l = QLabel(t)
        l.setObjectName("Muted")
        return l

    def _mode(self, m):
        self.stack.setCurrentIndex(["quality", "size", "percent"].index(m))
        self._estimate()

    def _estimate(self):
        if self.kind != "image":
            m = self.mode.value()
            if m == "size":
                self.est.setText(f"Aiming for ≤ {self.size.value():g} {self.unit.currentText()} per file")
            elif m == "percent":
                self.est.setText(f"Aiming for ≈ {human_size(self.total * self.pct.value() / 100)}")
            else:
                self.est.setText("")
            return
        self.est.setText("Estimating…")
        self._et.start()

    def _estimate_now(self):
        if self.mode.value() != "quality":
            m = self.mode.value()
            self.est.setText(f"Aiming for ≤ {self.size.value():g} {self.unit.currentText()}" if m == "size"
                             else f"Aiming for ≈ {human_size(self.total * self.pct.value() / 100)}")
            return
        src = self.paths[0]
        q = int(self.q.value())
        fmt = self._fmt()
        ms = self.maxside.currentData()
        stamp = time.time()
        self._stamp = stamp

        def work(push):
            from ..engines import images as IM
            from PIL import Image
            im = IM.load(src)
            if ms and max(im.size) > ms:
                im.thumbnail((ms, ms), Image.Resampling.LANCZOS)
            f = fmt or IM.same_fmt(src)
            n = len(IM.encode_size(im, f if f in ("JPG", "PNG", "WEBP", "AVIF", "HEIC") else "JPG", q))
            push((stamp, n, os.path.getsize(src)))
        run_bg(work, self._est_value)

    def _est_value(self, v):
        stamp, n, orig = v
        if stamp == self._stamp:
            self._est_done(n, orig)

    def _est_done(self, n, orig):
        pct = (n - orig) / orig * 100 if orig else 0
        more = " (first file)" if len(self.paths) > 1 else ""
        self.est.setText(f"{human_size(orig)} → ~{human_size(n)}  ({pct:+.0f}%){more}")

    def _fmt(self):
        if not hasattr(self, "fmt") or self.fmt.currentIndex() == 0:
            return None
        return self.fmt.currentText()

    def values(self) -> dict:
        m = self.mode.value()
        v = {"mode": m}
        if m == "quality":
            if self.kind == "pdf":
                v["mode"], v["value"] = "level", self.level.value()
            else:
                v["value"] = self.q.value()
        elif m == "size":
            val = self.size.value()
            v["value"] = val if self.unit.currentText() == "MB" else val / 1024
        else:
            v["value"] = self.pct.value()
        if self.kind == "image":
            v["fmt"] = self._fmt()
            v["max_side"] = self.maxside.currentData()
        if self.kind == "video":
            v["max_height"] = self.res.currentData()
            v["audio_kbps"] = self.abr.currentData()
        if self.kind == "pdf":
            v["gray"] = self.gray.isChecked()
        return v


# ------------------------------------------------------------------ resize
class ResizeDialog(ToolDialog):
    def __init__(self, paths: list[str], size: tuple[int, int] | None, video=False):
        super().__init__("Resize", names(paths) + (f" · {size[0]} × {size[1]}" if size else ""), "Resize")
        self.size0 = size or (1920, 1080)
        self.video = video
        if video:
            self.chips = Chips(["2160", "1440", "1080", "720", "480", "360", "240"], "720",
                               {k: f"{k}p" for k in ["2160", "1440", "1080", "720", "480", "360", "240"]})
            self.body.addWidget(self.chips)
        else:
            self.mode = Chips(["px", "pct"], "px", {"px": "Pixels", "pct": "Percent"})
            self.mode.changed.connect(self._mode)
            self.body.addWidget(self.mode)
            self.stack = QStackedWidget()
            px = QWidget()
            g = QHBoxLayout(px)
            g.setContentsMargins(0, 0, 0, 0)
            self.w = QSpinBox()
            self.h = QSpinBox()
            for s in (self.w, self.h):
                s.setRange(1, 50000)
                s.setSuffix(" px")
                s.setFixedWidth(110)
            self.w.setValue(self.size0[0])
            self.h.setValue(self.size0[1])
            self.lock = QToolButton()
            self.lock.setCheckable(True)
            self.lock.setChecked(True)
            self.lock.setIcon(icons.icon("lock", theme.TEXT, 16))
            self.lock.setToolTip("Keep aspect ratio")
            self.w.valueChanged.connect(self._w)
            self.h.valueChanged.connect(self._h)
            for wdg in (self._m("W"), self.w, self.lock, self._m("H"), self.h):
                g.addWidget(wdg)
            g.addStretch(1)
            self.stack.addWidget(px)
            pc = QWidget()
            pl = QVBoxLayout(pc)
            pl.setContentsMargins(0, 0, 0, 0)
            self.pct = LabeledSlider("Scale", 5, 400, 50, "%")
            pl.addWidget(self.pct)
            self.stack.addWidget(pc)
            self.body.addWidget(self.stack)
            pres = QHBoxLayout()
            for wpx in (3840, 2560, 1920, 1280, 1080, 800, 512):
                b = QPushButton(str(wpx))
                b.setObjectName("Chip")
                b.clicked.connect(lambda _=False, wpx=wpx: self.w.setValue(wpx))
                pres.addWidget(b)
            pres.addStretch(1)
            self.body.addLayout(pres)
            self.fmt = QComboBox()
            self.fmt.addItems(["Keep format", "JPG", "PNG", "WEBP", "AVIF"])
            r = QHBoxLayout()
            r.addWidget(self._m("Save as"))
            r.addWidget(self.fmt)
            r.addStretch(1)
            self.body.addLayout(r)
        self.resize(520, 10)

    def _m(self, t):
        l = QLabel(t)
        l.setObjectName("Muted")
        return l

    def _mode(self, m):
        self.stack.setCurrentIndex(0 if m == "px" else 1)

    def _w(self, v):
        if self.lock.isChecked():
            self.h.blockSignals(True)
            self.h.setValue(max(1, round(v * self.size0[1] / self.size0[0])))
            self.h.blockSignals(False)

    def _h(self, v):
        if self.lock.isChecked():
            self.w.blockSignals(True)
            self.w.setValue(max(1, round(v * self.size0[0] / self.size0[1])))
            self.w.blockSignals(False)

    def values(self):
        if self.video:
            return {"height": int(self.chips.value())}
        f = None if self.fmt.currentIndex() == 0 else self.fmt.currentText()
        if self.mode.value() == "pct":
            return {"percent": self.pct.value(), "fmt": f}
        return {"w": self.w.value(), "h": self.h.value(), "keep": False, "fmt": f}


# ------------------------------------------------------------------ metadata
class MetadataDialog(ToolDialog):
    """Shows everything; editable fields where we can write them back."""

    def __init__(self, src: str, data: dict, editable: list[str], gps: str | None = None, can_strip_gps=False):
        super().__init__("Metadata", Path(src).name, "Save changes")
        self.choice = None
        self.edits = {}
        t = QTableWidget(len(data), 2)
        t.horizontalHeader().hide()
        t.verticalHeader().hide()
        t.setShowGrid(False)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.rows = {}
        for i, (k, v) in enumerate(data.items()):
            a = QTableWidgetItem(str(k))
            a.setFlags(Qt.ItemFlag.ItemIsEnabled)
            a.setForeground(QColor(theme.MUTED))
            b = QTableWidgetItem(str(v))
            if k in editable:
                b.setToolTip("Double-click to edit")
            else:
                b.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            t.setItem(i, 0, a)
            t.setItem(i, 1, b)
            self.rows[k] = b
        # make sure every editable field appears even if empty
        for k in editable:
            if k not in data:
                r = t.rowCount()
                t.insertRow(r)
                a = QTableWidgetItem(k)
                a.setFlags(Qt.ItemFlag.ItemIsEnabled)
                a.setForeground(QColor(theme.MUTED))
                t.setItem(r, 0, a)
                b = QTableWidgetItem("")
                t.setItem(r, 1, b)
                self.rows[k] = b
        self.table = t
        self.orig = {k: self.rows[k].text() for k in editable}
        self.editable = editable
        self.body.addWidget(t, 1)
        row = QHBoxLayout()
        if gps:
            m = QPushButton("  Open location in maps")
            m.setIcon(icons.icon("pin", theme.TEXT, 16))
            m.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(f"https://www.openstreetmap.org/?mlat={gps.split(',')[0].strip()}&mlon={gps.split(',')[1].strip()}#map=15")))
            row.addWidget(m)
        if can_strip_gps:
            g = QPushButton("Remove location")
            g.clicked.connect(lambda: self._pick("strip_gps"))
            row.addWidget(g)
        s = QPushButton("Remove all metadata")
        s.setObjectName("Danger")
        s.clicked.connect(lambda: self._pick("strip_all"))
        row.addWidget(s)
        row.addStretch(1)
        self.body.addLayout(row)
        if not editable:
            self.ok_btn.hide()
        self.resize(620, 560)

    def _pick(self, c):
        self.choice = c
        self.accept()

    def on_accept(self):
        self.edits = {k: self.rows[k].text() for k in self.editable if self.rows[k].text() != self.orig.get(k, "")}
        if not self.edits:
            self.status.setText("Nothing changed")
            return
        self.choice = "edit"
        self.accept()


# ------------------------------------------------------------------ merge / join order
class OrderDialog(ToolDialog):
    def __init__(self, paths: list[str], title="Merge", primary="Merge", extra_options=None):
        super().__init__(title, f"{len(paths)} files · drag to reorder", primary)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for p in sorted(paths, key=lambda x: natural_key(Path(x).name)):
            it = QListWidgetItem(f"{Path(p).name}    ·  {human_size(os.path.getsize(p)) if os.path.isfile(p) else ''}")
            it.setData(Qt.ItemDataRole.UserRole, p)
            self.list.addItem(it)
        self.body.addWidget(self.list, 1)
        row = QHBoxLayout()
        for ic, tip, fn in (("up", "Move up", lambda: self._move(-1)), ("down", "Move down", lambda: self._move(1)),
                            ("trash", "Remove", self._remove)):
            b = QToolButton()
            b.setIcon(icons.icon(ic, theme.TEXT, 16))
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        srt = QPushButton("Sort A→Z")
        srt.clicked.connect(lambda: self.list.sortItems())
        row.addWidget(srt)
        row.addStretch(1)
        self.opt = None
        if extra_options:
            self.opt = Chips(*extra_options)
            row.addWidget(self.opt)
        self.body.addLayout(row)
        self.resize(560, 460)

    def _move(self, d):
        r = self.list.currentRow()
        if r < 0:
            return
        n = r + d
        if 0 <= n < self.list.count():
            it = self.list.takeItem(r)
            self.list.insertItem(n, it)
            self.list.setCurrentRow(n)

    def _remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def paths(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ------------------------------------------------------------------ PDF organize
class _Thumbs(QObject):
    thumb = Signal(int, object)


class OrganizeDialog(ToolDialog):
    def __init__(self, src: str):
        super().__init__("Organize", Path(src).name + " · drag pages to reorder", "Save", reset=True)
        self.src = src
        self.list = QListWidget()
        self.list.setViewMode(QListView.ViewMode.IconMode)
        self.list.setIconSize(QSize(130, 170))
        self.list.setGridSize(QSize(158, 210))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Snap)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setSpacing(6)
        self.body.addWidget(self.list, 1)
        bar = QHBoxLayout()
        for ic, tip, fn in (("rotate", "Rotate left", lambda: self._rot(-90)), ("reverse", "Rotate right", lambda: self._rot(90)),
                            ("trash", "Delete pages", self._delete)):
            b = QPushButton("  " + tip)
            b.setIcon(icons.icon(ic, theme.TEXT, 16))
            b.clicked.connect(fn)
            bar.addWidget(b)
        rev = QPushButton("Reverse order")
        rev.clicked.connect(self._reverse)
        bar.addWidget(rev)
        bar.addStretch(1)
        self.body.addLayout(bar)
        self.images = {}
        self.rot = {}
        self._fill()
        self.resize(900, 680)

    def _fill(self):
        from ..engines import pdfs
        import pymupdf
        self.list.clear()
        with pymupdf.open(self.src) as d:
            n = d.page_count
        for i in range(n):
            it = QListWidgetItem(f"{i + 1}")
            it.setData(Qt.ItemDataRole.UserRole, i)
            it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.list.addItem(it)
            self.rot[i] = 0

        src = self.src

        def work(push):
            for i, im in pdfs.thumbnails(src, 130):
                push((i, im))
        run_bg(work, self._got_v)

    def _got_v(self, v):
        self._got(*v)

    def _got(self, i, im):
        self.images[i] = QPixmap.fromImage(pil_to_qimage(im))
        self._refresh_icon(i)

    def _refresh_icon(self, i):
        pm = self.images.get(i)
        if pm is None:
            return
        from PySide6.QtGui import QTransform
        if self.rot.get(i):
            pm = pm.transformed(QTransform().rotate(self.rot[i]), Qt.TransformationMode.SmoothTransformation)
        for r in range(self.list.count()):
            it = self.list.item(r)
            if it.data(Qt.ItemDataRole.UserRole) == i:
                it.setIcon(QIcon(pm))

    def _rot(self, d):
        for it in self.list.selectedItems():
            i = it.data(Qt.ItemDataRole.UserRole)
            self.rot[i] = (self.rot[i] + d) % 360
            self._refresh_icon(i)

    def _delete(self):
        if len(self.list.selectedItems()) >= self.list.count():
            self.status.setText("Can't delete every page")
            return
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def _reverse(self):
        items = [self.list.takeItem(0) for _ in range(self.list.count())]
        for it in reversed(items):
            self.list.addItem(it)

    def on_reset(self):
        self._fill()

    def plan(self):
        out = []
        for r in range(self.list.count()):
            i = self.list.item(r).data(Qt.ItemDataRole.UserRole)
            out.append((i, self.rot.get(i, 0)))
        return out


# ------------------------------------------------------------------ PDF redact
class PdfRedactDialog(ToolDialog):
    def __init__(self, src: str):
        super().__init__("Redact", Path(src).name, "Redact", reset=True)
        from ..engines import pdfs
        self.src = src
        self.page = 0
        self.areas: dict[int, list] = {}
        im, self.zoom, self.n, _ = pdfs.render_page(src, 0)
        top = QHBoxLayout()
        left = QVBoxLayout()
        self.view = BoxView(pil_to_qimage(im))
        self.view.changed.connect(self._store)
        left.addWidget(self.view, 1)
        nav = QHBoxLayout()
        self.prev_b = QPushButton("‹ Prev")
        self.next_b = QPushButton("Next ›")
        self.page_lab = QLabel()
        self.page_lab.setObjectName("Muted")
        self.prev_b.clicked.connect(lambda: self._go(-1))
        self.next_b.clicked.connect(lambda: self._go(1))
        nav.addWidget(self.prev_b)
        nav.addStretch(1)
        nav.addWidget(self.page_lab)
        nav.addStretch(1)
        nav.addWidget(self.next_b)
        left.addLayout(nav)
        top.addLayout(left, 1)
        side = QVBoxLayout()
        side.addWidget(section("Find & remove text"))
        self.terms = QPlainTextEdit()
        self.terms.setPlaceholderText("One word or phrase per line…")
        self.terms.setFixedHeight(110)
        side.addWidget(self.terms)
        side.addWidget(section("Also remove"))
        self.presets = {}
        for k in pdfs.REDACT_PRESETS:
            c = QCheckBox(k)
            self.presets[k] = c
            side.addWidget(c)
        side.addSpacing(8)
        side.addWidget(section("Areas"))
        hint = QLabel("Drag boxes on the page to black out anything (images, signatures…). Right-click a box to remove.")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        side.addWidget(hint)
        side.addStretch(1)
        note = QLabel("This is real redaction - the text underneath is deleted, not just covered.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{theme.accent()};")
        side.addWidget(note)
        sw = QWidget()
        sw.setLayout(side)
        sw.setFixedWidth(280)
        top.addWidget(sw)
        self.body.addLayout(top, 1)
        self._update_nav()
        self.resize(1000, 760)

    def _store(self):
        z = self.zoom
        self.areas[self.page] = [(b.x() / z, b.y() / z, b.right() / z, b.bottom() / z) for b in self.view.boxes]
        self._update_nav()

    def _go(self, d):
        from ..engines import pdfs
        np_ = self.page + d
        if not (0 <= np_ < self.n):
            return
        self.page = np_
        im, self.zoom, _, _ = pdfs.render_page(self.src, self.page)
        self.view.set_image(pil_to_qimage(im))
        z = self.zoom
        self.view.boxes = [QRectF(QPointF(a * z, b * z), QPointF(c * z, d_ * z)) for a, b, c, d_ in self.areas.get(self.page, [])]
        self.view.update()
        self._update_nav()

    def _update_nav(self):
        total = sum(len(v) for v in self.areas.values())
        self.page_lab.setText(f"Page {self.page + 1} of {self.n}" + (f"  ·  {total} box(es)" if total else ""))
        self.prev_b.setEnabled(self.page > 0)
        self.next_b.setEnabled(self.page < self.n - 1)

    def on_reset(self):
        self.areas.clear()
        self.view.boxes.clear()
        self.view.update()
        self.terms.clear()
        for c in self.presets.values():
            c.setChecked(False)
        self._update_nav()

    def values(self):
        from ..engines import pdfs
        terms = [t for t in self.terms.toPlainText().splitlines() if t.strip()]
        pats = [pdfs.REDACT_PRESETS[k] for k, c in self.presets.items() if c.isChecked()]
        return terms, pats, {k: v for k, v in self.areas.items() if v}

    def on_accept(self):
        t, p, a = self.values()
        if not (t or p or a):
            self.status.setText("Nothing to redact yet")
            return
        self.accept()


# ------------------------------------------------------------------ rename
class RenameDialog(ToolDialog):
    def __init__(self, paths: list[str]):
        super().__init__("Rename", names(paths), "Rename")
        self.paths = paths
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        self.pattern = QLineEdit("{name}")
        self.pattern.setToolTip("{name} original name · {n} counter · {date} modified date · {ext} extension")
        self.find = QLineEdit()
        self.repl = QLineEdit()
        self.start = QSpinBox()
        self.start.setRange(0, 100000)
        self.start.setValue(1)
        self.pad = QSpinBox()
        self.pad.setRange(1, 6)
        self.pad.setValue(2)
        self.case = QComboBox()
        self.case.addItems(["Keep case", "lower case", "UPPER CASE", "Title Case"])
        for lab, w in (("Pattern", self.pattern), ("Find", self.find), ("Replace with", self.repl),
                       ("Counter starts", self.start), ("Counter digits", self.pad), ("Case", self.case)):
            l = QLabel(lab)
            l.setObjectName("Muted")
            form.addRow(l, w)
        self.body.addLayout(form)
        tip = QLabel("Tokens: {name} {n} {date} {ext} {parent}")
        tip.setObjectName("Muted")
        self.body.addWidget(tip)
        self.preview = QTableWidget(0, 2)
        self.preview.setHorizontalHeaderLabels(["Before", "After"])
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.preview.verticalHeader().hide()
        self.preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.body.addWidget(self.preview, 1)
        for w in (self.pattern, self.find, self.repl):
            w.textChanged.connect(self._refresh)
        for w in (self.start, self.pad):
            w.valueChanged.connect(self._refresh)
        self.case.currentIndexChanged.connect(self._refresh)
        self.resize(640, 520)
        self._refresh()

    def plan(self) -> list[tuple[str, str]]:
        out = []
        for i, p in enumerate(sorted(self.paths, key=lambda x: natural_key(Path(x).name))):
            P = Path(p)
            stem, ext = (P.name, "") if P.is_dir() else (P.stem, P.suffix)
            date = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(p)))
            name = self.pattern.text() or "{name}"
            name = (name.replace("{name}", stem).replace("{n}", str(self.start.value() + i).zfill(self.pad.value()))
                    .replace("{date}", date).replace("{ext}", ext.lstrip(".")).replace("{parent}", P.parent.name))
            if self.find.text():
                name = name.replace(self.find.text(), self.repl.text())
            c = self.case.currentIndex()
            if c == 1:
                name = name.lower()
            elif c == 2:
                name = name.upper()
            elif c == 3:
                name = name.title()
            name = re.sub(r'[<>:"/\\|?*]', "_", name).strip() or stem
            out.append((p, str(P.with_name(name + ext))))
        return out

    def _refresh(self):
        plan = self.plan()
        self.preview.setRowCount(len(plan))
        targets = [b for _, b in plan]
        clash = len(set(t.lower() for t in targets)) != len(targets)
        for r, (a, b) in enumerate(plan):
            self.preview.setItem(r, 0, QTableWidgetItem(Path(a).name))
            it = QTableWidgetItem(Path(b).name)
            if Path(b).exists() and Path(b) != Path(a):
                it.setForeground(QColor(theme.DANGER))
                clash = True
            self.preview.setItem(r, 1, it)
        self.ok_btn.setEnabled(not clash)
        self.status.setText("Name clash - tweak the pattern" if clash else "")


# ------------------------------------------------------------------ checksum
class _Hash(QObject):
    done = Signal(str, str, str)


class ChecksumDialog(ToolDialog):
    def __init__(self, paths: list[str]):
        super().__init__("Checksum", names(paths), "Done")
        self.cancel_btn.hide()
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["File", "Algorithm", "Hash"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.body.addWidget(self.table, 1)
        cmp = QHBoxLayout()
        self.compare = QLineEdit()
        self.compare.setPlaceholderText("Paste a hash to compare…")
        self.compare.textChanged.connect(self._cmp)
        cmp.addWidget(self.compare)
        self.match = QLabel("")
        cmp.addWidget(self.match)
        self.body.addLayout(cmp)
        copy = QPushButton("Copy SHA-256")
        copy.clicked.connect(self._copy)
        self.layout().itemAt(2).layout().insertWidget(2, copy)
        self.hashes = []
        plist = list(paths)
        run_bg(lambda push: ChecksumDialog._work(plist, push), self._add_v)
        self.status.setText("Hashing…")
        self.resize(900, 360)

    @staticmethod
    def _work(paths, push):
        for p in paths:
            if not os.path.isfile(p):
                continue
            hs = {"MD5": hashlib.md5(), "SHA-1": hashlib.sha1(), "SHA-256": hashlib.sha256()}
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    for h in hs.values():
                        h.update(chunk)
            for k, h in hs.items():
                push((Path(p).name, k, h.hexdigest()))

    def _add_v(self, v):
        self._add(*v)

    def _add(self, name, alg, hx):
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, v in enumerate((name, alg, hx)):
            self.table.setItem(r, c, QTableWidgetItem(v))
        self.hashes.append((name, alg, hx))
        self.status.setText("")
        self._cmp(self.compare.text())

    def _cmp(self, t):
        t = t.strip().lower()
        if not t:
            self.match.setText("")
            return
        hit = [h for h in self.hashes if h[2] == t]
        self.match.setText(f"✓ matches {hit[0][0]} ({hit[0][1]})" if hit else "✗ no match")
        self.match.setStyleSheet(f"color:{theme.SUCCESS if hit else theme.DANGER}; font-weight:600;")

    def _copy(self):
        s = "\n".join(f"{h[2]}  {h[0]}" for h in self.hashes if h[1] == "SHA-256")
        QApplication.clipboard().setText(s)
        self.status.setText("Copied")
