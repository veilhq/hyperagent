#!/usr/bin/env python3
"""
Hyperagent — Standalone AI chat app powered by Kiro CLI's ACP protocol.

Runs as an independent PyWebView process. Can be launched from Hypervisor
or directly (Start menu, taskbar shortcut, etc.).

Usage:
    pythonw hyperagent.py
"""

import json
import os
import shutil
import threading
import time
from pathlib import Path

import webview

from helpers import (
    HYPERVISOR_DIR,
    ICON_FILE,
    PREFS_FILE,
    PORTAL_ROOT,
    logger,
)
from acp_pool import ACPClientPool
from bridge_api import HyperagentAPI

# Auto-build: regenerate generated_html.py from current source before import
import subprocess, sys
subprocess.run(
    [sys.executable, str(Path(__file__).parent / "build.py")],
    cwd=str(Path(__file__).parent),
)
from generated_html import HTML


# ---------------------------------------------------------------------------
# Window chrome (dark title bar + custom icon via Windows DWM API)
# ---------------------------------------------------------------------------

def _apply_window_chrome(title: str, icon_path: str):
    """Force dark title bar and custom icon via Windows DWM API."""
    import ctypes
    hwnd = ctypes.windll.user32.FindWindowW(None, title)
    if not hwnd:
        return
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    val = ctypes.c_int(1)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(val), ctypes.sizeof(val)
    )
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    WM_SETICON = 0x0080
    ICON_BIG = 1
    ICON_SMALL = 0
    hicon = ctypes.windll.user32.LoadImageW(
        0, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE
    )
    if hicon:
        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)


# ---------------------------------------------------------------------------
# Theme file watcher
# ---------------------------------------------------------------------------

def _start_theme_watcher(window, api):
    """Poll preferences.json for changes and push palette updates to frontend."""
    prefs_file = HYPERVISOR_DIR / "preferences.json"
    last_mtime = prefs_file.stat().st_mtime if prefs_file.exists() else 0

    def _watch():
        nonlocal last_mtime
        while True:
            time.sleep(2)
            try:
                if not prefs_file.exists():
                    continue
                mtime = prefs_file.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    palette = api.get_accent()
                    payload = json.dumps(palette).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                    window.evaluate_js(f"if(window.applyAccent)window.applyAccent(JSON.parse(`{payload}`))")
            except Exception:
                pass

    threading.Thread(target=_watch, daemon=True).start()


# ---------------------------------------------------------------------------
# Stale lock cleanup
# ---------------------------------------------------------------------------

def _cleanup_stale_locks(sessions_dir):
    """Remove lock files whose owning kiro-cli process is orphaned (parent bridge dead)."""
    if not sessions_dir or not sessions_dir.exists():
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_TERMINATE = 0x0001
    cleaned = 0

    my_pid = os.getpid()

    for lock_file in sessions_dir.glob("*.lock"):
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            if pid == 0:
                lock_file.unlink(missing_ok=True)
                cleaned += 1
                continue
            if pid == my_pid:
                continue
            # Check if the process's parent (bridge) is still alive
            parent_pid = _get_parent_pid(pid)
            if parent_pid is not None:
                # Parent exists — check if it's still running
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, parent_pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    continue  # Parent alive — lock is valid
            # Parent dead or unknown — orphan. Kill the kiro-cli process and remove lock.
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 1)
                kernel32.CloseHandle(handle)
                logger.info("startup: killed orphaned kiro-cli pid=%d", pid)
            lock_file.unlink(missing_ok=True)
            cleaned += 1
        except Exception:
            pass
    if cleaned:
        logger.info("startup: cleaned %d stale lock file(s)", cleaned)


def _get_parent_pid(pid):
    """Get the parent PID of a process on Windows using toolhelp32 snapshot."""
    try:
        import ctypes
        import ctypes.wintypes

        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.wintypes.DWORD),
                ("cntUsage", ctypes.wintypes.DWORD),
                ("th32ProcessID", ctypes.wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", ctypes.wintypes.DWORD),
                ("cntThreads", ctypes.wintypes.DWORD),
                ("th32ParentProcessID", ctypes.wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot == -1:
            return None
        try:
            entry = PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
                return None
            while True:
                if entry.th32ProcessID == pid:
                    return entry.th32ParentProcessID
                if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                    break
        finally:
            kernel32.CloseHandle(snapshot)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("main() starting")

    # Clean up empty sessions (0 messages) left over from session switching
    sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
    if sessions_dir.exists():
        for jsonl in sessions_dir.glob("*.jsonl"):
            if jsonl.stat().st_size == 0:
                sid = jsonl.stem
                for f in sessions_dir.glob(f"{sid}*"):
                    f.unlink(missing_ok=True)

    # Clear stale tab/session associations from preferences
    if PREFS_FILE.exists():
        try:
            prefs = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
            changed = False
            for key in ("tabs", "active_tab"):
                if key in prefs:
                    del prefs[key]
                    changed = True
            if changed:
                PREFS_FILE.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
                logger.info("startup: cleared stale tab associations from preferences")
        except Exception:
            pass

    # Clean up orphaned lock files from force-closed sessions
    _cleanup_stale_locks(sessions_dir)

    pool = ACPClientPool(max_tabs=5)
    api = HyperagentAPI(pool)

    # Always start fresh with a single tab
    initial_tab = pool.create_tab()
    pool.start_tab(initial_tab)

    icon_path = str(ICON_FILE) if ICON_FILE.exists() else None

    window = webview.create_window(
        "Hyperagent",
        html=HTML,
        js_api=api,
        width=700,
        height=850,
        min_size=(500, 400),
        background_color='#000000',
    )

    def on_start():
        time.sleep(1)
        _apply_window_chrome("Hyperagent", str(ICON_FILE))
        pool.set_window(window)
        logger.info("on_start: window ready, connecting protocol")
        pool.connect_tab(initial_tab)
        _start_theme_watcher(window, api)

    webview.start(on_start, icon=icon_path, debug=False)
    pool.save_tab_state()
    pool.stop_all()


if __name__ == "__main__":
    main()
