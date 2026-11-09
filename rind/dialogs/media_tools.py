"""Timeline-based tools for video/audio: Trim, To GIF, Snapshot, Bleep, Split at points."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QSizePolicy,
                               QSpinBox, QToolButton, QVBoxLayout, QWidget)

from .. import icons, theme
from ..engines import media as M
from ..engines.common import NO_WINDOW
from .base import Chips, ToolDialog, run_bg


class _FrameReq:
    """plain shared state between the dialog and its frame worker (no Qt objects)"""
    t = None
    busy = False


class Timeline(QWidget):
    changed = Signal()
    seek = Signal(float)

    def __init__(self, duration: float, mode: str = "range"):
        super().__init__()
        self.dur = max(0.01, duration)
        self.mode = mode
        self.a, self.b = 0.0, self.dur
        self.play = 0.0
        self.ranges: list[list[float]] = []
        self.markers: list[float] = []
        self.thumbs: list[QPixmap] = []
        self.wave: list[float] = []
        self.drag = None
        self.setMinimumHeight(86)
        self.setMouseTracking(True)

    def track(self) -> QRectF:
        return QRectF(14, 10, self.width() - 28, self.height() - 34)

    def x_of(self, t):
        r = self.track()
        return r.left() + r.width() * t / self.dur

    def t_of(self, x):
        r = self.track()
        return max(0.0, min(self.dur, (x - r.left()) / r.width() * self.dur))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.track()
        clip = QPainterPath()
        clip.addRoundedRect(r, 8, 8)
        p.save()
        p.setClipPath(clip)
        p.fillRect(r, QColor("#1b1b1f"))
        if self.thumbs:
            w = r.width() / len(self.thumbs)
            for i, pm in enumerate(self.thumbs):
                tr = QRectF(r.left() + i * w, r.top(), w + 1, r.height())
                src = QRectF(pm.rect())
                # cover
                s = max(tr.width() / src.width(), tr.height() / src.height())
                sw, sh = tr.width() / s, tr.height() / s
                p.drawPixmap(tr, pm, QRectF((src.width() - sw) / 2, (src.height() - sh) / 2, sw, sh))
        elif self.wave:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#5a5a66"))
            n = len(self.wave)
            bw = r.width() / n
            mid = r.center().y()
            for i, v in enumerate(self.wave):
                h = max(1.0, v * r.height() * 0.92)
                p.drawRect(QRectF(r.left() + i * bw, mid - h / 2, max(1.0, bw * 0.8), h))
        acc = QColor(theme.accent())
        if self.mode in ("range", "gif"):
            xa, xb = self.x_of(self.a), self.x_of(self.b)
            p.fillRect(QRectF(r.left(), r.top(), xa - r.left(), r.height()), QColor(0, 0, 0, 170))
            p.fillRect(QRectF(xb, r.top(), r.right() - xb, r.height()), QColor(0, 0, 0, 170))
        if self.mode == "ranges":
            for a, b in self.ranges:
                c = QColor(acc)
                c.setAlpha(110)
                p.fillRect(QRectF(self.x_of(a), r.top(), self.x_of(b) - self.x_of(a), r.height()), c)
        p.restore()
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 8, 8)
        if self.mode in ("range", "gif"):
            xa, xb = self.x_of(self.a), self.x_of(self.b)
            p.setPen(QPen(acc, 3))
            p.drawLine(QPointF(xa, r.top()), QPointF(xb, r.top()))
            p.drawLine(QPointF(xa, r.bottom()), QPointF(xb, r.bottom()))
            for x in (xa, xb):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(acc)
                p.drawRoundedRect(QRectF(x - 5, r.top() - 3, 10, r.height() + 6), 4, 4)
                p.setBrush(QColor("#ffffff"))
                p.drawRoundedRect(QRectF(x - 1, r.center().y() - 9, 2, 18), 1, 1)
        if self.mode == "markers":
            for m in self.markers:
                x = self.x_of(m)
                p.setPen(QPen(acc, 2))
                p.drawLine(QPointF(x, r.top() - 4), QPointF(x, r.bottom() + 4))
        # playhead
        x = self.x_of(self.play)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(QPointF(x, r.top() - 6), QPointF(x, r.bottom() + 6))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(x, r.top() - 6), 4, 4)
        # time labels
        p.setFont(theme.ui_font(8))
        p.setPen(QColor(theme.MUTED))
        if x - r.left() > 70:
            p.drawText(QRectF(r.left(), r.bottom() + 6, 120, 16), Qt.AlignmentFlag.AlignLeft, "0:00")
        if r.right() - x > 70:
            p.drawText(QRectF(r.right() - 120, r.bottom() + 6, 120, 16), Qt.AlignmentFlag.AlignRight, M.fmt_time(self.dur))
        p.setPen(QColor("#ffffff"))
        lx = min(max(x - 60, r.left() - 14), r.right() - 106)
        align = Qt.AlignmentFlag.AlignCenter if r.left() + 46 < x < r.right() - 46 else (
            Qt.AlignmentFlag.AlignLeft if x <= r.left() + 46 else Qt.AlignmentFlag.AlignRight)
        p.drawText(QRectF(lx if align == Qt.AlignmentFlag.AlignCenter else (r.left() if align == Qt.AlignmentFlag.AlignLeft else r.right() - 120),
                          r.bottom() + 6, 120, 16), align, M.fmt_time(self.play))
        p.end()

    def mousePressEvent(self, e):
        x = e.position().x()
        t = self.t_of(x)
        if self.mode in ("range", "gif"):
            if abs(x - self.x_of(self.a)) < 10:
                self.drag = "a"
            elif abs(x - self.x_of(self.b)) < 10:
                self.drag = "b"
            else:
                self.drag = "play"
        elif self.mode == "markers" and e.button() == Qt.MouseButton.RightButton:
            near = [m for m in self.markers if abs(self.x_of(m) - x) < 8]
            for m in near:
                self.markers.remove(m)
            self.changed.emit()
            self.update()
            return
        elif self.mode == "ranges" and e.button() == Qt.MouseButton.RightButton:
            for rg in list(self.ranges):
                if rg[0] <= t <= rg[1]:
                    self.ranges.remove(rg)
            self.changed.emit()
            self.update()
            return
        elif self.mode == "ranges" and e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.drag = "newrange"
            self.ranges.append([t, t])
        else:
            self.drag = "play"
        self._apply(t)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self.drag is None:
            near = self.mode in ("range", "gif") and (abs(x - self.x_of(self.a)) < 10 or abs(x - self.x_of(self.b)) < 10)
            self.setCursor(Qt.CursorShape.SizeHorCursor if near else Qt.CursorShape.PointingHandCursor)
            return
        self._apply(self.t_of(x))

    def _apply(self, t):
        if self.drag == "a":
            self.a = min(t, self.b - 0.05)
            self.play = self.a
        elif self.drag == "b":
            self.b = max(t, self.a + 0.05)
            self.play = self.b
        elif self.drag == "newrange":
            rg = self.ranges[-1]
            rg[1] = t
        else:
            self.play = t
        self.changed.emit()
        self.seek.emit(self.play)
        self.update()

    def mouseReleaseEvent(self, e):
        if self.drag == "newrange" and self.ranges:
            rg = self.ranges[-1]
            rg.sort()
            if rg[1] - rg[0] < 0.05:
                self.ranges.pop()
        self.drag = None
        self.changed.emit()
        self.update()


class TimelineDialog(ToolDialog):
    """mode: trim | gif | snapshot | bleep | split"""
    TITLES = {"trim": ("Trim", "Save"), "gif": ("To GIF", "Make GIF"), "snapshot": ("Snapshot", "Save frame"),
              "bleep": ("Bleep", "Bleep it"), "split": ("Split", "Split")}

    def __init__(self, src: str, mode: str):
        title, primary = self.TITLES[mode]
        super().__init__(title, Path(src).name, primary, reset=mode in ("trim", "gif", "bleep", "split"))
        self.src = src
        self.mode = mode
        self.info = M.probe(src)
        self.is_video = self.info["has_video"]
        self._freq = _FrameReq()
        if self.is_video:
            self.preview = QLabel()
            self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.preview.setMinimumSize(560, 315)
            self.preview.setStyleSheet("background:#0d0d0f; border-radius:10px;")
            self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            self.body.addWidget(self.preview, 1)
        tl_mode = {"trim": "range", "gif": "gif", "snapshot": "point", "bleep": "ranges", "split": "markers"}[mode]
        self.tl = Timeline(self.info.duration, tl_mode)
        self.tl.changed.connect(self._sync_fields)
        self.tl.seek.connect(self._seek)
        controls = QHBoxLayout()
        self.play_btn = QToolButton()
        self.play_btn.setIcon(icons.icon("audio" if not self.is_video else "film", theme.TEXT, 18))
        self.play_btn.setToolTip("Play / pause (Space)")
        self.play_btn.clicked.connect(self._toggle_play)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.tl, 1)
        self.body.addLayout(controls)
        row = QHBoxLayout()
        self.f_start = QLineEdit()
        self.f_end = QLineEdit()
        for f in (self.f_start, self.f_end):
            f.setFixedWidth(96)
            f.editingFinished.connect(self._typed)
        if mode in ("trim", "gif"):
            for lab, f in (("Start", self.f_start), ("End", self.f_end)):
                l = QLabel(lab)
                l.setObjectName("Muted")
                row.addWidget(l)
                row.addWidget(f)
                row.addSpacing(8)
            self.len_lab = QLabel()
            self.len_lab.setObjectName("Muted")
            row.addWidget(self.len_lab)
        row.addStretch(1)
        if mode == "trim":
            self.fast = QCheckBox("Fast (no re-encode, cuts on keyframes)")
            row.addWidget(self.fast)
        if mode == "gif":
            l = QLabel("FPS")
            l.setObjectName("Muted")
            self.fps = QSpinBox()
            self.fps.setRange(5, 30)
            self.fps.setValue(12)
            l2 = QLabel("Width")
            l2.setObjectName("Muted")
            self.gw = QSpinBox()
            self.gw.setRange(120, 1920)
            self.gw.setValue(min(640, self.info.get("width", 640)))
            self.gw.setSuffix(" px")
            for w in (l, self.fps, l2, self.gw):
                row.addWidget(w)
        if mode == "bleep":
            hint = QLabel("Shift+drag on the timeline to mark a range · right-click to remove")
            hint.setObjectName("Muted")
            row.addWidget(hint)
            self.bmode = Chips(["beep", "silence"], "beep", {"beep": "Beep", "silence": "Silence"})
            row.addWidget(self.bmode)
            add = QPushButton("Mark 1s at playhead")
            add.clicked.connect(self._add_range)
            row.addWidget(add)
        if mode == "split":
            hint = QLabel("Click to move the playhead, then add a cut · right-click a cut to remove")
            hint.setObjectName("Muted")
            row.addWidget(hint)
            add = QPushButton("Add cut here")
            add.clicked.connect(self._add_marker)
            row.addWidget(add)
            even = QPushButton("Even parts…")
            even.clicked.connect(self._even)
            row.addWidget(even)
        self.body.addLayout(row)
        self._player = None
        self._player_tried = False
        self._sync_fields()
        self.resize(820, 620 if self.is_video else 260)
        src, is_video, dur = self.src, self.is_video, self.info.duration
        run_bg(lambda push: TimelineDialog._load_strip(src, is_video, dur, push), self._got_strip)
        self._seek(0)

    # ------------------------------------------------------------ background loading
    @staticmethod
    def _load_strip(src, is_video, dur, push):
        if is_video:
            n = 12
            tmp = Path(tempfile.mkdtemp(prefix="rind_thumbs_"))
            dur = max(dur, 0.1)
            subprocess.run([M.ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", src, "-vf",
                            f"fps={n / dur:.5f},scale=-2:72", "-frames:v", str(n), str(tmp / "t%03d.jpg")],
                           capture_output=True, creationflags=NO_WINDOW)
            imgs = [QImage(str(f)) for f in sorted(tmp.glob("t*.jpg"))]
            shutil.rmtree(tmp, ignore_errors=True)
            push(("thumbs", imgs))
        else:
            push(("wave", M.waveform(src, 600)))

    def _got_strip(self, v):
        kind, data = v
        if kind == "thumbs":
            self._got_thumbs(data)
        else:
            self._got_wave(data)

    def _got_thumbs(self, imgs):
        self.tl.thumbs = [QPixmap.fromImage(i) for i in imgs if not i.isNull()]
        self.tl.update()

    def _got_wave(self, w):
        peak = max(w) if w else 1
        self.tl.wave = [v / (peak or 1) for v in w]
        self.tl.update()

    def _seek(self, t):
        if self._player and self._playing():
            self._player.setPosition(int(t * 1000))
        if not self.is_video:
            return
        req = self._freq
        req.t = t
        if not req.busy:
            req.busy = True
            src = self.src

            def work(push):
                try:
                    while req.t is not None:
                        tt = req.t
                        req.t = None
                        push((tt, QImage.fromData(M.frame_png(src, tt, 960))))
                finally:
                    req.busy = False
            run_bg(work, self._got_frame_v)

    def _got_frame_v(self, v):
        self._got_frame(*v)

    def _got_frame(self, t, img):
        if img.isNull():
            return
        self._show_img(img)

    def _show_img(self, img: QImage):
        pm = QPixmap.fromImage(img).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pm)

    # ------------------------------------------------------------ playback (optional)
    def _init_player(self):
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._player.setAudioOutput(self._audio)
            if self.is_video:
                self._sink = QVideoSink(self)
                self._player.setVideoSink(self._sink)
                self._sink.videoFrameChanged.connect(lambda f: self._show_img(f.toImage()) if self._playing() else None)
            self._player.setSource(QUrl.fromLocalFile(self.src))
            self._player.positionChanged.connect(self._pos)
        except Exception:
            self._player = None
            self.play_btn.setEnabled(False)
            self.play_btn.setToolTip("Playback unavailable")

    def _playing(self):
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        except Exception:
            return False

    def _toggle_play(self):
        if not self._player_tried:
            self._player_tried = True
            self._init_player()
        if not self._player:
            return
        if self._playing():
            self._player.pause()
        else:
            start = self.tl.play
            if self.mode in ("trim", "gif") and not (self.tl.a <= start < self.tl.b - 0.05):
                start = self.tl.a
            self._player.setPosition(int(start * 1000))
            self._player.play()

    def _pos(self, ms):
        t = ms / 1000
        if not self._playing():
            return
        if self.mode in ("trim", "gif") and t >= self.tl.b:
            self._player.pause()
            return
        self.tl.play = t
        self.tl.update()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space:
            self._toggle_play()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        if self._player:
            self._player.stop()
        super().closeEvent(e)

    def done(self, r):
        if self._player:
            self._player.stop()
        super().done(r)

    # ------------------------------------------------------------ fields
    def _sync_fields(self):
        self.f_start.setText(M.fmt_time(self.tl.a))
        self.f_end.setText(M.fmt_time(self.tl.b))
        if hasattr(self, "len_lab"):
            self.len_lab.setText(f"length {M.fmt_time(self.tl.b - self.tl.a)}")
        if self.mode == "bleep":
            self.status.setText(f"{len(self.tl.ranges)} range(s)")
        if self.mode == "split":
            self.status.setText(f"{len(self.tl.markers) + 1} part(s)")

    def _typed(self):
        try:
            a = M.parse_time(self.f_start.text())
            b = M.parse_time(self.f_end.text())
            if 0 <= a < b <= self.tl.dur + 0.01:
                self.tl.a, self.tl.b = a, min(b, self.tl.dur)
                self.tl.update()
        except ValueError:
            pass
        self._sync_fields()

    def _add_range(self):
        t = self.tl.play
        self.tl.ranges.append([t, min(self.tl.dur, t + 1.0)])
        self.tl.update()
        self._sync_fields()

    def _add_marker(self):
        t = self.tl.play
        if 0.1 < t < self.tl.dur - 0.1 and all(abs(t - m) > 0.1 for m in self.tl.markers):
            self.tl.markers.append(t)
            self.tl.markers.sort()
        self.tl.update()
        self._sync_fields()

    def _even(self):
        from PySide6.QtWidgets import QInputDialog
        n, ok = QInputDialog.getInt(self, "Even parts", "How many parts?", 2, 2, 200)
        if ok:
            self.tl.markers = [self.tl.dur * i / n for i in range(1, n)]
            self.tl.update()
            self._sync_fields()

    def on_reset(self):
        self.tl.a, self.tl.b = 0, self.tl.dur
        self.tl.ranges.clear()
        self.tl.markers.clear()
        self.tl.update()
        self._sync_fields()

    def on_accept(self):
        if self.mode == "bleep" and not self.tl.ranges:
            self.status.setText("Mark at least one range first")
            return
        if self.mode == "split" and not self.tl.markers:
            self.status.setText("Add at least one cut")
            return
        self.accept()

    def values(self) -> dict:
        v = {"start": self.tl.a, "end": self.tl.b, "t": self.tl.play,
             "ranges": [tuple(r) for r in self.tl.ranges], "markers": list(self.tl.markers)}
        if self.mode == "trim":
            v["fast"] = self.fast.isChecked()
        if self.mode == "gif":
            v["fps"], v["width"] = self.fps.value(), self.gw.value()
        if self.mode == "bleep":
            v["bmode"] = self.bmode.value()
        return v
