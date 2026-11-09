"""Float: a little always-on-top shelf to park files and drag them out again later."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDrag, QPainter, QPen, QPixmap
from PySide6.QtCore import QFileInfo
from PySide6.QtWidgets import QAbstractItemView, QFileIconProvider, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QToolButton, QVBoxLayout, QWidget

from .. import icons, theme
from ..registry import human_size


class ShelfList(QListWidget):
    def __init__(self):
        super().__init__()
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(QSize(48, 48))
        self.setGridSize(QSize(92, 86))
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setMovement(QListView.Movement.Static)
        self.setStyleSheet("QListWidget { background: transparent; border: none; } QListWidget::item { color: #ddd; }")
        self.provider = QFileIconProvider()

    def add(self, path: str):
        for i in range(self.count()):
            if self.item(i).data(Qt.ItemDataRole.UserRole) == path:
                return
        it = QListWidgetItem(Path(path).name)
        it.setData(Qt.ItemDataRole.UserRole, path)
        it.setToolTip(f"{path}\n{human_size(os.path.getsize(path)) if os.path.isfile(path) else 'folder'}")
        ext = Path(path).suffix.lower()
        icon = None
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            pm = QPixmap(path)
            if not pm.isNull():
                icon = pm.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        it.setIcon(icon if icon is not None else self.provider.icon(QFileInfo(path)))
        self.addItem(it)

    def startDrag(self, actions):
        items = self.selectedItems()
        if not items:
            return
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(i.data(Qt.ItemDataRole.UserRole)) for i in items])
        d = QDrag(self)
        d.setMimeData(md)
        d.setPixmap(items[0].icon().pixmap(48, 48))
        d.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, Qt.DropAction.CopyAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and e.source() is not self:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.source() is not self:
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.add(os.path.normpath(u.toLocalFile()))
        e.setDropAction(Qt.DropAction.CopyAction)
        e.accept()


class Shelf(QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(330, 230)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 14)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icons.pixmap("float", theme.accent(), 16))
        head.addWidget(ic)
        t = QLabel("Float")
        t.setStyleSheet("font-weight:600;")
        head.addWidget(t)
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        head.addWidget(self.count)
        head.addStretch(1)
        clr = QToolButton()
        clr.setIcon(icons.icon("trash", theme.MUTED, 14))
        clr.setToolTip("Clear")
        clr.clicked.connect(lambda: (self.list.clear(), self._upd()))
        head.addWidget(clr)
        x = QToolButton()
        x.setIcon(icons.icon("x", theme.MUTED, 14))
        x.clicked.connect(self.hide)
        head.addWidget(x)
        lay.addLayout(head)
        self.list = ShelfList()
        self.list.model().rowsInserted.connect(self._upd)
        self.list.model().rowsRemoved.connect(self._upd)
        lay.addWidget(self.list, 1)
        hint = QLabel("drag files out when you need them · drop more in")
        hint.setObjectName("Muted")
        hint.setStyleSheet("font-size:11px;")
        lay.addWidget(hint)
        self._drag = None

    def _upd(self, *a):
        n = self.list.count()
        self.count.setText(f"· {n}" if n else "")

    def add(self, paths):
        for p in paths:
            self.list.add(p)
        if not self.isVisible():
            from PySide6.QtGui import QCursor, QGuiApplication
            g = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
            self.move(g.right() - self.width() - 24, g.top() + 80)
        self.show()
        self.raise_()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(QColor(24, 24, 27, 248))
        p.drawRoundedRect(r, 14, 14)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
