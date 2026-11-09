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
    # glass tokens: white overlays read as frosted panes on the acrylic backdrop,
    # and as neat dark greys on the solid fallback background
    g1, g2, g3 = "rgba(255,255,255,0.045)", "rgba(255,255,255,0.075)", "rgba(255,255,255,0.13)"
    edge, edge_hi = "rgba(255,255,255,0.10)", "rgba(255,255,255,0.24)"
    pane = "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 rgba(255,255,255,0.075), stop:1 rgba(255,255,255,0.03))"
    btn = "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 rgba(255,255,255,0.13), stop:1 rgba(255,255,255,0.055))"
    btn_h = "qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 rgba(255,255,255,0.19), stop:1 rgba(255,255,255,0.09))"
    acc = f"qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {QColor(a).lighter(122).name()}, stop:1 {QColor(a).darker(106).name()})"
    acc_h = f"qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {QColor(a).lighter(135).name()}, stop:1 {a})"
    return f"""
    * {{ color: {TEXT}; }}
    QWidget#Root, QDialog, QMainWindow {{ background: {BG}; }}
    QWidget#Root[glass="true"] {{ background: rgba(14,14,18,0.56); }}
    QToolTip {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; border-radius: 6px; }}
    QLabel#Title {{ font-size: 15px; font-weight: 600; }}
    QLabel#Muted, QLabel[muted="true"] {{ color: {MUTED}; }}
    QLabel#Section {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; }}
    QFrame#Card {{ background: {pane}; border: 1px solid {edge}; border-top-color: {edge_hi}; border-radius: 14px; }}
    QFrame#Sep {{ background: {edge}; max-height: 1px; min-height: 1px; }}

    QPushButton {{
        background: {btn}; border: 1px solid {edge}; border-top-color: {edge_hi}; border-radius: 10px;
        padding: 6px 15px; min-height: 18px;
    }}
    QPushButton:hover {{ background: {btn_h}; }}
    QPushButton:pressed {{ background: {g2}; border-top-color: {edge}; }}
    QPushButton:disabled {{ color: {MUTED}; background: {g1}; border-color: {g2}; }}
    QPushButton#Primary {{ background: {acc}; border: 1px solid {QColor(a).darker(115).name()};
        border-top-color: {QColor(a).lighter(150).name()}; color: #fff; font-weight: 600; }}
    QPushButton#Primary:hover {{ background: {acc_h}; }}
    QPushButton#Primary:disabled {{ background: {g2}; border-color: {g2}; color: {MUTED}; }}
    QPushButton#Danger {{ color: {DANGER}; }}
    QPushButton#Chip {{
        background: {btn}; border: 1px solid {edge}; border-top-color: {edge_hi}; border-radius: 9px;
        padding: 3px 11px; min-height: 16px; font-size: 12px;
    }}
    QPushButton#Chip:checked {{ background: {acc}; border-color: {QColor(a).darker(115).name()};
        border-top-color: {QColor(a).lighter(150).name()}; color: #fff; font-weight: 600; }}
    QPushButton#Chip:hover:!checked {{ background: {btn_h}; }}
    QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 9px; padding: 5px; }}
    QToolButton:hover {{ background: {btn}; border-color: {edge}; border-top-color: {edge_hi}; }}
    QToolButton:checked {{ background: {g3}; border-color: {a}; }}

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTimeEdit, QPlainTextEdit, QTextEdit {{
        background: rgba(0,0,0,0.22); border: 1px solid {edge}; border-bottom-color: {edge_hi}; border-radius: 9px;
        padding: 4px 8px; selection-background-color: {a};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {a}; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox::down-arrow {{ image: url("{chev}"); width: 12px; height: 12px; }}
    QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; selection-background-color: {a}; outline: none; }}

    QSlider::groove:horizontal {{ height: 5px; background: rgba(0,0,0,0.3); border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {acc}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: qradialgradient(cx:0.4,cy:0.3,radius:0.8, stop:0 #ffffff, stop:1 #d9d9e0);
        border: 1px solid rgba(255,255,255,0.6); width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}

    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {edge}; border-top-color: {edge_hi}; background: {g2}; }}
    QCheckBox::indicator {{ border-radius: 5px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {acc}; border-color: {a}; }}

    QListWidget, QTreeWidget, QTableWidget {{
        background: {pane}; border: 1px solid {edge}; border-top-color: {edge_hi}; border-radius: 12px; outline: none;
        alternate-background-color: {g1};
    }}
    QListWidget::item, QTreeWidget::item {{ padding: 5px; border-radius: 7px; }}
    QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{ background: {g3}; color: {TEXT}; }}
    QHeaderView::section {{ background: transparent; color: {MUTED}; border: none; padding: 5px; }}
    QTableWidget {{ gridline-color: {edge}; }}

    QProgressBar {{ background: rgba(0,0,0,0.3); border: none; border-radius: 3px; max-height: 6px; text-align: center; }}
    QProgressBar::chunk {{ background: {acc}; border-radius: 3px; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {g3}; border-radius: 4px; min-height: 30px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {g3}; border-radius: 4px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 7px 14px; border-radius: 9px; margin-right: 2px;
        border: 1px solid transparent; }}
    QTabBar::tab:selected {{ background: {btn}; color: {TEXT}; border-color: {edge}; border-top-color: {edge_hi}; }}
    QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 6px; }}
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
        if getattr(w, "_glass", False):
            from . import glass
            glass.apply_backdrop(w)
            return
        # caption colour to match background (Win11 22000+)
        col = QColor(BG)
        colorref = ctypes.c_int(col.red() | (col.green() << 8) | (col.blue() << 16))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref), 4)
    except Exception:
        pass
