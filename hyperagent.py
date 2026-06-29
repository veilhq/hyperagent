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
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import webview

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HYPERAGENT_DIR = Path(__file__).parent.resolve()
HYPERSPACE_ROOT = HYPERAGENT_DIR.parent
PORTAL_ROOT = HYPERSPACE_ROOT.parent
HYPERVISOR_DIR = HYPERSPACE_ROOT / ".hypervisor"
PREFS_FILE = HYPERAGENT_DIR / "preferences.json"
ICON_FILE = HYPERVISOR_DIR / "assets" / "ha-box.ico"

# ---------------------------------------------------------------------------
# Debug log
# ---------------------------------------------------------------------------

_LOG_FILE = HYPERAGENT_DIR / "debug.log"

def _log(msg):
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{time.time():.3f} {msg}\n")


# ---------------------------------------------------------------------------
# Thread-safe preferences I/O
# ---------------------------------------------------------------------------

_prefs_lock = threading.Lock()


def _load_prefs():
    with _prefs_lock:
        if PREFS_FILE.exists():
            try:
                return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}


def _save_prefs(prefs):
    with _prefs_lock:
        PREFS_FILE.write_text(json.dumps(prefs, indent=2), encoding="utf-8")


def _save_session_id(sid):
    prefs = _load_prefs()
    prefs["sessionId"] = sid
    _save_prefs(prefs)


def _clear_session_id():
    prefs = _load_prefs()
    prefs.pop("sessionId", None)
    _save_prefs(prefs)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _check_auth():
    """Return True if kiro-cli is already authenticated."""
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        r = subprocess.run(
            ["kiro-cli", "whoami"],
            capture_output=True, text=True, startupinfo=si, timeout=10
        )
        return r.returncode == 0 and "Logged in" in r.stdout
    except Exception as e:
        _log(f"_check_auth error: {e}")
        return False


def _do_login(window=None):
    """Run device-flow login. Pushes URL to frontend if window available.
    Returns True on success."""
    _log("_do_login: starting device flow")
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        proc = subprocess.Popen(
            ["kiro-cli", "login", "--license", "pro", "--use-device-flow"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, startupinfo=si,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        # Read output line by line looking for the verification URL
        url_pushed = False
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            _log(f"_do_login output: {line.strip()}")
            # Look for URL in output (kiro-cli prints the verification URI)
            if not url_pushed and ("http" in line.lower()):
                # Extract URL
                import re as _re
                urls = _re.findall(r'https?://\S+', line)
                if urls and window:
                    url_pushed = True
                    payload = json.dumps({"url": urls[0]}).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                    try:
                        window.evaluate_js(
                            f"if(window.__acpAuthRequired)window.__acpAuthRequired(JSON.parse(`{payload}`))"
                        )
                    except Exception:
                        pass
        proc.wait(timeout=120)
        success = proc.returncode == 0
        _log(f"_do_login: exit={proc.returncode}")
        return success
    except Exception as e:
        _log(f"_do_login error: {e}")
        return False


def _do_login_visible():
    """Run 'kiro-cli login' in a visible console so interactive prompts (AWS SSO, etc.) work.
    Blocks until the process exits. Returns True on success."""
    _log("_do_login_visible: spawning visible console")
    try:
        proc = subprocess.Popen(
            ["kiro-cli", "login", "--license", "pro"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        proc.wait(timeout=120)
        _log(f"_do_login_visible: exit={proc.returncode}")
        return proc.returncode == 0
    except Exception as e:
        _log(f"_do_login_visible error: {e}")
        return False


# ---------------------------------------------------------------------------
# ACPClient — manages the kiro-cli subprocess and JSON-RPC protocol
# ---------------------------------------------------------------------------

class ACPClient:
    def __init__(self, session_id=None, window=None):
        self._process = None
        self._socket = None
        self._sockfile = None
        self._window = window
        self._state = "stopped"  # stopped | starting | ready | prompting
        self._id_counter = 0
        self._pending = {}  # id -> callback
        self._session_id = session_id
        self._owned_sessions = set()  # session IDs created by this instance
        self._lock = threading.Lock()
        self._last_push = 0
        self._server_sock = None
        self._last_metadata = None
        self._active_prompt_id = None
        self._on_session_assigned = None  # callback(session_id) for registry

    def set_window(self, window):
        self._window = window

    @property
    def state(self):
        return self._state

    # --- Subprocess lifecycle ---

    def start_process(self):
        """Spawn bridge + kiro-cli. Call BEFORE webview.start()."""
        # Auth gate: ensure login is valid before spawning the hidden subprocess.
        # This prevents kiro-cli from hanging/crashing inside the bridge due to
        # expired AWS credentials that need interactive login.
        if not _check_auth():
            _log("start_process: not authenticated, triggering visible login")
            if self._window:
                self._push_js("__acpAuthRequired", {"url": None})
            success = _do_login_visible()
            if not success or not _check_auth():
                _log("start_process: login failed")
                self._state = "crashed"
                if self._window:
                    self._push_js("__acpError", {"error": "Login failed — complete login in the console window, then click Reconnect"})
                    self._push_state()
                return
            if self._window:
                self._push_js("__acpAuthComplete", {})

        # Create TCP server to accept bridge connection
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.bind(("127.0.0.1", 0))
        self._server_sock.listen(1)
        port = self._server_sock.getsockname()[1]
        _log(f"start_process: listening on port {port}")

        bridge = str(HYPERAGENT_DIR / "acp_bridge.py")
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        self._process = subprocess.Popen(
            [sys.executable, bridge, str(port)],
            startupinfo=si,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        _log(f"start_process: bridge pid={self._process.pid}")

        # Accept connection from bridge
        self._server_sock.settimeout(10)
        try:
            self._socket, _ = self._server_sock.accept()
            self._sockfile = self._socket.makefile("rwb")
            _log("start_process: bridge connected")
        except socket.timeout:
            _log("start_process: bridge connection timeout")
            self._state = "crashed"
            return

        threading.Thread(target=self._read_stdout, daemon=True).start()

    def connect(self):
        """Initialize the ACP protocol. Call AFTER window is ready."""
        if not self._socket:
            self._state = "crashed"
            self._push_js("__acpError", {"error": "kiro-cli not found or failed to start"})
            self._push_state()
            return
        self._state = "starting"
        self._push_state()
        self._initialize()

    def start(self):
        """Full start for reconnect scenarios."""
        self.stop()
        self.start_process()
        if self._window:
            self.connect()

    def stop(self):
        self._state = "stopped"
        self._owned_sessions.clear()
        try:
            if self._socket:
                self._socket.close()
        except Exception:
            pass
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                self._process.kill()
        self._process = None
        self._socket = None
        self._sockfile = None

    def _find_kiro(self):
        found = shutil.which("kiro-cli")
        if found:
            return found
        fallback = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "bin" / "kiro-cli.exe"
        if fallback.exists():
            return str(fallback)
        return None

    # --- JSON-RPC send/receive ---

    def _next_id(self):
        self._id_counter += 1
        return self._id_counter

    def _send(self, msg):
        if not self._sockfile:
            return
        data = json.dumps(msg) + "\n"
        try:
            self._sockfile.write(data.encode())
            self._sockfile.flush()
            _log(f"sent: id={msg.get('id')} method={msg.get('method','')}")
        except (BrokenPipeError, OSError) as e:
            _log(f"send error: {e}")

    def _request(self, method, params=None, callback=None):
        rid = self._next_id()
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params:
            msg["params"] = params
        if callback:
            self._pending[rid] = callback
        self._send(msg)
        return rid

    def _notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self._send(msg)

    # --- Protocol flow ---

    def _initialize(self):
        def on_init(result):
            _log(f"on_init called: {str(result)[:100]}")
            # Don't create a session yet — show welcome screen immediately.
            # A session is created lazily on first prompt or sidebar load.
            self._state = "ready"
            self._push_state()

        self._request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": True, "writeTextFile": True}, "terminal": True},
            "clientInfo": {"name": "hyperagent", "version": "1.0.0"}
        }, on_init)

    def _new_session(self):
        cwd = str(PORTAL_ROOT).replace("\\", "/")
        self._request("session/new", {
            "cwd": cwd,
            "mcpServers": []
        }, self._on_session)

    def _on_session(self, result):
        _log(f"_on_session: {result}")
        if isinstance(result, dict) and "sessionId" in result:
            self._session_id = result["sessionId"]
            self._owned_sessions.add(self._session_id)
            _save_session_id(self._session_id)
            self._state = "ready"
            if self._on_session_assigned:
                self._on_session_assigned(self._session_id)
        elif isinstance(result, dict) and "error" in result:
            # session/load failed — create new
            self._new_session()
            return
        else:
            self._state = "ready"
        self._push_state()

    def prompt(self, text):
        if self._state != "ready":
            return
        # Lazy session creation: if no session exists yet, create one then prompt
        if not self._session_id:
            self._state = "prompting"
            self._prompt_start = time.time()
            self._push_state()
            def on_lazy_session(result):
                # Extract session ID without pushing "ready" state to frontend
                _log(f"on_lazy_session: {result}")
                if isinstance(result, dict) and "sessionId" in result:
                    self._session_id = result["sessionId"]
                    self._owned_sessions.add(self._session_id)
                    _save_session_id(self._session_id)
                elif isinstance(result, dict) and "error" in result:
                    self._state = "ready"
                    self._push_state()
                    self._push_js("__acpError", {"error": "Failed to create session"})
                    return
                # Now send the actual prompt
                if self._session_id:
                    rid = self._request("session/prompt", {
                        "sessionId": self._session_id,
                        "prompt": [{"type": "text", "text": text}]
                    }, self._on_prompt_done)
                    self._active_prompt_id = rid
            cwd = str(PORTAL_ROOT).replace("\\", "/")
            self._request("session/new", {"cwd": cwd, "mcpServers": []}, on_lazy_session)
            return
        self._state = "prompting"
        self._prompt_start = time.time()
        self._push_state()
        rid = self._request("session/prompt", {
            "sessionId": self._session_id,
            "prompt": [{"type": "text", "text": text}]
        }, self._on_prompt_done)
        self._active_prompt_id = rid

    def _on_prompt_done(self, result):
        elapsed = round(time.time() - getattr(self, '_prompt_start', time.time()), 1)
        _log(f"prompt_done: {json.dumps(result)[:500]}")
        self._state = "ready"
        data = result or {}
        data["_elapsed"] = elapsed
        data["_sessionId"] = self._session_id
        if hasattr(self, '_last_metadata') and self._last_metadata:
            data["_metadata"] = self._last_metadata
            self._last_metadata = None
        self._push_js("__acpTurnEnd", data)
        self._push_state()

    def cancel(self):
        if self._state == "prompting" and self._session_id:
            # Remove the pending callback for the active prompt so
            # the stale response from kiro-cli is silently dropped
            if self._active_prompt_id is not None:
                self._pending.pop(self._active_prompt_id, None)
            self._active_prompt_id = None
            self._request("session/cancel", {"sessionId": self._session_id})
            self._state = "ready"
            self._push_js("__acpTurnEnd", {"_cancelled": True, "_sessionId": self._session_id})
            self._push_state()

    def new_session(self):
        if self._state not in ("ready",):
            return
        self._session_id = None
        _clear_session_id()
        self._state = "ready"
        self._push_state()
        self._push_js("__acpNewSession", {})

    # --- Stdout reader ---

    def _read_stdout(self):
        """Read from socket (relayed from bridge)."""
        try:
            while self._sockfile:
                line = self._sockfile.readline()
                if not line:
                    break
                if line.strip():
                    try:
                        msg = json.loads(line)
                        _log(f"recv: id={msg.get('id')} method={msg.get('method','')}")
                        self._dispatch(msg)
                    except json.JSONDecodeError as e:
                        _log(f"JSON decode error: {e}")
        except Exception as e:
            _log(f"reader exception: {e}")
        _log(f"reader exited, state={self._state}")
        if self._state not in ("stopped",):
            self._state = "crashed"
            self._push_state()

    def _drain_stderr(self):
        pass  # Bridge handles stderr

    # --- Message dispatch ---

    def _dispatch(self, msg):
        # Response to a request we sent
        if "id" in msg and msg["id"] in self._pending:
            cb = self._pending.pop(msg["id"])
            result = msg.get("result") or msg.get("error")
            if msg.get("error"):
                result = {"error": msg["error"]}
            threading.Thread(target=cb, args=(result,), daemon=True).start()
            return

        # Server-initiated request (permission prompts)
        if "id" in msg and "method" in msg:
            self._handle_server_request(msg)
            return

        # Notification (session/update)
        method = msg.get("method", "")
        if method == "session/update":
            update = msg.get("params", {}).get("update", {})
            su_type = update.get("sessionUpdate", "unknown")
            if su_type != "agent_message_chunk":
                _log(f"session_update: type={su_type} id={update.get('toolCallId','')[:20]} title={update.get('title','')}")
            update["_sessionId"] = self._session_id
            self._push_js_throttled("__acpUpdate", update)
        elif method == "_kiro.dev/metadata":
            params = msg.get("params", {})
            _log(f"metadata: {json.dumps(params)[:500]}")
            self._last_metadata = params
        elif method == "_kiro.dev/session/update":
            params = msg.get("params", {})
            _log(f"session_update_dev: {json.dumps(params)[:500]}")
            # Push tool name hint to frontend for icon resolution
            update = params.get("update", {})
            if update.get("sessionUpdate") == "tool_call_chunk":
                self._push_js("__acpToolHint", {
                    "toolCallId": update.get("toolCallId", ""),
                    "name": update.get("title", ""),
                    "kind": update.get("kind", "")
                })

    def _handle_server_request(self, msg):
        method = msg.get("method", "")
        if "permission" in method or "confirm" in method:
            # Auto-approve
            rid = msg["id"]
            options = msg.get("params", {}).get("options", [])
            allow = next((o for o in options if "allow" in o.get("kind", "")), options[0] if options else None)
            if allow:
                self._send({"jsonrpc": "2.0", "id": rid,
                    "result": {"outcome": {"outcome": "selected", "optionId": allow["optionId"]}}})
            else:
                self._send({"jsonrpc": "2.0", "id": rid, "result": {"outcome": {"outcome": "selected"}}})

    # --- Push to frontend ---

    def _push_js(self, fn_name, data):
        if not self._window:
            return
        payload = json.dumps(data).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        try:
            self._window.evaluate_js(
                f"if(window.{fn_name})window.{fn_name}(JSON.parse(`{payload}`))"
            )
            _log(f"push_js OK: {fn_name}")
        except Exception as e:
            _log(f"push_js FAIL: {fn_name} -> {e}")

    def _push_js_throttled(self, fn_name, data):
        now = time.time()
        if now - self._last_push < 0.016:
            time.sleep(0.016 - (now - self._last_push))
        self._last_push = time.time()
        self._push_js(fn_name, data)

    def _push_state(self):
        self._push_js("__acpStateChange", {"state": self._state, "_sessionId": self._session_id})

    # --- Session persistence ---

    # Session persistence delegated to module-level _load_prefs / _save_prefs


# ---------------------------------------------------------------------------
# PyWebView Bridge
# ---------------------------------------------------------------------------

class HyperagentAPI:
    MAX_LIVE_SESSIONS = 4

    def __init__(self, initial_client):
        self._clients = {}  # session_id -> ACPClient
        self._active_session_id = None
        self._window = None
        # Keep a reference to the initial client for bootstrapping
        self._boot_client = initial_client

    @property
    def _acp(self):
        """Active ACPClient — backwards-compatible accessor."""
        if self._active_session_id and self._active_session_id in self._clients:
            return self._clients[self._active_session_id]
        return self._boot_client

    def _register_client(self, session_id, client):
        """Add a client to the registry."""
        self._clients[session_id] = client

    def _unregister_client(self, session_id):
        """Remove a client from the registry and stop it."""
        client = self._clients.pop(session_id, None)
        if client:
            client.stop()

    def _live_count(self):
        """Number of live (non-stopped) clients."""
        return sum(1 for c in self._clients.values() if c._state != "stopped")

    def send_prompt(self, text):
        if text and text.strip():
            threading.Thread(target=self._acp.prompt, args=(text.strip(),), daemon=True).start()

    def rename_session(self, session_id, name):
        """Rename a session — persist custom name to preferences."""
        name = (name or "").strip()[:50]
        if not name:
            return False
        prefs = _load_prefs()
        titles = prefs.get("sessionTitles", {})
        titles[session_id] = name
        prefs["sessionTitles"] = titles
        _save_prefs(prefs)
        return True

    def cancel(self):
        self._acp.cancel()

    def new_session(self):
        threading.Thread(target=self._acp.new_session, daemon=True).start()

    def reconnect(self):
        threading.Thread(target=self._acp.start, daemon=True).start()

    def pin_session(self, session_id):
        """Pin a session — keep its ACPClient alive in the background."""
        if self._live_count() >= self.MAX_LIVE_SESSIONS:
            self._acp._push_js("__acpError", {"error": f"Max {self.MAX_LIVE_SESSIONS} live sessions reached"})
            return False
        # If the session already has a live client, just mark as pinned in prefs
        prefs = _load_prefs()
        pinned = set(prefs.get("pinnedSessions", []))
        pinned.add(session_id)
        prefs["pinnedSessions"] = list(pinned)
        _save_prefs(prefs)
        return True

    def unpin_session(self, session_id):
        """Unpin a session — tear down its subprocess if it's not the active session."""
        prefs = _load_prefs()
        pinned = set(prefs.get("pinnedSessions", []))
        pinned.discard(session_id)
        prefs["pinnedSessions"] = list(pinned)
        _save_prefs(prefs)
        # Tear down if not the active session
        if session_id != self._active_session_id:
            self._unregister_client(session_id)
        return True

    def switch_session(self, session_id):
        """Switch active session. Instant for pinned (live), teardown/reload for cold."""
        threading.Thread(
            target=self._switch_session_async, args=(session_id,), daemon=True
        ).start()

    def _switch_session_async(self, session_id):
        """Internal: handle session switch logic."""
        if session_id == self._active_session_id:
            return

        prefs = _load_prefs()
        pinned = set(prefs.get("pinnedSessions", []))

        # If target has a live client in registry — instant swap
        if session_id in self._clients and self._clients[session_id]._state != "stopped":
            old_id = self._active_session_id
            self._active_session_id = session_id
            _save_session_id(session_id)
            target_state = self._clients[session_id]._state
            self._acp._push_js("__acpSessionSwitched", {"sessionId": session_id, "instant": True, "state": target_state})
            return

        # Current session is mid-prompt — protect it before switching
        current_client = self._clients.get(self._active_session_id)
        if current_client and current_client._state == "prompting":
            if self._active_session_id not in pinned:
                # Auto-pin so it continues processing in background
                if self._live_count() < self.MAX_LIVE_SESSIONS:
                    pinned.add(self._active_session_id)
                    prefs["pinnedSessions"] = list(pinned)
                    _save_prefs(prefs)
                else:
                    # At limit — cancel the prompt before tearing down
                    current_client.cancel()

        # Cold switch — tear down current if not pinned
        if self._active_session_id and self._active_session_id not in pinned:
            self._unregister_client(self._active_session_id)

        # Load via existing load_session path
        self._active_session_id = session_id
        self._load_session_async(session_id)

    def get_state(self):
        return self._acp.state

    def toggle_fullscreen(self):
        if self._acp._window:
            self._acp._window.toggle_fullscreen()

    def get_accent(self):
        """Read theme from hypervisor's theme-defaults.json and return full palette."""
        theme_file = HYPERVISOR_DIR / "theme-defaults.json"
        try:
            data = json.loads(theme_file.read_text(encoding="utf-8"))
            accent = data.get("accent", "#00ff41")
            mode = data.get("paletteMode", "split")
        except Exception:
            accent, mode = "#00ff41", "split"
        return self._build_palette(accent, mode)

    @staticmethod
    def _build_palette(hex_color, mode):
        """Derive warm/cool/comp from accent + palette mode (mirrors hypervisor theme.js)."""
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        # RGB to HSL
        r1, g1, b1 = r / 255, g / 255, b / 255
        mx, mn = max(r1, g1, b1), min(r1, g1, b1)
        l = (mx + mn) / 2
        if mx == mn:
            h = s = 0.0
        else:
            d = mx - mn
            s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
            if mx == r1:
                h = ((g1 - b1) / d + (6 if g1 < b1 else 0)) / 6
            elif mx == g1:
                h = ((b1 - r1) / d + 2) / 6
            else:
                h = ((r1 - g1) / d + 4) / 6
        h *= 360

        def hsl_to_hex(hh, ss, ll):
            hh = ((hh % 360) + 360) % 360
            c = (1 - abs(2 * ll - 1)) * ss
            x = c * (1 - abs((hh / 60) % 2 - 1))
            m = ll - c / 2
            if hh < 60:     rr, gg, bb = c, x, 0
            elif hh < 120:  rr, gg, bb = x, c, 0
            elif hh < 180:  rr, gg, bb = 0, c, x
            elif hh < 240:  rr, gg, bb = 0, x, c
            elif hh < 300:  rr, gg, bb = x, 0, c
            else:           rr, gg, bb = c, 0, x
            return "#{:02x}{:02x}{:02x}".format(
                round((rr + m) * 255), round((gg + m) * 255), round((bb + m) * 255))

        if mode == "triadic":
            warm = hsl_to_hex(h + 120, min(s * 1.1, 1), min(l * 1.15, 0.75))
            cool = hsl_to_hex(h + 240, min(s * 0.9, 1), min(l * 0.95, 0.65))
            comp = hsl_to_hex(h + 180, s * 0.7, min(l * 0.85, 0.55))
        elif mode == "analogous":
            warm = hsl_to_hex(h + 30, min(s * 1.05, 1), min(l * 1.1, 0.75))
            cool = hsl_to_hex(h + 60, min(s * 0.9, 1), min(l * 0.95, 0.65))
            comp = hsl_to_hex(h - 30, s * 0.85, min(l * 0.9, 0.6))
        elif mode == "square":
            warm = hsl_to_hex(h + 90, min(s * 1.1, 1), min(l * 1.1, 0.75))
            cool = hsl_to_hex(h + 180, min(s * 0.9, 1), min(l * 0.95, 0.65))
            comp = hsl_to_hex(h + 270, s * 0.8, min(l * 0.85, 0.55))
        elif mode == "complement":
            warm = hsl_to_hex(h + 180, min(s * 1.1, 1), min(l * 1.2, 0.75))
            cool = hsl_to_hex(h + 180, min(s * 0.7, 1), min(l * 0.7, 0.5))
            comp = hsl_to_hex(h, s * 0.5, min(l * 0.6, 0.4))
        else:  # split
            warm = hsl_to_hex(h + 150, min(s * 1.1, 1), min(l * 1.15, 0.75))
            cool = hsl_to_hex(h + 210, min(s * 0.9, 1), min(l * 0.95, 0.65))
            comp = hsl_to_hex(h + 180, s * 0.7, min(l * 0.85, 0.55))

        return {"accent": hex_color, "warm": warm, "cool": cool, "comp": comp}

    def _is_session_locked(self, session_id):
        """Check if a session lock file is held by a running process (Windows)."""
        try:
            lock_file = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli" / f"{session_id}.lock"
            if not lock_file.exists():
                return False
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            if pid == 0:
                return False
            # Don't mark our own kiro-cli's sessions as locked
            if pid in self._get_own_kiro_pids():
                return False
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False

    def _get_own_kiro_pids(self):
        """Get PIDs of all kiro-cli processes owned by any live client in the registry."""
        pids = set()
        try:
            sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
            # Collect owned sessions from all live clients
            sessions_to_check = set()
            for client in self._clients.values():
                sessions_to_check.update(client._owned_sessions)
                if client._session_id:
                    sessions_to_check.add(client._session_id)
            # Also check boot client
            sessions_to_check.update(self._boot_client._owned_sessions)
            if self._boot_client._session_id:
                sessions_to_check.add(self._boot_client._session_id)
            for sid in sessions_to_check:
                lock_file = sessions_dir / f"{sid}.lock"
                if lock_file.exists():
                    data = json.loads(lock_file.read_text(encoding="utf-8"))
                    pid = int(data.get("pid", 0))
                    if pid:
                        pids.add(pid)
        except Exception:
            pass
        return pids

    def list_sessions(self):
        """List sessions by reading metadata directly from the filesystem."""
        if not _check_auth():
            return {"sessions": [], "active": None, "auth_required": True}
        try:
            sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
            if not sessions_dir.exists():
                return {"sessions": [], "active": self._acp._session_id}
            project_cwd = str(PORTAL_ROOT).replace("\\", "/")
            sessions = []
            now = datetime.now(timezone.utc)
            for meta_file in sessions_dir.glob("*.json"):
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                # Filter to sessions for this project
                if data.get("cwd", "").rstrip("/") != project_cwd.rstrip("/"):
                    continue
                sid = meta_file.stem
                title = data.get("title", "(no title)") or "(no title)"
                if len(title) > 40:
                    title = title[:40].rstrip() + "..."
                # Compute relative age from updated_at
                updated = data.get("updated_at") or data.get("created_at", "")
                age = self._relative_age(updated, now)
                # Count messages from JSONL line count
                jsonl_file = sessions_dir / f"{sid}.jsonl"
                msg_count = 0
                if jsonl_file.exists():
                    try:
                        with open(jsonl_file, "r", encoding="utf-8") as f:
                            msg_count = sum(1 for _ in f)
                    except OSError:
                        pass
                sessions.append({
                    "id": sid, "age": age,
                    "title": title, "msgs": f"{msg_count} msgs",
                    "locked": self._is_session_locked(sid),
                    "_updated": updated,
                })
            # Sort by most recently updated first
            sessions.sort(key=lambda s: s.get("_updated", ""), reverse=True)
            for s in sessions:
                del s["_updated"]
            # Override with AI-generated titles and add pin/state info
            prefs = _load_prefs()
            saved_titles = prefs.get("sessionTitles", {})
            pinned_ids = set(prefs.get("pinnedSessions", []))
            for s in sessions:
                if s["id"] in saved_titles:
                    s["title"] = saved_titles[s["id"]]
                s["pinned"] = s["id"] in pinned_ids
                # Check if session has a live client with state info
                client = self._clients.get(s["id"])
                s["processing"] = bool(client and client._state == "prompting")
                s["completed"] = False  # set by frontend via indicator updates
            return {"sessions": sessions, "active": self._active_session_id or self._acp._session_id}
        except Exception as e:
            _log(f"list_sessions error: {e}")
            return {"sessions": [], "active": None}

    @staticmethod
    def _relative_age(iso_str, now):
        """Convert an ISO timestamp to a human-readable relative age."""
        try:
            # Handle nanosecond precision by truncating to microseconds
            iso_str = re.sub(r'(\.\d{6})\d+', r'\1', iso_str)
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            delta = now - dt
            secs = int(delta.total_seconds())
            if secs < 60:
                return f"{secs} seconds ago"
            mins = secs // 60
            if mins < 60:
                return f"{mins} minute{'s' if mins != 1 else ''} ago"
            hours = mins // 60
            if hours < 24:
                return f"{hours} hour{'s' if hours != 1 else ''} ago"
            days = hours // 24
            return f"{days} day{'s' if days != 1 else ''} ago"
        except Exception:
            return ""

    def load_session(self, session_id):
        """Load an existing session by ID."""
        threading.Thread(
            target=self._load_session_async, args=(session_id,), daemon=True
        ).start()

    def delete_session(self, session_id):
        """Delete a session by removing its files directly.

        The kiro-cli subprocess approach doesn't work because the running ACP
        process holds an advisory lock on the session store, causing the
        separate kiro-cli delete command to silently fail.
        """
        # Reject deletion of any session with a live client (active or pinned)
        if session_id in self._clients and self._clients[session_id]._state != "stopped":
            return False
        try:
            sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
            # Remove all files matching the session ID (*.json, *.jsonl, *.lock, *.history)
            for f in sessions_dir.glob(f"{session_id}*"):
                if f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    def _delete_session_files(self, session_id):
        """Remove session files from disk (used to clean up throwaway sessions)."""
        try:
            sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
            for f in sessions_dir.glob(f"{session_id}*"):
                if f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink(missing_ok=True)
        except Exception:
            pass

    def get_session_history(self, session_id):
        """Read messages from a session's JSONL file."""
        sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
        jsonl_file = sessions_dir / f"{session_id}.jsonl"
        if not jsonl_file.exists():
            return []
        try:
            messages = []
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if '"ToolResults"' in line[:50] or '"ToolUse"' in line[:50]:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    kind = entry.get("kind")
                    data = entry.get("data", {})
                    content = data.get("content", [])
                    if kind == "Prompt":
                        for c in content:
                            if c.get("kind") == "text" and c.get("data"):
                                messages.append({"role": "user", "text": c["data"]})
                                break
                    elif kind == "AssistantMessage":
                        for c in content:
                            if c.get("kind") == "text" and c.get("data"):
                                messages.append({"role": "agent", "text": c["data"]})
                                break
            return messages
        except Exception as e:
            _log(f"get_session_history error: {e}")
            return []

    def _load_session_async(self, session_id):
        self._acp._state = "starting"
        self._acp._push_state()

        # Read history immediately so frontend can start rendering
        history = self.get_session_history(session_id)
        self._acp._push_js("__acpSessionLoaded", {"sessionId": session_id, "messages": history})

        throwaway_to_clean = [None]  # mutable container for closure

        def on_load_result(result):
            if isinstance(result, dict) and "error" in result:
                _log(f"sidebar load failed: {result}")
                self._acp._push_js("__acpError", {"error": f"Failed to load session"})
            else:
                self._acp._session_id = session_id
                _save_session_id(session_id)
                # Register client under the loaded session ID
                self._register_client(session_id, self._acp)
                self._active_session_id = session_id
                # Safe to delete throwaway now that session/load succeeded
                if throwaway_to_clean[0]:
                    self._delete_session_files(throwaway_to_clean[0])
            self._acp._state = "ready"
            self._acp._push_state()

        # session/load requires releasing current session first via session/new
        def on_new_done(result):
            throwaway_id = result.get("sessionId") if isinstance(result, dict) else None
            if throwaway_id:
                self._acp._owned_sessions.add(throwaway_id)
                throwaway_to_clean[0] = throwaway_id
            self._acp._request("session/load", {
                "sessionId": session_id,
                "cwd": str(PORTAL_ROOT).replace("\\", "/"),
                "mcpServers": []
            }, on_load_result)

        cwd = str(PORTAL_ROOT).replace("\\", "/")
        self._acp._request("session/new", {"cwd": cwd, "mcpServers": []}, on_new_done)


# ---------------------------------------------------------------------------
# Inline HTML
# ---------------------------------------------------------------------------

from generated_html import HTML


# ---------------------------------------------------------------------------
# Main
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


def _start_theme_watcher(window, api):
    """Poll theme-defaults.json for changes and push palette updates to frontend."""
    theme_file = HYPERVISOR_DIR / "theme-defaults.json"
    last_mtime = theme_file.stat().st_mtime if theme_file.exists() else 0

    def _watch():
        nonlocal last_mtime
        while True:
            time.sleep(2)
            try:
                if not theme_file.exists():
                    continue
                mtime = theme_file.stat().st_mtime
                if mtime != last_mtime:
                    last_mtime = mtime
                    palette = api.get_accent()
                    payload = json.dumps(palette).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                    window.evaluate_js(f"if(window.applyAccent)window.applyAccent(JSON.parse(`{payload}`))")
            except Exception:
                pass

    threading.Thread(target=_watch, daemon=True).start()


def main():
    _log("main() starting")

    # Clean up empty sessions (0 messages) left over from session switching
    sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
    if sessions_dir.exists():
        for jsonl in sessions_dir.glob("*.jsonl"):
            if jsonl.stat().st_size == 0:
                sid = jsonl.stem
                for f in sessions_dir.glob(f"{sid}*"):
                    f.unlink(missing_ok=True)

    acp = ACPClient()
    api = HyperagentAPI(acp)

    # When boot client gets a session, register it in the multi-client registry
    def _on_boot_session(session_id):
        api._register_client(session_id, acp)
        api._active_session_id = session_id
    acp._on_session_assigned = _on_boot_session

    icon_path = str(ICON_FILE) if ICON_FILE.exists() else None

    # Spawn ACP subprocess and start reader BEFORE webview to avoid pipe issues
    acp.start_process()

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
        acp.set_window(window)
        api._window = window
        _log("on_start: window ready, connecting protocol")
        acp.connect()
        # Start theme file watcher
        _start_theme_watcher(window, api)

    webview.start(on_start, icon=icon_path, debug=True)
    # Stop all live clients on exit
    for client in list(api._clients.values()):
        client.stop()
    acp.stop()


if __name__ == "__main__":
    main()
