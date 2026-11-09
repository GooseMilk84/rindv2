"""Windows glue: spotting a file drag + key press, global hotkey, Explorer selection,
shell integration (context menu, Send To, startup)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, QPoint, QTimer, Signal
from PySide6.QtGui import QCursor

from .config import settings

IS_WIN = sys.platform == "win32"

if IS_WIN:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

VK = {"shift": 0x10, "ctrl": 0x11, "alt": 0x12, "win": 0x5B, "lbutton": 0x01, "esc": 0x1B, "rbutton": 0x02}
EXPLORER_CLASSES = {"CabinetWClass", "ExploreWClass", "Progman", "WorkerW", "#32770"}


def key_down(name: str) -> bool:
    if not IS_WIN:
        return False
    vk = VK.get(name)
    if vk is None:
        return False
    down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
    if name == "win":
        down = down or bool(user32.GetAsyncKeyState(0x5C) & 0x8000)
    if name == "lbutton" and user32.GetSystemMetrics(23):  # SM_SWAPBUTTON (left-handed mouse)
        down = bool(user32.GetAsyncKeyState(0x02) & 0x8000)
    return down


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def root_class_at(x: int, y: int) -> str:
    if not IS_WIN:
        return ""
    pt = wintypes.POINT(x, y)
    h = user32.WindowFromPoint(pt)
    if not h:
        return ""
    root = user32.GetAncestor(h, 2)  # GA_ROOT
    return class_name(root or h)


def drag_image_visible() -> bool:
    """Explorer (and most shell drag sources) show a 'SysDragImage' window while dragging."""
    if not IS_WIN:
        return False
    for cls in ("SysDragImage", "DragImageWindow"):
        h = user32.FindWindowW(cls, None)
        if h and user32.IsWindowVisible(h):
            return True
    return False


def cursor_phys() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


INTERNAL_DRAG = [False]     # set while Rind itself is dragging files (e.g. out of the Float shelf)


class DragWatcher(QObject):
    """Polls mouse/keyboard state (~60 Hz) to notice: a file drag is in progress AND
    the user pressed one of the wheel keys. Cheap: a handful of GetAsyncKeyState calls."""
    trigger = Signal(str, QPoint)      # mode, logical cursor pos
    switch = Signal(str)
    released = Signal()
    escape = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QTimer(self, interval=15, timeout=self._poll)
        self.lb_prev = False
        self.press_xy = (0, 0)
        self.press_ok = False
        self.mods_prev = {"shift": False, "ctrl": False, "alt": False, "win": False}
        self.active = False
        self.enabled = True

    def start(self):
        if IS_WIN:
            self.timer.start()

    def mode_keys(self) -> dict:
        return {settings.get("key_convert", "shift"): "convert",
                settings.get("key_tools", "ctrl"): "tool",
                settings.get("key_actions", "alt"): "action"}

    def _poll(self):
        lb = key_down("lbutton")
        mods = {k: key_down(k) for k in self.mods_prev}
        if lb and not self.lb_prev:
            x, y = cursor_phys()
            self.press_xy = (x, y)
            self.press_ok = root_class_at(x, y) in EXPLORER_CLASSES
        if lb and self.enabled:
            x, y = cursor_phys()
            moved = abs(x - self.press_xy[0]) + abs(y - self.press_xy[1]) > 8
            det = settings.get("drag_detection", "auto")
            dragging = drag_image_visible() or (det != "strict" and self.press_ok and moved)
            if det == "loose":
                dragging = moved
            dragging = dragging or INTERNAL_DRAG[0]
            mk = self.mode_keys()
            for k, down in mods.items():
                if down and not self.mods_prev[k] and k in mk:
                    if self.active:
                        self.switch.emit(mk[k])
                    elif dragging:
                        self.active = True
                        self.trigger.emit(mk[k], QCursor.pos())
            if self.active and key_down("esc"):
                self.active = False
                self.escape.emit()
        if not lb and self.lb_prev and self.active:
            self.active = False
            self.released.emit()
        self.lb_prev = lb
        self.mods_prev = mods


# ------------------------------------------------------------------ global hotkey
MOD = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
VKEYS = {"space": 0x20, "enter": 0x0D, "tab": 0x09, **{chr(c).lower(): c for c in range(0x41, 0x5B)},
         **{str(d): 0x30 + d for d in range(10)}, **{f"f{i}": 0x6F + i for i in range(1, 13)}}


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.cb = callback

    def nativeEventFilter(self, etype, message):
        if IS_WIN and bytes(etype) == b"windows_generic_MSG":
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0312 and msg.wParam == 0xB0B:
                    self.cb()
                    return True, 0
            except Exception:
                pass
        return False, 0


def register_hotkey(spec: str) -> bool:
    if not IS_WIN or not spec:
        return False
    parts = [p.strip().lower() for p in spec.split("+")]
    mods = 0x4000  # MOD_NOREPEAT
    vk = None
    for p in parts:
        if p in MOD:
            mods |= MOD[p]
        else:
            vk = VKEYS.get(p)
    if vk is None:
        return False
    user32.UnregisterHotKey(None, 0xB0B)
    return bool(user32.RegisterHotKey(None, 0xB0B, mods, vk))


def explorer_selection() -> list[str]:
    """Files selected in the foreground Explorer window (or on the desktop)."""
    if not IS_WIN:
        return []
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        fg = user32.GetForegroundWindow()
        cls = class_name(fg)
        shell = win32com.client.Dispatch("Shell.Application")
        if cls in ("Progman", "WorkerW"):
            try:
                sw = shell.Windows()
                desk = sw.FindWindowSW(0, None, 8, 0, 1)  # SWC_DESKTOP, SWFO_NEEDDISPATCH
                if isinstance(desk, tuple):
                    desk = desk[0]
                items = desk.Document.SelectedItems()
                return [items.Item(i).Path for i in range(items.Count)]
            except Exception:
                return []
        for w in shell.Windows():
            try:
                if int(w.HWND) == int(fg or 0):
                    items = w.Document.SelectedItems()
                    return [items.Item(i).Path for i in range(items.Count)]
            except Exception:
                continue
    except Exception:
        pass
    return []


# ------------------------------------------------------------------ shell integration
def launch_command(extra: str = "") -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"{extra}'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if pyw.exists():
        exe = pyw
    script = Path(__file__).resolve().parent.parent / "run_rind.pyw"
    return f'"{exe}" "{script}"{extra}'


def icon_path() -> str:
    p = Path(__file__).resolve().parent / "assets" / "rind.ico"
    return str(p) if p.exists() else ""


def set_startup(on: bool):
    if not IS_WIN:
        return
    import winreg
    k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
    try:
        if on:
            winreg.SetValueEx(k, "Rind", 0, winreg.REG_SZ, launch_command(" --background"))
        else:
            try:
                winreg.DeleteValue(k, "Rind")
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(k)


def set_context_menu(on: bool):
    if not IS_WIN:
        return
    import winreg
    cmd = launch_command(' --files "%1"')
    for cls in (r"Software\Classes\*\shell\Rind", r"Software\Classes\Directory\shell\Rind"):
        if on:
            k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, cls)
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "Rind…")
            winreg.SetValueEx(k, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
            ic = icon_path()
            if ic:
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, ic)
            c = winreg.CreateKey(k, "command")
            winreg.SetValueEx(c, "", 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(c)
            winreg.CloseKey(k)
        else:
            for sub in (cls + r"\command", cls):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
                except FileNotFoundError:
                    pass


def context_menu_installed() -> bool:
    if not IS_WIN:
        return False
    import winreg
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\*\shell\Rind"))
        return True
    except OSError:
        return False


def sendto_path() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "SendTo" / "Rind.lnk"


def set_sendto(on: bool):
    if not IS_WIN:
        return
    p = sendto_path()
    if not on:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        return
    try:
        import win32com.client
        sh = win32com.client.Dispatch("WScript.Shell")
        lnk = sh.CreateShortCut(str(p))
        cmd = launch_command(" --files")
        exe, _, args = cmd.partition('" ')
        lnk.TargetPath = exe.strip('"')
        lnk.Arguments = args
        ic = icon_path()
        if ic:
            lnk.IconLocation = ic
        lnk.save()
    except Exception:
        pass


def set_dark_window(widget):
    from .theme import dark_titlebar
    dark_titlebar(widget)
