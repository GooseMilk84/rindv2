"""App wiring: tray, single instance, drag watcher -> wheel -> handler."""
from __future__ import annotations

import json
import logging
import os
import sys
import traceback

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon

from . import APP_NAME, theme, winhooks
from .config import settings
from .jobs import JobManager, log
from .tools import Handler
from .wheel import Wheel

SERVER = f"rind-{os.environ.get('USERNAME') or os.environ.get('USER') or 'me'}"


def app_icon(size=64, accent=None) -> QIcon:
    import math
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = s / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#17171a"))
        p.drawEllipse(QPointF(c, c), s * 0.48, s * 0.48)
        acc = QColor(accent or theme.accent())
        n = 6
        r0, r1 = s * 0.18, s * 0.42
        gap = 0.10
        for i in range(n):
            a0 = -math.pi / 2 + i * 2 * math.pi / n + gap
            a1 = a0 + 2 * math.pi / n - 2 * gap
            path = QPainterPath()
            path.moveTo(c + math.cos(a0) * r1, c + math.sin(a0) * r1)
            path.arcTo(QRectF(c - r1, c - r1, 2 * r1, 2 * r1), -math.degrees(a0), -math.degrees(a1 - a0))
            path.lineTo(c + math.cos(a1) * r0, c + math.sin(a1) * r0)
            path.arcTo(QRectF(c - r0, c - r0, 2 * r0, 2 * r0), -math.degrees(a1), math.degrees(a1 - a0))
            path.closeSubpath()
            col = QColor(acc) if i == 1 else QColor("#4a4a52")
            p.setBrush(col)
            p.drawPath(path)
        p.setBrush(acc)
        p.drawEllipse(QPointF(c, c), s * 0.09, s * 0.09)
        p.end()
        icon.addPixmap(pm)
    return icon


class RindApp:
    def __init__(self, app: QApplication, files: list[str] | None, background: bool):
        self.app = app
        self.jobs = JobManager()
        self._shelf = None
        self._settings = None
        self.handler = Handler(self.jobs, self.shelf)
        self.drag_wheel = Wheel(click_mode=False)
        self.click_wheel = Wheel(click_mode=True)
        for w in (self.drag_wheel, self.click_wheel):
            w.chosen.connect(self._chosen)
        # pre-create native windows so the first popup is instant and the drop target is registered
        self.drag_wheel.winId()
        self.click_wheel.winId()
        self.watcher = winhooks.DragWatcher()
        self.watcher.trigger.connect(self._trigger)
        self.watcher.switch.connect(self.drag_wheel.set_mode)
        self.watcher.released.connect(self._released)
        self.watcher.escape.connect(self.drag_wheel.dismiss)
        self.watcher.start()
        self.pending: list[str] = []
        self.pending_timer = QTimer(singleShot=True, interval=350, timeout=self._open_pending)
        self.server = QLocalServer()
        QLocalServer.removeServer(SERVER)
        self.server.listen(SERVER)
        self.server.newConnection.connect(self._incoming)
        self._tray()
        self._hotkey_filter = winhooks.HotkeyFilter(lambda: QTimer.singleShot(0, self.hotkey))
        app.installNativeEventFilter(self._hotkey_filter)
        winhooks.register_hotkey(settings.get("hotkey", ""))
        if files:
            self.queue(files)
        elif not background:
            if winhooks.IS_WIN:
                self.jobs.notify(f"{APP_NAME} is running", "Drag a file, tap Shift (Ctrl = tools, Alt = actions), drop on a slice.")
            else:
                QTimer.singleShot(200, self.open_files)

    # ------------------------------------------------------------ tray
    def _tray(self):
        self.tray = QSystemTrayIcon(app_icon())
        self.tray.setToolTip(f"{APP_NAME} - drag a file + Shift")
        m = QMenu()
        a = QAction("Open files…", m)
        a.triggered.connect(self.open_files)
        m.addAction(a)
        a = QAction("Float shelf", m)
        a.triggered.connect(lambda: self.shelf().add([]))
        m.addAction(a)
        m.addSeparator()
        self.pause_act = QAction("Pause drag detection", m, checkable=True)
        self.pause_act.toggled.connect(lambda v: setattr(self.watcher, "enabled", not v))
        m.addAction(self.pause_act)
        a = QAction("Settings…", m)
        a.triggered.connect(self.open_settings)
        m.addAction(a)
        a = QAction("Open log", m)
        a.triggered.connect(self._open_log)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Quit", m)
        a.triggered.connect(self.quit)
        m.addAction(a)
        self.tray_menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.open_settings() if r == QSystemTrayIcon.ActivationReason.DoubleClick
                                    else (self.open_files() if r == QSystemTrayIcon.ActivationReason.MiddleClick else None))
        self.tray.show()

    def _open_log(self):
        from .config import data_dir
        p = data_dir() / "log.txt"
        if not p.exists():
            p.write_text("")
        from .jobs import open_file
        open_file(str(p))

    def shelf(self):
        if self._shelf is None:
            from .dialogs.shelf import Shelf
            self._shelf = Shelf()
        return self._shelf

    def open_settings(self):
        if self._settings is None:
            from .dialogs.settings_win import SettingsWindow
            self._settings = SettingsWindow()
            self._settings.changed.connect(self._settings_changed)
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def _settings_changed(self):
        self.tray.setIcon(app_icon())

    def open_files(self):
        files, _ = QFileDialog.getOpenFileNames(None, f"{APP_NAME} - pick files")
        if files:
            self.click_wheel.popup(QCursor.pos(), [os.path.normpath(f) for f in files], "convert")

    def quit(self):
        self.tray.hide()
        self.app.quit()

    # ------------------------------------------------------------ wheel flow
    def _trigger(self, mode, pos):
        if self.click_wheel.isVisible():
            self.click_wheel.dismiss()
        self.drag_wheel.popup(pos, [], mode)

    def _released(self):
        # mouse went up: if the drop didn't land on us, close quietly
        QTimer.singleShot(350, lambda: self.drag_wheel.dismiss() if self.drag_wheel.isVisible() else None)

    def _chosen(self, item, paths):
        # leave the OLE drop callback before opening any dialogs (otherwise Explorer waits on us)
        QTimer.singleShot(0, lambda: self._safe_handle(item, paths))

    def _safe_handle(self, item, paths):
        try:
            self.handler.handle(item, paths)
        except Exception as ex:
            log(traceback.format_exc())
            from .jobs import Info
            self.handler.show_info(Info("Something broke", f"{type(ex).__name__}: {ex}"))

    def hotkey(self):
        paths = winhooks.explorer_selection()
        if paths:
            self.click_wheel.popup(QCursor.pos(), paths, "convert")
        else:
            self.jobs.notify("Nothing selected", "Select files in Explorer, then press the hotkey.")

    # ------------------------------------------------------------ single instance
    def queue(self, files: list[str]):
        self.pending += [os.path.normpath(f) for f in files if f and os.path.exists(f)]
        self.pending_timer.start()

    def _open_pending(self):
        paths, self.pending = list(dict.fromkeys(self.pending)), []
        if paths:
            self.click_wheel.popup(QCursor.pos(), paths, "convert")

    def _incoming(self):
        sock = self.server.nextPendingConnection()

        def read():
            try:
                data = json.loads(bytes(sock.readAll()).decode("utf-8"))
                if data.get("files"):
                    self.queue(data["files"])
                elif data.get("quit"):
                    self.quit()
                elif data.get("show"):
                    self.open_settings()
            except Exception:
                pass
        sock.readyRead.connect(read)


def send_to_running(files: list[str] | None, quit_: bool = False) -> bool:
    s = QLocalSocket()
    s.connectToServer(SERVER)
    if not s.waitForConnected(300):
        return False
    payload = {"quit": True} if quit_ else ({"files": files} if files else {"show": True})
    s.write(json.dumps(payload).encode("utf-8"))
    s.flush()
    s.waitForBytesWritten(1000)
    s.disconnectFromServer()
    return True


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.WARNING)
    background = "--background" in argv
    files = []
    if "--files" in argv:
        files = [a for a in argv[argv.index("--files") + 1:] if not a.startswith("--")]
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Rind.FileWheel")
        except Exception:
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    if "--quit" in argv:
        send_to_running(None, quit_=True)
        return 0
    if send_to_running(files):
        return 0
    theme.apply(app)
    app.setWindowIcon(app_icon())

    def excepthook(t, v, tb):
        log("".join(traceback.format_exception(t, v, tb)))
    sys.excepthook = excepthook
    r = RindApp(app, files, background)
    rc = app.exec()
    settings.save()
    try:
        from .engines import office
        office.shutdown()      # close the hidden Word/PowerPoint/Excel we kept warm
    except Exception:
        pass
    os._exit(rc)  # skip Qt/Python teardown order issues on quit
