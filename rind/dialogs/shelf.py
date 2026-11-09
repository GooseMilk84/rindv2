"""Float: a little always-on-top glass shelf to park files and drag them out again later."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QFileInfo, QMimeData, QPoint, QRectF, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDrag, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileIconProvider, QHBoxLayout, QLabel, QListView,
                               QListWidget, QListWidgetItem, QMenu, QToolButton, QVBoxLayout, QWidget)

from .. import glass, icons, theme
from ..registry import human_size

IMG = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".ico", ".tif", ".tiff")


def _local_paths(mime) -> list[str]:
    return [os.path.normpath(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()] if mime.hasUrls() else []


class ShelfList(QListWidget):
    def __init__(self, shelf):
        super().__init__()
        self.shelf = shelf
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(QSize(48, 48))
        self.setGridSize(QSize(92, 86))
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        # setMovement(Static) quietly switches drag OFF and makes the viewport refuse drops,
        # which is why the old shelf just rubber-banded. Turn both back on *after* it.
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setDropIndicatorShown(False)
        self.setStyleSheet("QListWidget { background: transparent; border: none; }"
                           "QListWidget::item { color: #e8e8ee; border-radius: 9px; }"
                           "QListWidget::item:selected { background: rgba(255,255,255,0.16); }"
                           "QListWidget::item:hover:!selected { background: rgba(255,255,255,0.07); }")
        self.provider = QFileIconProvider()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(lambda it: self.shelf.open_paths([it.data(Qt.ItemDataRole.UserRole)]))

    def paths(self, selected=False) -> list[str]:
        its = self.selectedItems() if selected else [self.item(i) for i in range(self.count())]
        return [i.data(Qt.ItemDataRole.UserRole) for i in its]

    def add(self, path: str):
        if not os.path.exists(path) or path in self.paths():
            return
        it = QListWidgetItem(Path(path).name or path)
        it.setData(Qt.ItemDataRole.UserRole, path)
        it.setToolTip(f"{path}\n{human_size(os.path.getsize(path)) if os.path.isfile(path) else 'folder'}")
        icon = None
        if Path(path).suffix.lower() in IMG:
            pm = QPixmap(path)
            if not pm.isNull():
                icon = pm.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        it.setIcon(icon if icon is not None else self.provider.icon(QFileInfo(path)))
        self.addItem(it)

    def remove(self, paths):
        paths = set(paths)
        for i in reversed(range(self.count())):
            if self.item(i).data(Qt.ItemDataRole.UserRole) in paths:
                self.takeItem(i)

    def prune(self):
        """files that got moved/deleted elsewhere drop off the shelf"""
        self.remove([p for p in self.paths() if not os.path.exists(p)])

    # ---- drag out
    def startDrag(self, actions):
        items = self.selectedItems()
        if not items:
            return
        paths = [i.data(Qt.ItemDataRole.UserRole) for i in items]
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(p) for p in paths])
        d = QDrag(self)
        d.setMimeData(md)
        pm = items[0].icon().pixmap(48, 48)
        d.setPixmap(pm)
        d.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        from .. import winhooks
        winhooks.INTERNAL_DRAG[0] = True        # lets Shift pop the wheel while dragging from here, like from Explorer
        try:
            res = d.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.CopyAction)
        finally:
            winhooks.INTERNAL_DRAG[0] = False
        if self.shelf.take_on_drop and res in (Qt.DropAction.CopyAction, Qt.DropAction.MoveAction):
            self.remove(paths)
        QTimer.singleShot(400, self.prune)       # Explorer moves on the same drive happen a beat later

    # ---- drop in
    def dragEnterEvent(self, e):
        if e.source() is self:
            e.ignore()
        elif _local_paths(e.mimeData()):
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.source() is not self and e.mimeData().hasUrls():
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e):
        for p in _local_paths(e.mimeData()):
            self.add(p)
        e.setDropAction(Qt.DropAction.CopyAction)   # we only *remember* the file, never move it
        e.accept()

    # ---- keys + menu
    def keyPressEvent(self, e):
        sel = self.paths(True)
        if e.key() == Qt.Key.Key_Delete and sel:
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.shelf.delete_files(sel)
            else:
                self.remove(sel)
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and sel:
            self.shelf.open_paths(sel)
        elif e.matches(QKeySequence.StandardKey.Copy) and sel:
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(p) for p in sel])
            QApplication.clipboard().setMimeData(md)
        elif e.matches(QKeySequence.StandardKey.Paste):
            for p in _local_paths(QApplication.clipboard().mimeData()):
                self.add(p)
        else:
            super().keyPressEvent(e)

    def _menu(self, pos):
        it = self.itemAt(pos)
        if it is not None and not it.isSelected():
            self.clearSelection()
            it.setSelected(True)
        sel = self.paths(True)
        m = QMenu(self)
        if sel:
            m.addAction(icons.icon("openwith", theme.TEXT, 14), "Open", lambda: self.shelf.open_paths(sel))
            m.addAction(icons.icon("folder", theme.TEXT, 14), "Show in folder", lambda: self.shelf.reveal(sel[0]))
            m.addAction(icons.icon("path", theme.TEXT, 14), "Copy path",
                        lambda: QApplication.clipboard().setText("\n".join(sel)))
            m.addAction(icons.icon("app", theme.TEXT, 14), "Open in Rind wheel…", lambda: self.shelf.wheel_for(sel))
            m.addSeparator()
            m.addAction(icons.icon("x", theme.TEXT, 14), "Remove from Float\tDel", lambda: self.remove(sel))
            a = m.addAction(icons.icon("trash", theme.DANGER, 14), "Delete file…\tShift+Del", lambda: self.shelf.delete_files(sel))
        else:
            m.addAction("Paste files\tCtrl+V", lambda: [self.add(p) for p in _local_paths(QApplication.clipboard().mimeData())])
            m.addAction("Clear shelf", self.clear).setEnabled(self.count() > 0)
        m.addSeparator()
        take = m.addAction("Take files off the shelf when dragged out")
        take.setCheckable(True)
        take.setChecked(self.shelf.take_on_drop)
        take.toggled.connect(self.shelf.set_take_on_drop)
        m.exec(self.viewport().mapToGlobal(pos))

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.count() == 0:
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = QRectF(self.viewport().rect()).adjusted(4, 4, -4, -4)
            pen = p.pen()
            pen.setColor(QColor(255, 255, 255, 50))
            pen.setStyle(Qt.PenStyle.DashLine)
            pen.setWidthF(1.2)
            p.setPen(pen)
            p.drawRoundedRect(r, 12, 12)
            p.setPen(QColor(255, 255, 255, 120))
            p.setFont(theme.ui_font(9.5))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "drop files here")
            p.end()


class Shelf(QWidget):
    R = 16

    def __init__(self, handler=None):
        super().__init__(None)
        from ..config import settings
        self.settings = settings
        self.handler = handler
        self.take_on_drop = bool(settings.get("float_take_on_drop", False))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setMinimumSize(250, 170)
        self.resize(340, 240)
        self._blur = False
        self._blur_tried = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("float", theme.accent(), 16))
        head.addWidget(ic)
        t = QLabel("Float")
        t.setStyleSheet("font-weight:600; background: transparent;")
        head.addWidget(t)
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        head.addWidget(self.count)
        head.addStretch(1)
        self.del_btn = QToolButton()
        self.del_btn.setIcon(icons.icon("trash", theme.MUTED, 14))
        self.del_btn.setToolTip("Delete the selected files… (Shift+Del)")
        self.del_btn.clicked.connect(lambda: self.delete_files(self.list.paths(True)))
        head.addWidget(self.del_btn)
        clr = QToolButton()
        clr.setIcon(icons.icon("x", theme.MUTED, 14))
        clr.setToolTip("Hide (files stay on the shelf)")
        clr.clicked.connect(self.hide)
        head.addWidget(clr)
        lay.addLayout(head)
        self.list = ShelfList(self)
        self.list.model().rowsInserted.connect(self._upd)
        self.list.model().rowsRemoved.connect(self._upd)
        self.list.itemSelectionChanged.connect(self._upd)
        lay.addWidget(self.list, 1)
        self.hint = QLabel("drag out to use · Del removes · right-click for more")
        self.hint.setObjectName("Muted")
        self.hint.setStyleSheet("font-size:11px; background: transparent;")
        lay.addWidget(self.hint)
        self._drag = None
        self._upd()

    # ---- helpers
    def _upd(self, *a):
        n = self.list.count()
        self.count.setText(f"· {n}" if n else "")
        self.del_btn.setEnabled(bool(self.list.selectedItems()))

    def set_take_on_drop(self, v):
        self.take_on_drop = bool(v)
        self.settings["float_take_on_drop"] = self.take_on_drop
        self.settings.save()

    def add(self, paths):
        for p in paths:
            self.list.add(p)
        if not self.isVisible():
            from PySide6.QtGui import QCursor, QGuiApplication
            g = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
            self.move(g.right() - self.width() - 24, g.top() + 80)
        self.show()
        self.raise_()

    def open_paths(self, paths):
        from ..jobs import open_file
        for p in paths:
            open_file(p)

    def reveal(self, p):
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(p)])

    def wheel_for(self, paths):
        app = QApplication.instance()
        rind = getattr(app, "rind", None)
        if rind is not None:
            rind.click_wheel.popup(self.mapToGlobal(self.rect().center()), paths, "convert")

    def delete_files(self, paths):
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        from ..tools import delete_files
        jobs = self.handler.jobs if self.handler is not None else None
        if jobs is None:
            from .. import trash
            trash.delete(paths)
            self.list.prune()
            return
        delete_files(paths, jobs, self, on_done=self.list.prune)

    # ---- window-level drops (header / edges count too)
    def dragEnterEvent(self, e):
        if _local_paths(e.mimeData()) and e.source() is not self.list:
            e.setDropAction(Qt.DropAction.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        self.dragEnterEvent(e)

    def dropEvent(self, e):
        for p in _local_paths(e.mimeData()):
            self.list.add(p)
        e.setDropAction(Qt.DropAction.CopyAction)
        e.accept()

    # ---- glass
    def showEvent(self, e):
        super().showEvent(e)
        if not self._blur_tried:
            self._blur_tried = True
            self._blur = glass.blur_behind(self, self.R)
            if not self._blur:      # painted card sits inside a shadow margin, so pad the content more
                self.layout().setContentsMargins(20, 14, 20, 20)
        self.list.prune()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._blur:
            glass.round_mask(self, self.R)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._blur:
            r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            glass.panel(p, r, self.R, tint=QColor(16, 16, 22, 148), shine=36)
        else:
            r = QRectF(self.rect()).adjusted(7, 5, -7, -9)
            glass.shadow(p, r, self.R, 7, 8)
            glass.panel(p, r, self.R, tint=QColor(22, 22, 28, 238), shine=34)
        p.end()

    # ---- move by dragging the header
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
