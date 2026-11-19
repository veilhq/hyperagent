"""Status sprite: a small always-on-top tile that shows Hyperagent's state as an inkblot.

The window is frameless and on top; the page drags it through get_bounds and
set_bounds. Its shown/hidden choice and position live under "statusSprite" in
preferences.json.
"""

import ctypes
import json
import os
import threading
import time
from contextlib import contextmanager
from ctypes import wintypes

import webview

from helpers import logger, read_prefs, update_prefs

SPRITE_TITLE = "Hyperagent Status"
SPRITE_SIZE = 176
PREFS_KEY = "statusSprite"
RATE_WINDOW_S = 60
LIVE_S = 20
ALARM_S = 30
ERROR_WINDOW_S = 600
SUCCESS_S = 6

SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
MONITOR_DEFAULTTONULL = 0
DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED = 2, 1
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND = 33, 1
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

_user32 = ctypes.windll.user32
_user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
_user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


@contextmanager
def per_monitor_dpi():
    """Runs the block with this thread per-monitor DPI aware, so window and cursor coordinates are physical pixels."""
    previous = _user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    try:
        yield
    finally:
        if previous:
            _user32.SetThreadDpiAwarenessContext(previous)


def _own_window(title):
    """Handle of this process's top-level window with the given title, or None."""
    found = []
    pid = os.getpid()

    def visit(hwnd, _):
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            buffer = ctypes.create_unicode_buffer(len(title) + 2)
            _user32.GetWindowTextW(hwnd, buffer, len(buffer))
            if buffer.value == title:
                found.append(hwnd)
                return False
        return True

    _user32.EnumWindows(_ENUM_WINDOWS_PROC(visit), 0)
    return found[0] if found else None


def reduce_status(states, error_times, turn_times, now, waiting=(), success_times=()):
    """Fold every tab into one status: busy if any tab works, an error if any tab errored, asleep if none is ready.

    waiting is one flag per tab (an open question, plan approval, or login); success_times are clean turn finishes.
    """
    errors = [at for at in error_times if at and now - at <= ERROR_WINDOW_S]
    error_age = now - max(errors) if errors else None
    if error_age is None and "crashed" in states:
        error_age = float(ALARM_S)
    recent = [at for at in turn_times if now - at <= RATE_WINDOW_S]
    live = bool(recent) and now - max(recent) <= LIVE_S
    finishes = [at for at in success_times if at and now - at <= SUCCESS_S]
    return {
        "busy": "prompting" in states,
        "closed": not any(state in ("ready", "prompting") for state in states),
        "error_age": round(error_age, 1) if error_age is not None else None,
        "rate": len(recent) if live else 0,
        "waiting": any(flag and state not in ("stopped", "crashed") for flag, state in zip(waiting, states)),
        "success_age": round(now - max(finishes), 1) if finishes else None,
    }


def _sprite_prefs():
    value = read_prefs().get(PREFS_KEY)
    return value if isinstance(value, dict) else {}


def _save_sprite_prefs(**changes):
    def mutate(prefs):
        current = prefs.get(PREFS_KEY)
        current = dict(current) if isinstance(current, dict) else {}
        current.update(changes)
        prefs[PREFS_KEY] = current
    try:
        update_prefs(mutate)
    except (OSError, ValueError) as e:
        logger.warning("status sprite: could not save preferences: %s", e)


def sprite_enabled():
    """True when the operator has turned the status sprite on."""
    return bool(_sprite_prefs().get("on"))


class SpriteApi:
    """Bridge for the sprite page: status, theme, and window moves."""

    def __init__(self, pool, accent_source):
        self._pool = pool
        self._accent_source = accent_source
        self._window = None
        self._hwnd = None
        self._grab = None

    def bind(self, window):
        self._window = window
        self._hwnd = None

    def _handle(self):
        if not self._hwnd or not _user32.IsWindow(self._hwnd):
            self._hwnd = _own_window(SPRITE_TITLE)
        return self._hwnd

    def get_status(self):
        """Current status of all tabs folded into one."""
        clients = list(self._pool._clients.values())
        return reduce_status(
            [client.state for client in clients],
            [client.last_error_at for client in clients],
            [at for client in clients for at in client.turn_end_times],
            time.time(),
            waiting=[bool(client.waiting_on) for client in clients],
            success_times=[client.last_success_at for client in clients],
        )

    def get_accent(self):
        """Same palette payload the chat window gets."""
        return self._accent_source()

    def get_bounds(self):
        """Window rect in physical pixels."""
        rect = wintypes.RECT()
        with per_monitor_dpi():
            _user32.GetWindowRect(self._handle(), ctypes.byref(rect))
        return {"x": rect.left, "y": rect.top, "w": rect.right - rect.left, "h": rect.bottom - rect.top}

    def save_bounds(self):
        _save_sprite_prefs(bounds=self.get_bounds())

    def _cursor(self):
        point = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def begin_drag(self):
        """Remembers where the cursor holds the window, so drag_to keeps that spot under it."""
        with per_monitor_dpi():
            cursor = self._cursor()
        bounds = self.get_bounds()
        self._grab = (cursor[0] - bounds["x"], cursor[1] - bounds["y"])

    def drag_to(self):
        """Moves the window so the held spot follows the cursor; True when it moved."""
        if not self._grab:
            return False
        with per_monitor_dpi():
            x, y = self._cursor()
            _user32.SetWindowPos(self._handle(), None, x - self._grab[0], y - self._grab[1], 0, 0,
                                 SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
        return True

    def end_drag(self, moved):
        """Ends a drag and saves the position if the window moved."""
        self._grab = None
        if moved:
            self.save_bounds()

    def restore(self):
        """Moves to the saved position if it is still on a monitor, then sizes for that monitor's DPI."""
        hwnd = self._handle()
        saved = _sprite_prefs().get("bounds")
        with per_monitor_dpi():
            if isinstance(saved, dict) and all(key in saved for key in ("x", "y", "w", "h")):
                rect = wintypes.RECT(int(saved["x"]), int(saved["y"]), int(saved["x"] + saved["w"]), int(saved["y"] + saved["h"]))
                if _user32.MonitorFromRect(ctypes.byref(rect), MONITOR_DEFAULTTONULL):
                    _user32.SetWindowPos(hwnd, None, int(saved["x"]), int(saved["y"]), 0, 0, SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOSIZE)
            side = round(SPRITE_SIZE * _user32.GetDpiForWindow(hwnd) / 96)
            _user32.SetWindowPos(hwnd, None, 0, 0, side, side, SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOMOVE)

    def set_tile_region(self, radius):
        """Clips the window to a rounded rect with the given corner radius in physical px."""
        hwnd = self._handle()
        bounds = self.get_bounds()
        diameter = int(round(radius * 2))
        region = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, bounds["w"] + 1, bounds["h"] + 1, diameter, diameter)
        for attribute, value in ((DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED), (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND)):
            setting = ctypes.c_int(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(setting), ctypes.sizeof(setting))
        with per_monitor_dpi():
            _user32.SetWindowRgn(hwnd, region, True)


def use_per_monitor_dpi():
    """Makes the process per-monitor DPI aware so every window renders crisp at each monitor's scale.

    Call before the first window; WebView2 needs every window in the process to share one DPI mode.
    """
    _user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    if not _user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
        logger.warning("dpi: could not make the process per-monitor aware (error %d)", ctypes.GetLastError())


class StatusSprite:
    """Opens, closes, and themes the status sprite window."""

    def __init__(self, pool, accent_source, html):
        self._api = SpriteApi(pool, accent_source)
        self._html = html
        self._window = None
        self._lock = threading.Lock()

    @property
    def is_open(self):
        return self._window is not None

    def open(self):
        with self._lock:
            if self._window:
                return
            window = self._create_window()
            self._api.bind(window)
            window.events.shown += self._api.restore
            window.events.closed += self._on_closed
            self._window = window
            if window.events.shown.is_set():
                self._api.restore()
        logger.info("status sprite: opened")

    def _create_window(self):
        return webview.create_window(
                SPRITE_TITLE,
                html=self._html,
                js_api=self._api,
                width=SPRITE_SIZE,
                height=SPRITE_SIZE,
                frameless=True,
                easy_drag=False,
                resizable=False,
                min_size=(64, 64),
                on_top=True,
                background_color="#000000",
            )

    def close(self):
        with self._lock:
            window, self._window = self._window, None
        if window:
            try:
                window.destroy()
            except Exception as e:
                logger.warning("status sprite: close failed: %s", e)
            logger.info("status sprite: closed")

    def _on_closed(self):
        with self._lock:
            self._window = None

    def set_enabled(self, on):
        """Shows or hides the sprite and remembers the choice."""
        _save_sprite_prefs(on=bool(on))
        if on:
            self.open()
        else:
            self.close()
        return {"on": bool(on)}

    def push_accent(self, palette):
        """Recolors an open sprite with the given palette payload."""
        window = self._window
        if not window:
            return
        payload = json.dumps(palette).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        try:
            window.evaluate_js(f"if(window.applySpriteTheme)window.applySpriteTheme(JSON.parse(`{payload}`))")
        except Exception as e:
            logger.debug("status sprite: theme push failed: %s", e)
