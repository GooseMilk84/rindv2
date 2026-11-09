"""Install/uninstall helpers: Start Menu shortcut, right-click entry, Send To, startup.
python -m rind.setup_shortcuts            -> install
python -m rind.setup_shortcuts --remove   -> remove everything"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import winhooks
from .config import settings


def start_menu_lnk() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Rind.lnk"


def make_start_menu():
    try:
        import win32com.client
        sh = win32com.client.Dispatch("WScript.Shell")
        lnk = sh.CreateShortCut(str(start_menu_lnk()))
        cmd = winhooks.launch_command("")
        exe, _, args = cmd.partition('" ')
        lnk.TargetPath = exe.strip('"')
        lnk.Arguments = args.strip()
        lnk.WorkingDirectory = str(Path(__file__).resolve().parent.parent)
        lnk.Description = "Rind - drag a file, tap Shift, drop on a slice"
        ic = winhooks.icon_path()
        if ic:
            lnk.IconLocation = ic
        lnk.save()
    except Exception as ex:
        print("  (couldn't make the Start Menu shortcut:", ex, ")")


def install():
    if sys.platform != "win32":
        print("shell integration is Windows-only")
        return
    make_start_menu()
    winhooks.set_context_menu(True)
    winhooks.set_sendto(True)
    winhooks.set_startup(True)
    settings["start_with_windows"] = True
    settings.save()
    print("  Start Menu shortcut, right-click entry, Send To and auto-start added.")


def remove():
    if sys.platform != "win32":
        return
    winhooks.set_context_menu(False)
    winhooks.set_sendto(False)
    winhooks.set_startup(False)
    try:
        start_menu_lnk().unlink()
    except FileNotFoundError:
        pass
    print("  Shell integration removed.")


if __name__ == "__main__":
    remove() if "--remove" in sys.argv else install()
