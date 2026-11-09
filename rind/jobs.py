"""Background jobs + the little progress/result toasts in the corner."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QPropertyAnimation, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal, QEasingCurve
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QProgressBar, QPushButton, QToolButton, QVBoxLayout, QWidget)

from . import icons, theme
from .config import settings
from .engines.common import Cancelled
from .registry import human_size


@dataclass
class Info:
    title: str
    data: object            # dict | list | str
    copyable: bool = True


@dataclass
class Result:
    outputs: list = field(default_factory=list)
    note: str = ""


class _Signals(QObject):
    progress = Signal(float, str)
    done = Signal(object)
    failed = Signal(str)


class _Runnable(QRunnable):
    def __init__(self, fn, cancel):
        super().__init__()
        self.fn = fn
        self.cancel = cancel
        self.sig = _Signals()

    def run(self):
        try:
            res = self.fn(lambda p, m="": self.sig.progress.emit(float(p), str(m)), self.cancel)
            self.sig.done.emit(res)
        except Cancelled:
            self.sig.failed.emit("Cancelled")
        except Exception as ex:
            msg = str(ex) or type(ex).__name__
            log(traceback.format_exc())
            self.sig.failed.emit(msg)


def log(text: str):
    try:
        from .config import data_dir
        with open(data_dir() / "log.txt", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except Exception:
        pass


def reveal(path: str):
    p = str(path)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(p)])


def open_file(path: str):
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        subprocess.Popen(["xdg-open", str(path)])


def trash(paths):
    try:
        from send2trash import send2trash
        for p in paths:
            if os.path.exists(p):
                send2trash(str(p))
    except Exception:
        import shutil
        for p in paths:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.exists(p):
                os.remove(p)


# ------------------------------------------------------------------ toast
class Toast(QWidget):
    closed = Signal(object)

    def __init__(self, title: str, subtitle: str, manager):
        super().__init__(None)
        self.manager = manager
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(360)
        self.cancel_event = threading.Event()
        self.outputs: list = []
        self.state = "run"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.icon = QLabel()
        self.icon.setFixedSize(22, 22)
        self.icon.setPixmap(icons.pixmap("clock", theme.accent(), 20))
        top.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight:600; font-size:13px;")
        self.sub = QLabel(subtitle)
        self.sub.setStyleSheet(f"color:{theme.MUTED}; font-size:12px;")
        self.sub.setWordWrap(True)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        top.addLayout(col, 1)
        self.x_btn = QToolButton()
        self.x_btn.setIcon(icons.icon("x", theme.MUTED, 14))
        self.x_btn.clicked.connect(self._x)
        top.addWidget(self.x_btn, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.btns = QHBoxLayout()
        self.btns.setSpacing(6)
        self.btns.addStretch(1)
        lay.addLayout(self.btns)
        self._hide_timer = QTimer(self, singleShot=True, timeout=self.close_toast)
        self.bar.setRange(0, 0)   # busy until the first real progress number arrives

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_blur_tried", False):
            self._blur_tried = True
            from . import glass
            self._blur = glass.blur_behind(self, 16)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_blur", False):
            from . import glass
            glass.round_mask(self, 16)

    def paintEvent(self, _):
        from . import glass
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if getattr(self, "_blur", False):
            glass.panel(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 16, tint=QColor(16, 16, 22, 155), shine=34)
        else:
            r = QRectF(self.rect()).adjusted(10, 8, -10, -12)
            glass.shadow(p, r, 16, 8, 7)
            glass.panel(p, r, 16, tint=QColor(22, 22, 28, 240), shine=32)
        p.end()

    def set_progress(self, frac: float, msg: str):
        if frac > 0:
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 1000)
            self.bar.setValue(int(max(0, min(1, frac)) * 1000))
        if msg:
            self.sub.setText(msg)

    def _x(self):
        if self.state == "run":
            self.cancel_event.set()
            self.sub.setText("Cancelling…")
        else:
            self.close_toast()

    def finish_ok(self, title: str, subtitle: str, outputs: list, undo=True):
        self.state = "ok"
        self.outputs = [str(o) for o in outputs]
        self.icon.setPixmap(icons.pixmap("check", theme.SUCCESS, 20))
        self.title.setText(title)
        self.sub.setText(subtitle)
        self.bar.hide()
        if outputs:
            first = self.outputs[0]
            b_open = QPushButton("Open")
            b_open.clicked.connect(lambda: (open_file(first), self.close_toast()))
            b_show = QPushButton("Show in folder")
            b_show.clicked.connect(lambda: (reveal(first), self.close_toast()))
            self.btns.addWidget(b_show)
            self.btns.addWidget(b_open)
            if undo:
                b_undo = QPushButton("Undo")
                b_undo.setObjectName("Danger")
                b_undo.clicked.connect(self._undo)
                self.btns.insertWidget(1, b_undo)
        self.adjustSize()
        self.manager.relayout()
        self._hide_timer.start(8000)

    def finish_fail(self, msg: str):
        self.state = "fail"
        cancelled = msg == "Cancelled"
        self.icon.setPixmap(icons.pixmap("x" if not cancelled else "info", theme.DANGER if not cancelled else theme.MUTED, 20))
        self.title.setText("Cancelled" if cancelled else "That didn't work")
        self.sub.setText("" if cancelled else msg[:400])
        self.bar.hide()
        self.adjustSize()
        self.manager.relayout()
        self._hide_timer.start(3000 if cancelled else 12000)

    def _undo(self):
        trash(self.outputs)
        self.title.setText("Undone")
        self.sub.setText("Moved the new file(s) to the Recycle Bin.")
        for i in reversed(range(self.btns.count())):
            w = self.btns.itemAt(i).widget()
            if w:
                w.deleteLater()
        self._hide_timer.start(2500)

    def enterEvent(self, e):
        if self._hide_timer.isActive():
            self._remaining = self._hide_timer.remainingTime()
            self._hide_timer.stop()

    def leaveEvent(self, e):
        if self.state != "run":
            self._hide_timer.start(3000)

    def close_toast(self):
        self.hide()
        self.closed.emit(self)
        self.deleteLater()


class JobManager(QObject):
    info_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(max(2, min(4, os.cpu_count() or 2)))
        self.toasts: list[Toast] = []
        self._keep = set()

    def relayout(self):
        scr = QGuiApplication.primaryScreen().availableGeometry()
        y = scr.bottom() - 6
        for t in reversed(self.toasts):
            t.adjustSize()
            y -= t.height()
            t.move(scr.right() - t.width() - 6, y)
            y -= 0

    def _remove(self, t):
        if t in self.toasts:
            self.toasts.remove(t)
        self.relayout()

    def notify(self, title: str, subtitle: str = ""):
        t = Toast(title, subtitle, self)
        t.closed.connect(self._remove)
        self.toasts.append(t)
        t.finish_ok(title, subtitle, [], undo=False)
        t._hide_timer.start(2500)
        t.show()
        self.relayout()

    def run(self, title: str, fn, sources: list[str] | None = None, verb: str = "Done", undo: bool = True):
        """fn(progress, cancel) -> list[Path] | Path | Result | Info | None"""
        sources = sources or []
        show = settings.get("show_toasts", True)
        t = Toast(title, Path(sources[0]).name + (f" + {len(sources) - 1} more" if len(sources) > 1 else "") if sources else "Working…", self)
        t.closed.connect(self._remove)
        self.toasts.append(t)
        if show:
            t.show()
            self.relayout()
        r = _Runnable(fn, t.cancel_event)
        self._keep.add(r)
        r.sig.progress.connect(t.set_progress)
        t0 = time.time()
        src_size = sum(os.path.getsize(s) for s in sources if os.path.isfile(s))

        def done(res):
            self._keep.discard(r)
            if isinstance(res, Info):
                t.close_toast()
                self.info_ready.emit(res)
                return
            note = ""
            if isinstance(res, Result):
                note = res.note
                res = res.outputs
            outs = res if isinstance(res, list) else ([res] if res else [])
            outs = [Path(o) for o in outs]
            sub = note
            if not sub and outs:
                if len(outs) == 1:
                    o = outs[0]
                    if o.is_file():
                        size = o.stat().st_size
                        delta = ""
                        if src_size and len(sources) == 1:
                            pct = (size - src_size) / src_size * 100
                            delta = f"  ({'+' if pct >= 0 else ''}{pct:.0f}%)"
                        sub = f"{o.name}\n{human_size(src_size) + ' → ' if src_size and len(sources) == 1 else ''}{human_size(size)}{delta}"
                    else:
                        n = sum(1 for _ in o.rglob('*') if _.is_file()) if o.is_dir() else 0
                        sub = f"{o.name}  ·  {n} file{'s' if n != 1 else ''}"
                else:
                    sub = f"{len(outs)} files created"
            secs = time.time() - t0
            t.finish_ok(f"{verb} in {secs:.1f}s" if secs >= 1 else verb, sub, outs, undo=undo)
            if not show:
                t.close_toast()
            if outs:
                settings.add_history({"t": time.time(), "title": title, "outputs": [str(o) for o in outs]})
                if settings.get("reveal_after"):
                    reveal(str(outs[0]))

        def failed(msg):
            self._keep.discard(r)
            t.finish_fail(msg)
            if not show:
                t.show()
                self.relayout()
        r.sig.done.connect(done)
        r.sig.failed.connect(failed)
        self.pool.start(r)
        return t
