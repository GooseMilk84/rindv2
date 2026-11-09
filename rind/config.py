"""Persistent settings stored as JSON in %APPDATA%\\Rind\\settings.json."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import APP_NAME

DEFAULTS = {
    # which key opens which wheel while dragging
    "key_convert": "shift",
    "key_tools": "ctrl",
    "key_actions": "alt",
    # output
    "output_mode": "beside",          # beside | subfolder
    "output_subfolder": "Rind",
    "jpg_quality": 92,
    "webp_quality": 90,
    "avif_quality": 70,
    "pdf_dpi": 200,
    "video_crf": 23,
    "audio_bitrate": "192k",
    # ui
    "accent": "#ff7a1a",
    "wheel_size": 380,
    "wheel_opacity": 0.97,
    "show_toasts": True,
    "reveal_after": False,
    "hotkey": "ctrl+alt+space",
    "drag_detection": "auto",         # auto | strict (needs drag image) | loose
    "start_with_windows": False,
    "use_office": True,               # use Word/PowerPoint/Excel via COM when installed
    "history": [],
}


def data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


class Settings(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        self.path = data_dir() / "settings.json"
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            for k, v in stored.items():
                if k in DEFAULTS:
                    self[k] = v
        except Exception:
            pass

    def save(self):
        try:
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(self), f, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def add_history(self, entry: dict):
        h = list(self.get("history", []))
        h.insert(0, entry)
        self["history"] = h[:40]
        self.save()


settings = Settings()
