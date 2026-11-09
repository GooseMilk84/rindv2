"""Settings window."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, icons, theme, winhooks
from ..config import settings
from .base import Chips, LabeledSlider, section

ACCENTS = {"Tangerine": "#ff7a1a", "Blue": "#0a84ff", "Green": "#30d158", "Purple": "#bf5af2", "Pink": "#ff375f",
           "Teal": "#40c8e0", "Yellow": "#ffd60a"}
KEYS = ["shift", "ctrl", "alt", "win"]
KEY_LABELS = {"shift": "Shift", "ctrl": "Ctrl", "alt": "Alt", "win": "Win"}


class SettingsWindow(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__(None)
        self.setWindowTitle(f"{APP_NAME} settings")
        self.setObjectName("Root")
        from .. import glass
        glass.prepare_window(self)
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        head = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(icons.pixmap("app", theme.accent(), 26))
        head.addWidget(logo)
        t = QLabel(APP_NAME)
        t.setObjectName("Title")
        head.addWidget(t)
        v = QLabel(f"v{__version__} · everything runs on this PC")
        v.setObjectName("Muted")
        head.addWidget(v)
        head.addStretch(1)
        lay.addLayout(head)
        tabs = QTabWidget()
        tabs.addTab(self._general(), "General")
        tabs.addTab(self._keys(), "Keys")
        tabs.addTab(self._output(), "Output")
        tabs.addTab(self._integration(), "Windows")
        tabs.addTab(self._history(), "History")
        lay.addWidget(tabs, 1)

    def showEvent(self, e):
        super().showEvent(e)
        theme.dark_titlebar(self)
        self._fill_history()

    # ------------------------------------------------------------ helpers
    def _form(self):
        w = QWidget()
        f = QFormLayout(w)
        f.setContentsMargins(6, 14, 6, 6)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(12)
        return w, f

    def _m(self, t):
        l = QLabel(t)
        l.setObjectName("Muted")
        return l

    def _check(self, key, text, on_change=None):
        c = QCheckBox(text)
        c.setChecked(bool(settings.get(key)))

        def ch(v):
            settings[key] = bool(v)
            settings.save()
            if on_change:
                on_change(bool(v))
            self.changed.emit()
        c.toggled.connect(ch)
        return c

    def _combo(self, key, options, labels=None, cast=str):
        c = QComboBox()
        for o in options:
            c.addItem((labels or {}).get(o, str(o)), o)
        c.setCurrentIndex(max(0, c.findData(settings.get(key))))

        def ch(_):
            settings[key] = c.currentData()
            settings.save()
            self.changed.emit()
        c.currentIndexChanged.connect(ch)
        return c

    def _spin(self, key, lo, hi, suffix=""):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(int(settings.get(key)))
        s.setSuffix(suffix)

        def ch(v):
            settings[key] = int(v)
            settings.save()
        s.valueChanged.connect(ch)
        return s

    # ------------------------------------------------------------ tabs
    def _general(self):
        w, f = self._form()
        f.addRow("", self._check("start_with_windows", "Start Rind when Windows starts", winhooks.set_startup))
        f.addRow("", self._check("show_toasts", "Show progress && result pop-ups"))
        f.addRow("", self._check("reveal_after", "Open the folder after each job"))
        ws = LabeledSlider("", 300, 520, settings.get("wheel_size", 380), " px", step=10)
        ws.lab.hide()
        ws.changed.connect(lambda v: (settings.__setitem__("wheel_size", int(v)), settings.save()))
        f.addRow(self._m("Wheel size"), ws)
        acc_now = next((k for k, v in ACCENTS.items() if v == settings.get("accent")), "Tangerine")
        ac = Chips(list(ACCENTS.keys()), acc_now)

        def set_acc(k):
            settings["accent"] = ACCENTS[k]
            settings.save()
            from PySide6.QtWidgets import QApplication
            theme.apply(QApplication.instance())
            self.changed.emit()
        ac.changed.connect(set_acc)
        f.addRow(self._m("Accent"), ac)
        f.addRow("", self._check("glass", "Liquid glass (blurs what's behind Rind's windows)"))
        f.addRow("", self._check("glass_backdrop", "Glass wheel shows a blurred view of what's under it"))
        f.addRow(self._m("Window glass"), self._combo("glass_style", ["acrylic", "mica"], {"acrylic": "Acrylic (clearer)", "mica": "Mica (subtle)"}))
        f.addRow("", self._check("confirm_delete", "Ask before deleting files"))
        return w

    def _keys(self):
        w, f = self._form()
        f.addRow(section("While dragging a file, press…"))
        for key, lab in (("key_convert", "Convert wheel"), ("key_tools", "Tools wheel"), ("key_actions", "Actions wheel")):
            f.addRow(self._m(lab), self._combo(key, KEYS, KEY_LABELS))
        hk = QLineEdit(settings.get("hotkey", ""))
        hk.setPlaceholderText("e.g. ctrl+alt+space")

        def save_hk():
            settings["hotkey"] = hk.text().strip().lower()
            settings.save()
            ok = winhooks.register_hotkey(settings["hotkey"])
            hk_state.setText("registered" if ok else ("couldn't register - try another combo" if winhooks.IS_WIN else ""))
        hk.editingFinished.connect(save_hk)
        hk_state = QLabel("")
        hk_state.setObjectName("Muted")
        f.addRow(section("Keyboard"))
        f.addRow(self._m("Wheel for selected files"), hk)
        f.addRow("", hk_state)
        tip = QLabel("Select files in Explorer, press the hotkey, click a slice. Tab / ← → switch wheels, Esc closes.")
        tip.setWordWrap(True)
        tip.setObjectName("Muted")
        f.addRow("", tip)
        f.addRow(section("Drag detection"))
        f.addRow(self._m("Mode"), self._combo("drag_detection", ["auto", "strict", "loose"],
                                              {"auto": "Auto (recommended)", "strict": "Strict - only real file drags",
                                               "loose": "Loose - any drag"}))
        return w

    def _output(self):
        w, f = self._form()
        f.addRow(self._m("Save results"), self._combo("output_mode", ["beside", "subfolder"],
                                                       {"beside": "Next to the original", "subfolder": "In a subfolder"}))
        sub = QLineEdit(settings.get("output_subfolder", "Rind"))
        sub.editingFinished.connect(lambda: (settings.__setitem__("output_subfolder", sub.text().strip() or "Rind"), settings.save()))
        f.addRow(self._m("Subfolder name"), sub)
        f.addRow(section("Quality"))
        f.addRow(self._m("JPG quality"), self._spin("jpg_quality", 30, 100))
        f.addRow(self._m("WEBP quality"), self._spin("webp_quality", 30, 100))
        f.addRow(self._m("AVIF quality"), self._spin("avif_quality", 20, 100))
        f.addRow(self._m("PDF → image DPI"), self._spin("pdf_dpi", 72, 600, " dpi"))
        f.addRow(self._m("Video quality (CRF)"), self._spin("video_crf", 14, 40))
        f.addRow(self._m("Audio bitrate"), self._combo("audio_bitrate", ["320k", "256k", "192k", "160k", "128k", "96k"]))
        f.addRow("", self._check("use_office", "Use Microsoft Office for Word/PowerPoint/Excel files when it's installed"))
        return w

    def _integration(self):
        w, f = self._form()
        if not winhooks.IS_WIN:
            f.addRow(QLabel("These only apply on Windows."))
        cm = QCheckBox("Add “Rind…” to the right-click menu")
        cm.setChecked(winhooks.context_menu_installed())
        cm.toggled.connect(winhooks.set_context_menu)
        f.addRow("", cm)
        st = QCheckBox("Add Rind to “Send to”")
        st.setChecked(winhooks.IS_WIN and winhooks.sendto_path().exists())
        st.toggled.connect(winhooks.set_sendto)
        f.addRow("", st)
        note = QLabel("On Windows 11 the right-click entry lives under “Show more options”. Send to handles multi-select best.")
        note.setWordWrap(True)
        note.setObjectName("Muted")
        f.addRow("", note)
        f.addRow(section("Engines found"))
        from ..engines import media
        from ..engines.common import find_soffice
        from ..engines.archives import seven_zip
        try:
            ff = media.ffmpeg()
        except Exception:
            ff = None
        office = "check on use" if winhooks.IS_WIN else "n/a"
        for lab, val in (("ffmpeg (video/audio)", ff), ("LibreOffice (optional)", find_soffice()),
                         ("7-Zip (optional, for odd archives)", seven_zip()), ("Microsoft Office", office)):
            l = QLabel(("✓  " + str(val)) if val else "–  not found")
            l.setStyleSheet(f"color:{theme.SUCCESS if val else theme.MUTED};")
            l.setWordWrap(True)
            f.addRow(self._m(lab), l)
        return w

    def _history(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 14, 6, 6)
        self.hist = QListWidget()
        self.hist.itemDoubleClicked.connect(lambda it: __import__("rind.jobs", fromlist=["reveal"]).reveal(it.data(Qt.ItemDataRole.UserRole)))
        v.addWidget(self.hist, 1)
        row = QHBoxLayout()
        lab = QLabel("Double-click to show in folder")
        lab.setObjectName("Muted")
        row.addWidget(lab)
        row.addStretch(1)
        clr = QPushButton("Clear")
        clr.clicked.connect(lambda: (settings.__setitem__("history", []), settings.save(), self._fill_history()))
        row.addWidget(clr)
        v.addLayout(row)
        return w

    def _fill_history(self):
        if not hasattr(self, "hist"):
            return
        self.hist.clear()
        for h in settings.get("history", []):
            for o in h.get("outputs", [])[:3]:
                it = QListWidgetItem(f"{time.strftime('%d %b %H:%M', time.localtime(h.get('t', 0)))}   {h.get('title', '')}   →   {Path(o).name}")
                it.setData(Qt.ItemDataRole.UserRole, o)
                self.hist.addItem(it)
