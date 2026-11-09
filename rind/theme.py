"""Dark theme: palette, stylesheet and window helpers."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from .config import settings

BG = "#111113"
SURFACE = "#18181b"
SURFACE2 = "#202024"
SURFACE3 = "#2a2a2f"
BORDER = "#2f2f35"
TEXT = "#ededf0"
MUTED = "#8d8d97"
DANGER = "#ff4d4f"
SUCCESS = "#34c759"


def accent() -> str:
    return settings.get("accent", "#ff7a1a")


def accent_hover() -> str:
    c = QColor(accent())
    return c.lighter(115).name()


def ui_font(size: float = 9.5, weight=QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"])
    f.setPointSizeF(size)
    f.setWeight(weight)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


def _chevron() -> str:
    """write a tiny chevron svg for combo boxes, return a url()-safe path"""
    try:
        from .config import data_dir
        p = data_dir() / "chevron.svg"
        if not p.exists():
            p.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="12" height="12" fill="none" '
                         'stroke="#8d8d97" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
                         '<path d="M6 9l6 6 6-6"/></svg>', encoding="utf-8")
        return str(p).replace("\\", "/")
    except Exception:
        return ""


def stylesheet() -> str:
    a = accent()
    ah = accent_hover()
    chev = _chevron()
    return f"""
    * {{ color: {TEXT}; }}
    QWidget#Root, QDialog, QMainWindow {{ background: {BG}; }}
    QToolTip {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; border-radius: 6px; }}
    QLabel#Title {{ font-size: 15px; font-weight: 600; }}
    QLabel#Muted, QLabel[muted="true"] {{ color: {MUTED}; }}
    QLabel#Section {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; }}
    QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
    QFrame#Sep {{ background: {BORDER}; max-height: 1px; min-height: 1px; }}

    QPushButton {{
        background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 8px;
        padding: 6px 14px; min-height: 18px;
    }}
    QPushButton:hover {{ background: {SURFACE3}; }}
    QPushButton:pressed {{ background: {BORDER}; }}
    QPushButton:disabled {{ color: {MUTED}; background: {SURFACE}; }}
    QPushButton#Primary {{ background: {a}; border: 1px solid {a}; color: #fff; font-weight: 600; }}
    QPushButton#Primary:hover {{ background: {ah}; border-color: {ah}; }}
    QPushButton#Primary:disabled {{ background: {SURFACE3}; border-color: {SURFACE3}; color: {MUTED}; }}
    QPushButton#Danger {{ color: {DANGER}; }}
    QPushButton#Chip {{
        background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 7px;
        padding: 3px 10px; min-height: 16px; font-size: 12px;
    }}
    QPushButton#Chip:checked {{ background: {a}; border-color: {a}; color: #fff; font-weight: 600; }}
    QPushButton#Chip:hover:!checked {{ background: {SURFACE3}; }}
    QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 5px; }}
    QToolButton:hover {{ background: {SURFACE3}; }}
    QToolButton:checked {{ background: {SURFACE3}; border-color: {a}; }}

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit, QPlainTextEdit, QTextEdit {{
        background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 7px;
        padding: 4px 8px; selection-background-color: {a};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {a}; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{ image: url("{chev}"); width: 12px; height: 12px; }}
    QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; selection-background-color: {a}; outline: none; }}

    QSlider::groove:horizontal {{ height: 4px; background: {SURFACE3}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {a}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: #fff; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {BORDER}; background: {SURFACE2}; }}
    QCheckBox::indicator {{ border-radius: 4px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {a}; border-color: {a}; }}

    QListWidget, QTreeWidget, QTableWidget {{
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; outline: none;
        alternate-background-color: {SURFACE2};
    }}
    QListWidget::item, QTreeWidget::item {{ padding: 5px; border-radius: 6px; }}
    QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{ background: {SURFACE3}; color: {TEXT}; }}
    QHeaderView::section {{ background: {SURFACE2}; color: {MUTED}; border: none; padding: 5px; }}
    QTableWidget {{ gridline-color: {BORDER}; }}

    QProgressBar {{ background: {SURFACE3}; border: none; border-radius: 3px; max-height: 6px; text-align: center; }}
    QProgressBar::chunk {{ background: {a}; border-radius: 3px; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {SURFACE3}; border-radius: 4px; min-height: 30px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {SURFACE3}; border-radius: 4px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 7px 14px; border-radius: 7px; margin-right: 2px; }}
    QTabBar::tab:selected {{ background: {SURFACE2}; color: {TEXT}; }}
    QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {SURFACE3}; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
    """


def apply(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Base, QColor(SURFACE2))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(SURFACE2))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(accent()))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(SURFACE2))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(MUTED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(MUTED))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(MUTED))
    app.setPalette(pal)
    app.setFont(ui_font())
    app.setStyleSheet(stylesheet())
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except Exception:
        pass


def dark_titlebar(w: QWidget):
    """Ask DWM for a dark title bar + rounded corners on Windows 10/11."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(w.winId())
        val = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old id)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4) == 0:
                break
        # caption colour to match background (Win11 22000+)
        col = QColor(BG)
        colorref = ctypes.c_int(col.red() | (col.green() << 8) | (col.blue() << 16))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref), 4)
    except Exception:
        pass
