"""Shared dialog chrome + small helpers."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
                               QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSizePolicy, QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import icons, theme


def pil_to_qimage(im) -> QImage:
    im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    q = QImage(data, im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888)
    return q.copy()


def qimage_to_pil(q: QImage):
    from PIL import Image
    q = q.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = q.constBits()
    return Image.frombuffer("RGBA", (q.width(), q.height()), bytes(ptr), "raw", "RGBA", q.bytesPerLine(), 1).copy()


def hsep() -> QFrame:
    f = QFrame()
    f.setObjectName("Sep")
    return f


def section(text: str) -> QLabel:
    l = QLabel(text.upper())
    l.setObjectName("Section")
    return l


class ToolDialog(QDialog):
    """Dark dialog with a header, a body and a footer (Reset · Cancel · Primary)."""

    def __init__(self, title: str, subtitle: str = "", primary: str = "Apply", parent=None, reset=False, width=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("Root")
        from .. import glass
        glass.prepare_window(self)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(12)
        head = QHBoxLayout()
        t = QLabel(title)
        t.setObjectName("Title")
        head.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("Muted")
            s.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            head.addSpacing(8)
            head.addWidget(s, 1)
        else:
            head.addStretch(1)
        outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body, 1)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.reset_btn = QPushButton("Reset")
        self.reset_btn.setVisible(reset)
        self.reset_btn.clicked.connect(self.on_reset)
        foot.addWidget(self.reset_btn)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        foot.addWidget(self.status, 1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton(primary)
        self.ok_btn.setObjectName("Primary")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self.on_accept)
        foot.addWidget(self.cancel_btn)
        foot.addWidget(self.ok_btn)
        outer.addLayout(foot)
        if width:
            self.resize(width, 10)

    def on_reset(self):
        pass

    def on_accept(self):
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        theme.dark_titlebar(self)
        # centre on the screen with the cursor
        from PySide6.QtGui import QCursor
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        if not getattr(self, "_placed", False):
            self._placed = True
            fr = self.frameGeometry()
            fr.moveCenter(g.center())
            self.move(fr.topLeft())
        self.raise_()
        self.activateWindow()

    def run(self) -> bool:
        return self.exec() == QDialog.DialogCode.Accepted


class Chips(QWidget):
    """A row of toggle chips (exclusive)."""
    changed = Signal(str)

    def __init__(self, options: list[str], current: str | None = None, labels: dict | None = None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for o in options:
            b = QPushButton((labels or {}).get(o, o))
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(b)
            self.buttons[o] = b
            b.clicked.connect(lambda _=False, o=o: self.changed.emit(o))
            lay.addWidget(b)
        lay.addStretch(1)
        self.set(current or options[0])

    def set(self, o: str):
        if o in self.buttons:
            self.buttons[o].setChecked(True)

    def value(self) -> str:
        for o, b in self.buttons.items():
            if b.isChecked():
                return o
        return ""


class LabeledSlider(QWidget):
    changed = Signal(float)

    def __init__(self, label: str, lo: float, hi: float, val: float, suffix: str = "", step: float = 1, decimals: int = 0):
        super().__init__()
        self.scale = 1 / step
        self.decimals = decimals
        self.suffix = suffix
        self.default = val
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.lab = QLabel(label)
        self.lab.setFixedWidth(92)
        self.lab.setObjectName("Muted")
        self.s = QSlider(Qt.Orientation.Horizontal)
        self.s.setRange(int(lo * self.scale), int(hi * self.scale))
        self.s.setValue(int(val * self.scale))
        self.v = QLabel()
        self.v.setFixedWidth(56)
        self.v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.lab)
        lay.addWidget(self.s, 1)
        lay.addWidget(self.v)
        self.s.valueChanged.connect(self._ch)
        self._ch(self.s.value())

    def _ch(self, iv):
        v = iv / self.scale
        self.v.setText(f"{v:.{self.decimals}f}{self.suffix}")
        self.changed.emit(v)

    def value(self) -> float:
        return self.s.value() / self.scale

    def set(self, v: float):
        self.s.setValue(int(v * self.scale))

    def reset(self):
        self.set(self.default)


class InfoDialog(ToolDialog):
    def __init__(self, title: str, data, parent=None):
        super().__init__(title, "", "Done", parent)
        self.cancel_btn.hide()
        self.copy_btn = QPushButton("Copy all")
        self.copy_btn.clicked.connect(self._copy)
        self.layout().itemAt(2).layout().insertWidget(2, self.copy_btn)
        self.text_dump = ""
        if isinstance(data, dict):
            t = QTableWidget(len(data), 2)
            t.horizontalHeader().hide()
            t.verticalHeader().hide()
            t.setShowGrid(False)
            t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            t.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            for i, (k, v) in enumerate(data.items()):
                a = QTableWidgetItem(str(k))
                a.setForeground(QColor(theme.MUTED))
                t.setItem(i, 0, a)
                t.setItem(i, 1, QTableWidgetItem(str(v)))
            t.resizeRowsToContents()
            self.body.addWidget(t)
            self.text_dump = "\n".join(f"{k}: {v}" for k, v in data.items())
            self.resize(520, min(640, 120 + 30 * len(data)))
        else:
            if isinstance(data, list):
                text = "\n".join(str(x) for x in data) or "(nothing found)"
            else:
                text = str(data)
            e = QPlainTextEdit(text)
            e.setReadOnly(True)
            self.body.addWidget(e)
            self.text_dump = text
            self.resize(560, 420)

    def _copy(self):
        QApplication.clipboard().setText(self.text_dump)
        self.status.setText("Copied")


class FormDialog(ToolDialog):
    """Quick form builder: fields = [(key, label, kind, default, extra)]
    kinds: text, password, int, float, check, combo, chips, slider"""

    def __init__(self, title: str, subtitle: str, fields: list, primary="Apply", parent=None, note: str = ""):
        super().__init__(title, subtitle, primary, parent)
        self.widgets = {}
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        for f in fields:
            key, label, kind, default = f[:4]
            extra = f[4] if len(f) > 4 else {}
            w = self._make(kind, default, extra)
            self.widgets[key] = (kind, w)
            lab = QLabel(label)
            lab.setObjectName("Muted")
            if kind == "check":
                form.addRow("", w)
            else:
                form.addRow(lab, w)
        self.body.addLayout(form)
        if note:
            n = QLabel(note)
            n.setObjectName("Muted")
            n.setWordWrap(True)
            self.body.addWidget(n)
        self.resize(max(420, self.sizeHint().width()), self.sizeHint().height())

    def _make(self, kind, default, extra):
        if kind in ("text", "password"):
            w = QLineEdit(str(default or ""))
            if kind == "password":
                w.setEchoMode(QLineEdit.EchoMode.Password)
            if extra.get("placeholder"):
                w.setPlaceholderText(extra["placeholder"])
            return w
        if kind == "int":
            w = QSpinBox()
            w.setRange(extra.get("min", -10 ** 9), extra.get("max", 10 ** 9))
            w.setValue(int(default or 0))
            if extra.get("suffix"):
                w.setSuffix(extra["suffix"])
            return w
        if kind == "float":
            w = QDoubleSpinBox()
            w.setDecimals(extra.get("decimals", 2))
            w.setRange(extra.get("min", -10 ** 9), extra.get("max", 10 ** 9))
            w.setSingleStep(extra.get("step", 0.1))
            w.setValue(float(default or 0))
            if extra.get("suffix"):
                w.setSuffix(extra["suffix"])
            return w
        if kind == "check":
            w = QCheckBox(extra.get("text", ""))
            w.setChecked(bool(default))
            return w
        if kind == "combo":
            w = QComboBox()
            for o in extra["options"]:
                w.addItem(extra.get("labels", {}).get(o, str(o)), o)
            idx = w.findData(default)
            w.setCurrentIndex(max(0, idx))
            return w
        if kind == "chips":
            return Chips(extra["options"], default, extra.get("labels"))
        if kind == "slider":
            return LabeledSlider("", extra.get("min", 0), extra.get("max", 100), default, extra.get("suffix", ""),
                                 extra.get("step", 1), extra.get("decimals", 0))
        raise ValueError(kind)

    def values(self) -> dict:
        out = {}
        for k, (kind, w) in self.widgets.items():
            if kind in ("text", "password"):
                out[k] = w.text()
            elif kind in ("int", "float"):
                out[k] = w.value()
            elif kind == "check":
                out[k] = w.isChecked()
            elif kind == "combo":
                out[k] = w.currentData()
            elif kind == "chips":
                out[k] = w.value()
            elif kind == "slider":
                out[k] = w.value()
        return out


def ask_form(title, subtitle, fields, primary="Apply", note="") -> dict | None:
    d = FormDialog(title, subtitle, fields, primary, note=note)
    return d.values() if d.run() else None


def names(paths: list[str]) -> str:
    if len(paths) == 1:
        return Path(paths[0]).name
    return f"{len(paths)} files"


# ------------------------------------------------------------------ background work without dangling widgets
# Worker threads must never hold the last reference to a dialog (Python would then destroy Qt
# widgets off the GUI thread and crash). So workers only get plain data plus a `push` function;
# results hop back to the GUI thread through one long-lived bridge and are delivered through a
# weak reference to the dialog's method.
import threading as _threading
import weakref as _weakref

from PySide6.QtCore import QObject as _QObject


class _Bridge(_QObject):
    sig = Signal(object, object)

    def __init__(self):
        super().__init__()
        self.sig.connect(self._deliver)

    @staticmethod
    def _deliver(ref, value):
        cb = ref()
        if cb is not None:
            try:
                cb(value)
            except RuntimeError:
                pass  # widget already gone


_BRIDGE = None


def _bridge():
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = _Bridge()
    return _BRIDGE


def run_bg(work, on_value):
    """work(push) runs in a thread; every push(v) calls on_value(v) on the GUI thread (if still alive)."""
    ref = _weakref.WeakMethod(on_value) if hasattr(on_value, "__self__") else (lambda: on_value)
    br = _bridge()

    def push(v):
        br.sig.emit(ref, v)

    def t():
        try:
            work(push)
        except Exception:
            pass
    _threading.Thread(target=t, daemon=True).start()


class DeleteDialog(ToolDialog):
    """Are you sure? Lists what's going, with a 'skip the Recycle Bin' box."""

    def __init__(self, paths: list[str], parent=None):
        n = len(paths)
        super().__init__(f"Delete {n} item{'s' if n != 1 else ''}?", "", "Delete", parent, width=420)
        from PySide6.QtWidgets import QCheckBox, QListWidget
        from ..registry import human_size, total_size
        lst = QListWidget()
        for p in paths[:200]:
            lst.addItem(os.path.basename(p.rstrip("\\/")) or p)
        if n > 200:
            lst.addItem(f"…and {n - 200} more")
        lst.setMaximumHeight(min(200, 26 + 26 * min(n, 6)))
        self.body.addWidget(lst)
        info = QLabel(f"{human_size(total_size(paths))} · they'll go to the Recycle Bin, so you can get them back.")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        self.body.addWidget(info)
        self.perm = QCheckBox("Delete permanently (skip the Recycle Bin)")
        self.perm.toggled.connect(lambda v: (info.setText(f"{human_size(total_size(paths))} · gone for good, no undo." if v else
                                                          f"{human_size(total_size(paths))} · they'll go to the Recycle Bin, so you can get them back."),
                                             self.ok_btn.setObjectName("Danger" if v else "Primary"),
                                             self.ok_btn.style().unpolish(self.ok_btn), self.ok_btn.style().polish(self.ok_btn)))
        self.body.addWidget(self.perm)

    @property
    def permanent(self) -> bool:
        return self.perm.isChecked()
