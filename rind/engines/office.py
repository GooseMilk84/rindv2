"""One long-lived Office helper thread.

Starting Word/PowerPoint/Excel through COM is the slow part (often 30-70 s with add-ins),
so we start each app once, keep it hidden and warm, and reuse it for every job.
COM objects belong to the thread that made them, so all Office calls run on this one thread.
Apps quit after a few idle minutes, and on Rind exit."""
from __future__ import annotations

import queue
import sys
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout

from ..config import settings

IDLE_QUIT = 300        # seconds an app can sit unused before we close it
CALL_TIMEOUT = 150     # give up waiting (and fall back to the built-in converter) after this


class _OfficeThread:
    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.apps: dict[str, object] = {}
        self.last_used: dict[str, float] = {}
        self.ready: set[str] = set()      # apps already running (for progress messages)
        self.t = threading.Thread(target=self._run, name="rind-office", daemon=True)
        self.t.start()

    # ------------------------------------------------------------ worker side
    def _run(self):
        import pythoncom
        pythoncom.CoInitialize()
        while True:
            try:
                item = self.q.get(timeout=20)
            except queue.Empty:
                self._reap()
                continue
            if item is None:
                break
            progid, fn, fut = item
            if fut.set_running_or_notify_cancel():
                try:
                    app = self._app(progid)
                    try:
                        _ = app.Name            # cached instance still alive?
                    except Exception:
                        self._drop(progid)
                        app = self._app(progid)
                    res = fn(app)
                    self.last_used[progid] = time.time()
                    fut.set_result(res)
                except Exception as ex:
                    fut.set_exception(ex)
            self._reap()
        for p in list(self.apps):
            self._drop(p)
        pythoncom.CoUninitialize()

    def _app(self, progid):
        app = self.apps.get(progid)
        if app is not None:
            return app
        import win32com.client
        app = win32com.client.DispatchEx(progid)    # our own hidden instance, never the user's open window
        try:
            app.Visible = False
        except Exception:
            pass
        alerts = 1 if progid.startswith("PowerPoint") else 0      # ppAlertsNone=1, wdAlertsNone=0, Excel False
        for attr, val in (("DisplayAlerts", alerts), ("ScreenUpdating", False), ("AutomationSecurity", 3)):
            try:
                setattr(app, attr, val)          # 3 = msoAutomationSecurityForceDisable: no macro prompts
            except Exception:
                pass
        self.apps[progid] = app
        self.ready.add(progid)
        self.last_used[progid] = time.time()
        return app

    def _drop(self, progid):
        app = self.apps.pop(progid, None)
        self.ready.discard(progid)
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass

    def _reap(self):
        now = time.time()
        for p in list(self.apps):
            if now - self.last_used.get(p, now) > IDLE_QUIT:
                self._drop(p)

    # ------------------------------------------------------------ caller side
    def submit(self, progid, fn) -> Future:
        fut: Future = Future()
        self.q.put((progid, fn, fut))
        return fut


def _noop(app):
    return None


_T: _OfficeThread | None = None
_LOCK = threading.Lock()


def available() -> bool:
    if sys.platform != "win32" or not settings.get("use_office", True):
        return False
    try:
        import pythoncom  # noqa
        import win32com.client  # noqa
        return True
    except ImportError:
        return False


def _thread() -> _OfficeThread:
    global _T
    with _LOCK:
        if _T is None:
            _T = _OfficeThread()
        return _T


def is_warm(progid: str) -> bool:
    return _T is not None and progid in _T.ready


def run(progid: str, fn, timeout: float = CALL_TIMEOUT):
    """Run fn(app) on the Office thread. Returns its result, or None if Office isn't usable."""
    if not available():
        return None
    fut = _thread().submit(progid, fn)
    try:
        return fut.result(timeout=timeout)
    except FutureTimeout:
        return None
    except Exception:
        return None


def warm(progid: str):
    """Start an app in the background so the first conversion doesn't pay the boot cost."""
    if available() and not is_warm(progid):
        _thread().submit(progid, _noop)


def shutdown():
    if _T is not None:
        _T.q.put(None)
        _T.t.join(timeout=8)
