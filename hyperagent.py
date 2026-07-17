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
import uuid
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
SKILLS_DIR = PORTAL_ROOT / ".kiro" / "skills"
PREFS_FILE = HYPERAGENT_DIR / "preferences.json"
ICON_FILE = HYPERVISOR_DIR / "assets" / "ha-box.ico"

# ---------------------------------------------------------------------------
# Structured logging (shared ecosystem logger)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(HYPERSPACE_ROOT))
from hyper_logging import setup_logger, TRACE  # noqa: E402

# Log level is env-configurable. Default is INFO — the lifecycle timeline.
# Set HYPERAGENT_LOG_LEVEL=DEBUG for tool call details and metadata pushes.
# Set HYPERAGENT_LOG_LEVEL=TRACE for wire-protocol replay (every JSON-RPC frame).
import logging as _logging
_LEVEL_MAP = {
    "TRACE": TRACE,
    "DEBUG": _logging.DEBUG,
    "INFO": _logging.INFO,
    "WARNING": _logging.WARNING,
    "ERROR": _logging.ERROR,
}
_env_level = os.environ.get("HYPERAGENT_LOG_LEVEL", "INFO").upper()
_log_level = _LEVEL_MAP.get(_env_level, _logging.INFO)
logger = setup_logger("hyperagent", level=_log_level)
# Ensure the file handler also honors the requested level (setup_logger only
# applies level on first call; on hot-reload the existing handler wins).
for _h in logger.handlers:
    _h.setLevel(_log_level)


# ---------------------------------------------------------------------------
# Skill metadata cache
# ---------------------------------------------------------------------------

def _load_skill_metadata():
    """Scan .kiro/skills/*/SKILL.md and extract name + description from frontmatter."""
    skills = {}
    if not SKILLS_DIR.exists():
        return skills
    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            text = skill_file.read_text(encoding="utf-8")
            # Parse YAML frontmatter between --- fences
            if text.startswith("---"):
                end = text.index("---", 3)
                front = text[3:end]
                name = ""
                desc = ""
                for line in front.strip().splitlines():
                    if line.startswith("name:"):
                        name = line[5:].strip()
                    elif line.startswith("description:"):
                        desc = line[12:].strip()
                if name:
                    skills[name] = {"name": name, "description": desc}
        except Exception:
            continue
    return skills


_SKILL_CACHE = _load_skill_metadata()
_SKILL_MD_PATTERN = re.compile(r"[/\\]\.kiro[/\\]skills[/\\]([^/\\]+)[/\\]SKILL\.md")


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
        logger.error(f"_check_auth error: {e}")
        return False


def _do_login(window=None):
    """Run device-flow login. Pushes URL to frontend if window available.
    Returns True on success."""
    logger.info("_do_login: starting device flow")
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
            logger.info(f"_do_login output: {line.strip()}")
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
        logger.info(f"_do_login: exit={proc.returncode}")
        return success
    except Exception as e:
        logger.error(f"_do_login error: {e}")
        return False


def _do_login_visible():
    """Run 'kiro-cli login' in a visible console so interactive prompts (AWS SSO, etc.) work.
    Blocks until the process exits. Returns True on success."""
    logger.info("_do_login_visible: spawning visible console")
    try:
        proc = subprocess.Popen(
            ["kiro-cli", "login", "--license", "pro"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        proc.wait(timeout=120)
        logger.info(f"_do_login_visible: exit={proc.returncode}")
        return proc.returncode == 0
    except Exception as e:
        logger.error(f"_do_login_visible error: {e}")
        return False


# ---------------------------------------------------------------------------
# ACPClient — manages the kiro-cli subprocess and JSON-RPC protocol
# ---------------------------------------------------------------------------

class ACPClient:
    def __init__(self):
        self._process = None
        self._socket = None
        self._sockfile = None
        self._window = None
        self._state = "stopped"  # stopped | starting | ready | prompting
        self._id_counter = 0
        self._pending = {}  # id -> callback
        self._session_id = None
        self._owned_sessions = set()  # all session IDs created by this process
        self._lock = threading.Lock()
        self._last_push = 0
        self._server_sock = None
        self._last_metadata = None
        self._active_prompt_id = None
        self._skill_tool_ids = set()  # tool call IDs for SKILL.md reads (suppressed from UI)
        self._todo_tool_ids = set()  # tool call IDs for todo_list tools (pushed to task panel)
        self._cancelled = threading.Event()  # suppress session/update after cancel until next prompt
        self._prompt_start = None  # timing anchor for prompt duration logs

    def _tab_ctx(self):
        """Return '[tab=abcdef]' prefix for logs. Empty string if no tab_id assigned."""
        tid = getattr(self, "_tab_id", None)
        if not tid:
            return ""
        return f"[tab={str(tid)[:6]}] "

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
            logger.warning("%sstart_process: not authenticated, triggering visible login", self._tab_ctx())
            if self._window:
                self._push_js("__acpAuthRequired", {"url": None})
            success = _do_login_visible()
            if not success or not _check_auth():
                logger.error("%sstart_process: login failed", self._tab_ctx())
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
        logger.info("%sstart_process: bridge listening on port %d", self._tab_ctx(), port)

        bridge = str(HYPERAGENT_DIR / "acp_bridge.py")
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        self._process = subprocess.Popen(
            [sys.executable, bridge, str(port)],
            startupinfo=si,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        logger.debug("%sstart_process: bridge pid=%d", self._tab_ctx(), self._process.pid)

        # Accept connection from bridge
        self._server_sock.settimeout(10)
        try:
            self._socket, _ = self._server_sock.accept()
            self._sockfile = self._socket.makefile("rwb")
            logger.info("%sstart_process: bridge connected", self._tab_ctx())
        except socket.timeout:
            logger.error("%sstart_process: bridge connection timeout", self._tab_ctx())
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
            logger.trace("%ssent: id=%s method=%s", self._tab_ctx(), msg.get('id'), msg.get('method',''))
        except (BrokenPipeError, OSError) as e:
            logger.error("%ssend error: %s", self._tab_ctx(), e)

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
            logger.trace("%son_init: %s", self._tab_ctx(), str(result)[:100])
            # Don't create a session yet — show welcome screen immediately.
            # A session is created lazily on first prompt or sidebar load.
            if getattr(self, '_suppress_init_ready', False):
                # Tab is loading a session — don't push ready yet
                self._suppress_init_ready = False
                logger.debug("%son_init: suppressed ready (session load pending)", self._tab_ctx())
                return
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

    def _set_session_id(self, session_id):
        """Assign session_id and notify the frontend so tabs[tabId].sessionId stays in sync."""
        self._session_id = session_id
        if session_id:
            self._owned_sessions.add(session_id)
            self._save_session_id(session_id)
        self._push_js("__acpSessionIdChanged", {"sessionId": session_id})

    def _on_session(self, result):
        logger.debug("%s_on_session: %s", self._tab_ctx(), result)
        if isinstance(result, dict) and "sessionId" in result:
            self._set_session_id(result["sessionId"])
            self._state = "ready"
        elif isinstance(result, dict) and "error" in result:
            err = result["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            logger.warning("%s_on_session error, creating new: %s", self._tab_ctx(), err_msg)
            self._push_js("__acpError", {"error": f"Session load failed ({err_msg}), creating new session", "source": "jsonrpc"})
            # session/load failed — create new
            self._new_session()
            return
        else:
            self._state = "ready"
        self._push_state()

    def prompt(self, text):
        if self._state != "ready":
            return
        self._cancelled.clear()  # clear cancel suppression for new prompt
        logger.info("%sprompt: chars=%d lazy=%s", self._tab_ctx(), len(text), not self._session_id)
        # Lazy session creation: if no session exists yet, create one then prompt
        if not self._session_id:
            self._state = "prompting"
            self._prompt_start = time.time()
            self._push_state()
            def on_lazy_session(result):
                # Extract session ID without pushing "ready" state to frontend
                logger.debug("%son_lazy_session: %s", self._tab_ctx(), result)
                if isinstance(result, dict) and "sessionId" in result:
                    self._set_session_id(result["sessionId"])
                elif isinstance(result, dict) and "error" in result:
                    err = result["error"]
                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    self._state = "ready"
                    self._push_state()
                    self._push_js("__acpError", {"error": f"Session creation failed: {err_msg}", "source": "jsonrpc"})
                    logger.warning("%slazy session creation failed: %s", self._tab_ctx(), err_msg)
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
        stop_reason = ""
        if isinstance(result, dict):
            stop_reason = result.get("stopReason") or result.get("stop_reason") or ""
        logger.info("%sprompt done: %.1fs%s", self._tab_ctx(), elapsed, f" reason={stop_reason}" if stop_reason else "")
        logger.trace("%sprompt_done raw: %s", self._tab_ctx(), json.dumps(result)[:500])
        self._state = "ready"
        data = result or {}
        data["_elapsed"] = elapsed
        # Surface error details to frontend
        if isinstance(result, dict) and "error" in result:
            err = result["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            self._push_js("__acpError", {"error": f"Prompt failed: {err_msg}", "source": "jsonrpc"})
            logger.warning("%sprompt failed: %s", self._tab_ctx(), err_msg)
        if hasattr(self, '_last_metadata') and self._last_metadata:
            data["_metadata"] = self._last_metadata
            self._last_metadata = None
        self._skill_tool_ids.clear()
        self._todo_tool_ids.clear()
        self._push_js("__acpTurnEnd", data)
        self._push_state()

    def cancel(self, reason=None):
        reason = reason or "user"
        logger.info("%scancel: reason=%s state=%s", self._tab_ctx(), reason, self._state)
        if self._state == "prompting" and self._session_id:
            self._cancelled.set()
            # Remove the pending callback for the active prompt so
            # the stale response from kiro-cli is silently dropped
            prompt_id = self._active_prompt_id
            if prompt_id is not None:
                self._pending.pop(prompt_id, None)
                logger.debug("%scancel: dropped pending id=%s", self._tab_ctx(), prompt_id)
            self._active_prompt_id = None
            # Send both cancellation mechanisms:
            # 1. session/cancel (ACP notification — must NOT have an id)
            self._notify("session/cancel", {"sessionId": self._session_id})
            # 2. $/cancel_request (ACP generic per-request cancellation, targets the prompt)
            if prompt_id is not None:
                self._notify("$/cancel_request", {"requestId": prompt_id})
            self._state = "ready"
            self._skill_tool_ids.clear()
            # Include elapsed time and any metadata we have
            cancel_data = {"_cancelled": True}
            cancel_data["_elapsed"] = round(time.time() - getattr(self, '_prompt_start', time.time()), 1)
            if self._last_metadata:
                cancel_data["_metadata"] = self._last_metadata
                self._last_metadata = None
            self._push_js("__acpTurnEnd", cancel_data)
            self._push_state()
        else:
            logger.warning("%scancel: SKIPPED (state=%s, session=%s)", self._tab_ctx(), self._state, self._session_id)

    def new_session(self):
        if self._state not in ("ready",):
            return
        logger.info("%snew_session (in-place reset)", self._tab_ctx())
        self._session_id = None
        self._clear_session_id()
        self._todo_tool_ids.clear()
        self._state = "ready"
        self._push_state()
        self._push_js("__acpSessionIdChanged", {"sessionId": None})
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
                        logger.trace("%srecv: id=%s method=%s", self._tab_ctx(), msg.get('id'), msg.get('method',''))
                        self._dispatch(msg)
                    except json.JSONDecodeError as e:
                        logger.error("%sJSON decode error: %s", self._tab_ctx(), e)
        except Exception as e:
            logger.error("%sreader exception: %s", self._tab_ctx(), e)
        logger.info("%sreader exited, state=%s", self._tab_ctx(), self._state)
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
        if method == "_bridge/stderr":
            text = msg.get("params", {}).get("text", "")
            logger.warning("%skiro-cli stderr: %s", self._tab_ctx(), text)
            self._push_js("__acpError", {"error": text, "source": "stderr"})
            return
        if method == "_bridge/child_exited":
            exit_code = msg.get("params", {}).get("exitCode")
            logger.warning("%skiro-cli exited: code=%s (state was %s)", self._tab_ctx(), exit_code, self._state)
            if self._state not in ("stopped", "crashed"):
                self._state = "crashed"
                self._push_state()
                self._push_js("__acpError", {
                    "error": f"kiro-cli exited (code={exit_code})",
                    "source": "child_exited",
                })
            return
        if method == "session/update":
            # Suppress all session updates after cancel until next prompt
            if self._cancelled.is_set():
                logger.debug("%ssession/update suppressed (cancelled)", self._tab_ctx())
                return
            update = msg.get("params", {}).get("update", {})
            su_type = update.get("sessionUpdate", "unknown")
            if su_type != "agent_message_chunk":
                logger.trace("%ssession_update: type=%s id=%s title=%s", self._tab_ctx(), su_type, update.get('toolCallId','')[:20], update.get('title',''))
            # Detect skill activation: a tool_call reading a SKILL.md file
            if su_type == "tool_call":
                # One INFO line per tool call is the useful signal; full JSON at TRACE.
                logger.debug("%stool_call: %s (id=%s)", self._tab_ctx(), update.get('title','?'), update.get('toolCallId','')[:20])
                logger.trace("%stool_call_full: %s", self._tab_ctx(), json.dumps(update)[:800])
                skill_name = self._detect_skill_activation(update)
                if skill_name:
                    if skill_name == "_unknown":
                        meta = {"name": "skill", "description": "Skill context activated"}
                    else:
                        meta = _SKILL_CACHE.get(skill_name, {"name": skill_name, "description": ""})
                    self._push_js("__acpSkillActivation", meta)
                    self._skill_tool_ids.add(update.get("toolCallId", ""))
                    return  # suppress normal tool card
                # Track todo_list tool calls for task panel
                tool_name_meta = ""
                meta_block = update.get("_meta", {})
                if isinstance(meta_block, dict):
                    tool_name_meta = (meta_block.get("kiro", {}).get("toolName", "") or "").lower()
                title = (update.get("title", "") or "").lower()
                if "todo_list" in tool_name_meta or "todo_list" in title:
                    self._todo_tool_ids.add(update.get("toolCallId", ""))
                    # Push task update immediately from rawInput
                    raw_input = update.get("rawInput")
                    if raw_input and isinstance(raw_input, dict) and raw_input.get("command"):
                        self._push_js("__acpTaskUpdate", raw_input)
                        logger.trace("%stodo_list push (call): %s", self._tab_ctx(), json.dumps(raw_input)[:300])
            # Suppress tool_call_update for skill reads
            if su_type == "tool_call_update":
                if update.get("toolCallId", "") in self._skill_tool_ids:
                    return  # suppress completion event for skill reads
                # Intercept todo_list tool results for task panel
                if update.get("toolCallId", "") in self._todo_tool_ids:
                    # Try multiple possible output field names
                    output = update.get("output") or update.get("result") or update.get("content")
                    # Also check rawInput for the command/args that were sent
                    raw_input = update.get("rawInput")
                    payload = None
                    if output:
                        try:
                            payload = json.loads(output) if isinstance(output, str) else output
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if not payload and raw_input:
                        # Use the input args directly (contains command, tasks, etc.)
                        payload = raw_input if isinstance(raw_input, dict) else None
                    if payload:
                        self._push_js("__acpTaskUpdate", payload)
                        logger.trace("%stodo_list push (result): %s", self._tab_ctx(), json.dumps(payload)[:300])
            self._push_js_throttled("__acpUpdate", update)
        elif method == "_kiro.dev/metadata":
            if self._cancelled.is_set():
                return
            params = msg.get("params", {})
            logger.trace("%smetadata: %s", self._tab_ctx(), json.dumps(params)[:500])
            self._last_metadata = params
        elif method == "_kiro.dev/session/update":
            if self._cancelled.is_set():
                return
            params = msg.get("params", {})
            logger.trace("%ssession_update_dev: %s", self._tab_ctx(), json.dumps(params)[:500])
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
            rid = msg["id"]
            # If cancelled, deny permission to stop the agent from continuing
            if self._cancelled.is_set():
                options = msg.get("params", {}).get("options", [])
                deny = next((o for o in options if "deny" in o.get("kind", "") or "reject" in o.get("kind", "")), None)
                if deny:
                    self._send({"jsonrpc": "2.0", "id": rid,
                        "result": {"outcome": {"outcome": "selected", "optionId": deny["optionId"]}}})
                else:
                    # No explicit deny option — send dismiss/cancel outcome
                    self._send({"jsonrpc": "2.0", "id": rid,
                        "result": {"outcome": {"outcome": "dismissed"}}})
                return
            # Auto-approve
            options = msg.get("params", {}).get("options", [])
            allow = next((o for o in options if "allow" in o.get("kind", "")), options[0] if options else None)
            if allow:
                self._send({"jsonrpc": "2.0", "id": rid,
                    "result": {"outcome": {"outcome": "selected", "optionId": allow["optionId"]}}})
            else:
                self._send({"jsonrpc": "2.0", "id": rid, "result": {"outcome": {"outcome": "selected"}}})

    def _detect_skill_activation(self, update):
        """Check if a tool_call is reading a SKILL.md file. Returns skill name or None."""
        title = update.get("title", "")
        if "SKILL.md" not in title:
            return None
        # Check locations array (contains full file path)
        locations = update.get("locations")
        if locations and isinstance(locations, list):
            for loc in locations:
                p = loc.get("path", "")
                m = _SKILL_MD_PATTERN.search(p)
                if m:
                    return m.group(1)
        # Fallback: extract paths directly from rawInput operations
        raw = update.get("rawInput")
        if raw and isinstance(raw, dict):
            ops = raw.get("operations", [])
            for op in ops:
                if isinstance(op, dict):
                    p = op.get("path", "")
                    if p:
                        m = _SKILL_MD_PATTERN.search(p)
                        if m:
                            return m.group(1)
            # Also check a top-level path field
            p = raw.get("path", "")
            if p:
                m = _SKILL_MD_PATTERN.search(p)
                if m:
                    return m.group(1)
        # Last resort: title says SKILL.md but we can't identify which
        return "_unknown"
    # --- Push to frontend ---

    def _push_js(self, fn_name, data):
        if not self._window:
            return
        # Include tab_id for frontend routing
        if hasattr(self, '_tab_id') and self._tab_id:
            data = {**data, '_tabId': self._tab_id} if isinstance(data, dict) else data
        payload = json.dumps(data).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        try:
            self._window.evaluate_js(
                f"if(window.{fn_name})window.{fn_name}(JSON.parse(`{payload}`))"
            )
            logger.trace("%spush_js OK: %s", self._tab_ctx(), fn_name)
        except Exception as e:
            logger.error("%spush_js FAIL: %s -> %s", self._tab_ctx(), fn_name, e)

    def _push_js_throttled(self, fn_name, data):
        now = time.time()
        if now - self._last_push < 0.016:
            time.sleep(0.016 - (now - self._last_push))
        self._last_push = time.time()
        self._push_js(fn_name, data)

    def _push_state(self):
        # Log state transitions at INFO with tab context. This is the single
        # source of truth for state changes on the wire; keeping it here
        # avoids scattered "state=X" info logs.
        prev = getattr(self, "_last_logged_state", None)
        if self._state != prev:
            logger.info("%sstate: %s -> %s", self._tab_ctx(), prev or "-", self._state)
            self._last_logged_state = self._state
        self._push_js("__acpStateChange", {"state": self._state})

    # --- Session persistence ---

    def _load_prefs(self):
        if PREFS_FILE.exists():
            try:
                return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_prefs(self, prefs):
        PREFS_FILE.write_text(json.dumps(prefs, indent=2), encoding="utf-8")

    def _load_session_id(self):
        return self._load_prefs().get("sessionId")

    def _save_session_id(self, sid):
        prefs = self._load_prefs()
        prefs["sessionId"] = sid
        self._save_prefs(prefs)

    def _clear_session_id(self):
        prefs = self._load_prefs()
        prefs.pop("sessionId", None)
        self._save_prefs(prefs)


# ---------------------------------------------------------------------------
# ACPClientPool — manages multiple ACPClient instances for tabbed UI
# ---------------------------------------------------------------------------

class ACPClientPool:
    """Manages multiple ACPClient instances keyed by tab ID."""

    def __init__(self, max_tabs=5):
        self._clients = {}  # {tab_id: ACPClient}
        self._max_tabs = max_tabs
        self._active_tab = None
        self._window = None
        self._lock = threading.Lock()

    def set_window(self, window):
        self._window = window
        for client in self._clients.values():
            client.set_window(window)

    @property
    def active_client(self):
        """Return the ACPClient for the active tab, or None."""
        return self._clients.get(self._active_tab)

    def get_client(self, tab_id):
        """Return ACPClient for a specific tab."""
        return self._clients.get(tab_id)

    def create_tab(self, tab_id=None):
        """Create a new tab with its own ACPClient. Returns tab_id."""
        with self._lock:
            if len(self._clients) >= self._max_tabs:
                logger.warning("create_tab: max tabs reached (%d)", self._max_tabs)
                if self._window:
                    self._push_js("__acpError", {"error": f"Maximum {self._max_tabs} tabs allowed"})
                return None
            if tab_id is None:
                tab_id = str(uuid.uuid4())[:8]
            client = ACPClient()
            client._tab_id = tab_id
            client.set_window(self._window)
            self._clients[tab_id] = client
            if self._active_tab is None:
                self._active_tab = tab_id
            logger.info("create_tab: tab=%s (total=%d)", tab_id[:6], len(self._clients))
            return tab_id

    def close_tab(self, tab_id):
        """Stop and remove a tab's client."""
        with self._lock:
            client = self._clients.pop(tab_id, None)
            if client:
                client.stop()
            if self._active_tab == tab_id:
                # Switch to another tab or None
                self._active_tab = next(iter(self._clients), None)
            logger.info("close_tab: tab=%s remaining=%d active=%s",
                        str(tab_id)[:6], len(self._clients),
                        str(self._active_tab)[:6] if self._active_tab else "-")
            return self._active_tab

    def switch_tab(self, tab_id):
        """Set the active tab."""
        if tab_id in self._clients:
            prev = self._active_tab
            self._active_tab = tab_id
            if prev != tab_id:
                logger.info("switch_tab: %s -> %s",
                            str(prev)[:6] if prev else "-", str(tab_id)[:6])
            return True
        logger.warning("switch_tab: unknown tab=%s", str(tab_id)[:6])
        return False

    def start_tab(self, tab_id):
        """Start the ACP process for a tab."""
        client = self._clients.get(tab_id)
        if client:
            client.start_process()

    def connect_tab(self, tab_id):
        """Initialize ACP protocol for a tab."""
        client = self._clients.get(tab_id)
        if client:
            client.connect()

    def stop_all(self):
        """Stop all clients."""
        for client in self._clients.values():
            client.stop()
        self._clients.clear()

    def get_tab_states(self):
        """Return {tab_id: state} for all tabs."""
        return {tid: c.state for tid, c in self._clients.items()}

    def _push_js(self, fn_name, data):
        """Push JS to window (pool-level messages)."""
        if not self._window:
            return
        payload = json.dumps(data).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        try:
            self._window.evaluate_js(
                f"if(window.{fn_name})window.{fn_name}(JSON.parse(`{payload}`))"
            )
        except Exception:
            pass

    # --- Tab persistence ---

    def _load_prefs(self):
        if PREFS_FILE.exists():
            try:
                return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_prefs(self, prefs):
        PREFS_FILE.write_text(json.dumps(prefs, indent=2), encoding="utf-8")

    def save_tab_state(self):
        """Persist session titles for sidebar display. Does NOT save tab layout."""
        prefs = self._load_prefs()
        # Save session titles so they appear in the sidebar after restart
        titles = prefs.get("sessionTitles", {})
        for client in self._clients.values():
            if client._session_id and hasattr(client, '_tab_title'):
                titles[client._session_id] = client._tab_title
        prefs["sessionTitles"] = titles
        # Clear any stale tab layout from older versions
        prefs.pop("tabs", None)
        prefs.pop("active_tab", None)
        self._save_prefs(prefs)


# ---------------------------------------------------------------------------
# PyWebView Bridge
# ---------------------------------------------------------------------------

class HyperagentAPI:
    def __init__(self, pool):
        self._pool = pool

    @property
    def _acp(self):
        """Backward compat: return active client."""
        return self._pool.active_client

    def send_prompt(self, text, tab_id=None):
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if client and text and text.strip():
            threading.Thread(target=client.prompt, args=(text.strip(),), daemon=True).start()

    def cancel(self, reason=None, tab_id=None):
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if client:
            client.cancel(reason=reason)

    def new_session(self, tab_id=None):
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if client:
            threading.Thread(target=client.new_session, daemon=True).start()

    def create_tab(self):
        """Create a new tab, spawn its process, connect it."""
        tab_id = self._pool.create_tab()
        if not tab_id:
            return None
        def _start():
            self._pool.start_tab(tab_id)
            self._pool.connect_tab(tab_id)
            self._pool.save_tab_state()
        threading.Thread(target=_start, daemon=True).start()
        return tab_id

    def close_tab(self, tab_id):
        """Close a tab and its process."""
        new_active = self._pool.close_tab(tab_id)
        self._pool.save_tab_state()
        return new_active

    def switch_tab(self, tab_id):
        """Switch to a different tab."""
        result = self._pool.switch_tab(tab_id)
        if result:
            self._pool.save_tab_state()
        return result

    def get_tabs(self):
        """Return list of tabs with their states."""
        states = self._pool.get_tab_states()
        return {
            "tabs": [{"id": tid, "state": st} for tid, st in states.items()],
            "active": self._pool._active_tab
        }

    def open_session_in_tab(self, session_id):
        """Create a new tab and load an existing session into it."""
        logger.info("open_session_in_tab: session=%s", session_id[:8])
        # Guard: don't open a session that's already in another tab
        for tab_id, client in self._pool._clients.items():
            if client._session_id == session_id:
                logger.warning("open_session_in_tab: session %s already in tab=%s", session_id[:8], tab_id[:6])
                self._acp._push_js("__acpError", {
                    "error": "Session already open in another tab"
                })
                return None
        tab_id = self._pool.create_tab()
        if not tab_id:
            return None
        def _start():
            self._pool.start_tab(tab_id)
            # Suppress the automatic ready state from _initialize's on_init callback.
            # We'll manually transition to ready after session/load completes.
            client = self._pool.get_client(tab_id)
            if client:
                client._suppress_init_ready = True
            self._pool.connect_tab(tab_id)
            # Once connected, load the session into this tab's client
            if client:
                client._state = "starting"
                client._push_state()
                history = self.get_session_history(session_id)
                client._push_js("__acpSessionLoaded", {"sessionId": session_id, "messages": history})

                def on_load_result(result):
                    if isinstance(result, dict) and "error" in result:
                        err = result["error"]
                        err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                        logger.error("%sopen_session_in_tab load failed: %s", client._tab_ctx(), result)
                        client._push_js("__acpError", {"error": f"Failed to load session: {err_msg}"})
                    else:
                        client._set_session_id(session_id)
                    client._state = "ready"
                    client._push_state()

                def on_new_done(result):
                    throwaway_id = result.get("sessionId") if isinstance(result, dict) else None
                    if throwaway_id:
                        client._owned_sessions.add(throwaway_id)
                        # Clean up throwaway after load
                        def on_load_and_clean(res):
                            on_load_result(res)
                            if throwaway_id:
                                self._delete_session_files(throwaway_id)
                        client._request("session/load", {
                            "sessionId": session_id,
                            "cwd": str(PORTAL_ROOT).replace("\\", "/"),
                            "mcpServers": []
                        }, on_load_and_clean)
                    else:
                        client._request("session/load", {
                            "sessionId": session_id,
                            "cwd": str(PORTAL_ROOT).replace("\\", "/"),
                            "mcpServers": []
                        }, on_load_result)

                cwd = str(PORTAL_ROOT).replace("\\", "/")
                client._request("session/new", {"cwd": cwd, "mcpServers": []}, on_new_done)
            self._pool.save_tab_state()
        threading.Thread(target=_start, daemon=True).start()
        return tab_id

    def _heuristic_title(self, user_message):
        """Fallback title: verb + noun from the first sentence. Used if AI call fails.

        Best-effort — no NLP. Strips common filler prefixes ("can you", "please",
        etc.), skips leading articles/pronouns, then keeps the first two
        substantive words. Not perfect, but closer to the AI title shape than
        a raw prompt snippet.
        """
        text = user_message.strip()
        for sep in ['\n', '. ', '? ', '! ']:
            if sep in text:
                text = text[:text.index(sep)]
                break
        for prefix in ['can you ', 'could you ', 'please ', 'i want to ', 'i need to ', "let's ", 'help me ', 'i would like to ', 'would you ']:
            if text.lower().startswith(prefix):
                text = text[len(prefix):]
                break
        # Tokenize, drop articles/pronouns/filler that shouldn't lead a title.
        _SKIP = {"a", "an", "the", "to", "i", "we", "you", "just", "also", "then", "and", "but"}
        import re as _re
        tokens = [t for t in _re.findall(r"[A-Za-z0-9']+", text) if t]
        while tokens and tokens[0].lower() in _SKIP:
            tokens = tokens[1:]
        words = tokens[:2] if tokens else []
        if words:
            title = ' '.join(w[:1].upper() + w[1:].lower() for w in words)
        else:
            title = user_message.strip()[:30]
        return title or user_message[:30]

    def _ai_title(self, user_message):
        """Ask kiro-cli for a descriptive 2-5 word title. Returns None on failure."""
        import shutil
        kiro = shutil.which("kiro-cli")
        if not kiro:
            fallback = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "bin" / "kiro-cli.exe"
            kiro = str(fallback) if fallback.exists() else None
        if not kiro:
            logger.warning("_ai_title: kiro-cli not found, using heuristic")
            return None

        # Trim overlong input so the prompt stays cheap
        snippet = user_message.strip()
        if len(snippet) > 500:
            snippet = snippet[:500] + '...'

        prompt = (
            "Summarize the following user request as a two-word title in "
            "'Verb Noun' form (imperative verb + object noun). "
            "Examples: 'Fix Migration', 'Review PR', 'Refactor Sidebar', "
            "'Debug Deploy', 'Add Endpoint'. "
            "Rules: exactly 2 words, title case, no punctuation, no quotes, "
            "no trailing period, no articles (a/an/the). "
            "Reply with ONLY the title text — no preamble, no explanation.\n\n"
            "Request: " + snippet
        )

        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            # Force UTF-8 decoding — Windows default codepage mangles kiro-cli's
            # unicode footer glyphs (▸ •) into mojibake that leaks into titles.
            result = subprocess.run(
                [kiro, "chat", "--no-interactive", prompt],
                capture_output=True, text=True, timeout=60,
                encoding="utf-8", errors="replace",
                startupinfo=si,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            output = (result.stdout or "") + "\n" + (result.stderr or "")
            # Strip ANSI escape codes
            import re as _re
            clean = _re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
            clean = _re.sub(r'\x1b\[\?[0-9;]*[a-zA-Z]', '', clean)

            # Lines to reject: kiro-cli metering footer, prompt markers, spinners,
            # anything that clearly isn't the model's title reply.
            _METER_MARKERS = (
                'credits:', 'tokens:', 'time:', 'cost:', 'usage:', 'plan:',
                'thinking', 'trust-all-tools', 'session id', 'model:',
            )
            _PREFIX_REJECT = ('>', '▸', '›', '[', '#', '$', '•', '·', '|')

            def _looks_like_title(line):
                s = line.strip()
                if not s:
                    return False
                if s.startswith(_PREFIX_REJECT):
                    return False
                low = s.lower()
                if any(m in low for m in _METER_MARKERS):
                    return False
                # Reject lines that are clearly log/status noise
                if low.startswith(('info ', 'debug ', 'warn ', 'error ', '[info', '[debug', '[warn', '[error')):
                    return False
                return True

            lines = [ln for ln in clean.splitlines() if ln.strip()]
            # Walk from the bottom up — the model's final reply is the last
            # substantive line before the metering footer. Reverse-iterate and
            # take the first line that survives the reject filter.
            candidate = None
            for ln in reversed(lines):
                if _looks_like_title(ln):
                    candidate = ln.strip()
                    break
            if not candidate:
                logger.warning("_ai_title: no candidate line found in output: %r", clean[:400])
                return None

            title = candidate.strip('`"\'*_ ').strip()
            title = title.rstrip('.!?,;:')
            words = title.split()
            if not words:
                return None
            # Enforce verb+noun form: exactly 2 words. If the model returned
            # more, keep the first two (usually verb + primary noun).
            if len(words) > 2:
                words = words[:2]
            # Drop leading articles if the model slipped one in ("The Bug").
            _ARTICLES = {"a", "an", "the"}
            if len(words) == 2 and words[0].lower() in _ARTICLES:
                words = words[1:]
            title = ' '.join(w[:1].upper() + w[1:] for w in words if w)
            if len(title) > 40:
                title = title[:40].rstrip()
            low = title.lower()
            if any(bad in low for bad in ("error", "sorry", "cannot", "unable", "logged out")):
                logger.warning("_ai_title: rejected suspicious response: %r", title)
                return None
            logger.info("_ai_title: generated %r", title)
            return title or None
        except subprocess.TimeoutExpired:
            logger.warning("_ai_title: kiro-cli timeout")
            return None
        except Exception as e:
            logger.error("_ai_title error: %s", e)
            return None

    def generate_title(self, user_message, tab_id=None):
        """Generate a short session title from the user's first message.

        Routes to the ACPClient owning `tab_id` so the title lands on the tab
        that actually sent the prompt — not whichever tab happens to be active
        by the time this async work completes (bug: user switches sessions
        between send and turn-end and the wrong tab gets renamed).
        """
        # Resolve the target client up front, before any thread hop, so it
        # can't drift as the user switches tabs.
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if client is None:
            logger.warning("generate_title: no client for tab_id=%s", tab_id)
            return

        def _run():
            try:
                # Prefer an AI-generated 2-5 word title; fall back to heuristic
                # if kiro-cli is unavailable, times out, or returns junk.
                title = self._ai_title(user_message) or self._heuristic_title(user_message)

                client._push_js("__acpSessionTitle", {"title": title})
                # Persist title for sidebar and tab
                client._tab_title = title  # For tab persistence
                if client._session_id:
                    prefs = client._load_prefs()
                    titles = prefs.get("sessionTitles", {})
                    titles[client._session_id] = title
                    prefs["sessionTitles"] = titles
                    client._save_prefs(prefs)
                self._pool.save_tab_state()
            except Exception as e:
                logger.error(f"generate_title error: {e}")
                fallback = self._heuristic_title(user_message)
                client._push_js("__acpSessionTitle", {"title": fallback})
        threading.Thread(target=_run, daemon=True).start()

    def reconnect(self, tab_id=None):
        """Reconnect a specific tab (or active tab if no tab_id given)."""
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if client:
            threading.Thread(target=client.start, daemon=True).start()

    def get_state(self, tab_id=None):
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        return client.state if client else "stopped"

    def toggle_fullscreen(self):
        if self._acp._window:
            self._acp._window.toggle_fullscreen()

    def debug_log(self, message):
        """Route a JS-side trace into the hyperagent log."""
        try:
            logger.info("JS: %s", message)
        except Exception:
            pass
        return True

    def copy_to_clipboard(self, text):
        """Write text to the system clipboard via Windows clip.exe.

        clip.exe expects UTF-16LE on stdin. The previous PowerShell-based
        implementation relied on the $input automatic variable, which is
        only populated inside a real PowerShell pipeline — when invoked as
        a subprocess with stdin=PIPE, $input is empty and the clipboard is
        silently cleared. clip.exe is a straight stdin reader with no
        pipeline semantics, so it Just Works.
        """
        logger.debug("copy_to_clipboard: invoked (%d chars)", len(text) if text else 0)
        try:
            import subprocess
            process = subprocess.Popen(
                ["clip"],
                stdin=subprocess.PIPE,
            )
            process.communicate(input=text.encode("utf-16le"))
            if process.returncode != 0:
                logger.warning("clipboard write failed: clip.exe exited %d", process.returncode)
                return False
            logger.debug("clipboard write: %d chars", len(text))
            return True
        except Exception as e:
            logger.warning("clipboard write failed: %s", e)
            return False

    def get_plan_usage(self):
        """Run kiro-cli /usage command and parse plan credits percentage."""
        import shutil
        kiro = shutil.which("kiro-cli")
        if not kiro:
            fallback = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "bin" / "kiro-cli.exe"
            kiro = str(fallback) if fallback.exists() else None
        if not kiro:
            return {"ok": False, "error": "kiro-cli not found"}

        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            result = subprocess.run(
                [kiro, "chat", "--no-interactive", "/usage"],
                capture_output=True, text=True, timeout=20,
                startupinfo=si,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            # Output may be on stdout or stderr
            output = (result.stdout or "") + (result.stderr or "")
            # Strip ANSI escape codes
            import re as _re
            clean = _re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
            clean = _re.sub(r'\x1b\[\?[0-9;]*[a-zA-Z]', '', clean)

            logger.debug(f"plan_usage raw: {repr(clean[:300])}")

            # Format 1: "Credits (USED of TOTAL covered in plan)"
            credits_match = _re.search(r'\((\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+covered', clean)
            if credits_match:
                used = float(credits_match.group(1))
                total = float(credits_match.group(2))
                used_pct = int((used / total) * 100) if total > 0 else 0
                # Extract reset date
                reset_match = _re.search(r'resets?\s+on\s+([\d-]+)', clean)
                reset_str = reset_match.group(1) if reset_match else ""
                # Detect overage: "covered in plan" with used==total means at/over limit
                at_cap = (used >= total) and ("covered in plan" in clean)
                detail = f"{used:.1f} / {total:.0f} credits"
                if reset_str:
                    detail += f" | resets {reset_str}"
                if at_cap:
                    detail += " | at or over plan limit"
                used_str = f"{used:.1f}" if used != int(used) else str(int(used))
                if at_cap:
                    used_str += "+"
                total_str = str(int(total))
                return {"ok": True, "used_pct": used_pct, "used": used_str, "total": total_str, "detail": detail, "at_cap": at_cap}

            # Format 2: "███...██ XX% (resets on MM/DD)"
            pct_match = _re.search(r'(\d+)%', clean)
            if pct_match:
                used_pct = int(pct_match.group(1))
                detail = clean.strip().replace('\n', ' | ')[:200]
                return {"ok": True, "used_pct": used_pct, "detail": detail}

            return {"ok": False, "error": "Could not parse usage output"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # --- Gradient map presets (mirrors theme.js GRADIENT_MAPS) ---
    GRADIENT_MAPS = {
        "phosphor": {"accent": "#00ff41", "warm": "#ffb000", "cool": "#00cccc", "comp": "#ff3333",
                     "semantics": {"success": "#00ff41", "warning": "#ffb000", "error": "#ff3333", "info": "#00cccc"}},
        "blade-runner": {"accent": "#ff6ac1", "warm": "#ffd700", "cool": "#7dcfff", "comp": "#ff2e63",
                         "semantics": {"success": "#5af78e", "warning": "#ffd700", "error": "#ff2e63", "info": "#7dcfff"}},
        "ocean-depth": {"accent": "#00bfff", "warm": "#f0a500", "cool": "#4de0d0", "comp": "#ff6b6b",
                        "semantics": {"success": "#4de0d0", "warning": "#f0a500", "error": "#ff6b6b", "info": "#00bfff"}},
        "amber-terminal": {"accent": "#ffb000", "warm": "#ff6b35", "cool": "#ffe066", "comp": "#ff4444",
                           "semantics": {"success": "#7ddb57", "warning": "#ffe066", "error": "#ff4444", "info": "#66ccff"}},
        "ultraviolet": {"accent": "#b266ff", "warm": "#ff66b2", "cool": "#66ccff", "comp": "#ff4466",
                        "semantics": {"success": "#66ffb2", "warning": "#ffcc66", "error": "#ff4466", "info": "#66ccff"}},
        "solar-flare": {"accent": "#ff6600", "warm": "#ff3366", "cool": "#ffcc00", "comp": "#ff0044",
                        "semantics": {"success": "#66ff66", "warning": "#ffcc00", "error": "#ff0044", "info": "#33ccff"}},
        "arctic": {"accent": "#66ffee", "warm": "#aaddff", "cool": "#88ffcc", "comp": "#ff6688",
                   "semantics": {"success": "#88ffcc", "warning": "#ffdd66", "error": "#ff6688", "info": "#66ffee"}},
        "neon-noir": {"accent": "#39ff14", "warm": "#ff073a", "cool": "#00fff7", "comp": "#ff00ff",
                      "semantics": {"success": "#39ff14", "warning": "#ffdd00", "error": "#ff073a", "info": "#00fff7"}},
        "rust": {"accent": "#e65c00", "warm": "#e04000", "cool": "#ff9933", "comp": "#ff2200",
                 "semantics": {"success": "#66cc66", "warning": "#ff9933", "error": "#ff2200", "info": "#5599cc"}},
        "synthwave": {"accent": "#f72585", "warm": "#b44aff", "cool": "#4cc9f0", "comp": "#ff006e",
                      "semantics": {"success": "#72efdd", "warning": "#ffc300", "error": "#ff006e", "info": "#4cc9f0"}},
        "dracula": {"accent": "#bd93f9", "warm": "#ff79c6", "cool": "#8be9fd", "comp": "#ff5555",
                    "semantics": {"success": "#50fa7b", "warning": "#f1fa8c", "error": "#ff5555", "info": "#8be9fd"}},
        "monokai": {"accent": "#a6e22e", "warm": "#fd971f", "cool": "#66d9ef", "comp": "#f92672",
                    "semantics": {"success": "#a6e22e", "warning": "#e6db74", "error": "#f92672", "info": "#66d9ef"}},
        "gruvbox": {"accent": "#fabd2f", "warm": "#fe8019", "cool": "#83a598", "comp": "#fb4934",
                    "semantics": {"success": "#b8bb26", "warning": "#fabd2f", "error": "#fb4934", "info": "#83a598"}},
        "catppuccin": {"accent": "#cba6f7", "warm": "#f9e2af", "cool": "#89dceb", "comp": "#f38ba8",
                       "semantics": {"success": "#a6e3a1", "warning": "#f9e2af", "error": "#f38ba8", "info": "#89dceb"}},
        "nord": {"accent": "#88c0d0", "warm": "#ebcb8b", "cool": "#a3be8c", "comp": "#bf616a",
                 "semantics": {"success": "#a3be8c", "warning": "#ebcb8b", "error": "#bf616a", "info": "#88c0d0"}},
        "tokyo-night": {"accent": "#7aa2f7", "warm": "#e0af68", "cool": "#73daca", "comp": "#f7768e",
                        "semantics": {"success": "#9ece6a", "warning": "#e0af68", "error": "#f7768e", "info": "#7aa2f7"}},
        "cyberdeck": {"accent": "#00ff9f", "warm": "#ffe600", "cool": "#00e5ff", "comp": "#ff003c",
                      "semantics": {"success": "#00ff9f", "warning": "#ffe600", "error": "#ff003c", "info": "#00e5ff"}},
        "vaporwave": {"accent": "#ff71ce", "warm": "#b967ff", "cool": "#01cdfe", "comp": "#ff71ce",
                      "semantics": {"success": "#05ffa1", "warning": "#fffb96", "error": "#ff71ce", "info": "#01cdfe"}},
    }

    def get_accent(self):
        """Read theme from hypervisor's preferences.json and return full palette."""
        prefs_file = HYPERVISOR_DIR / "preferences.json"
        try:
            data = json.loads(prefs_file.read_text(encoding="utf-8"))
            theme_mode = data.get("hypervisor-theme-mode", "custom")
            gradient_map = data.get("hypervisor-gradient-map", "")

            if theme_mode == "preset" and gradient_map:
                # Look up in built-in gradient maps
                preset = self.GRADIENT_MAPS.get(gradient_map)
                if not preset:
                    # Check user gradient maps
                    preset = data.get("userGradientMaps", {}).get(gradient_map)
                if preset:
                    return {
                        "accent": preset["accent"],
                        "warm": preset["warm"],
                        "cool": preset["cool"],
                        "comp": preset["comp"],
                        "semantics": preset.get("semantics"),
                        "mode": "preset",
                        "gradientMap": gradient_map,
                    }

            # Custom mode: derive via OKLCH
            accent = data.get("hypervisor-accent", "#00ff41")
            mode = data.get("hypervisor-palette-mode", "split")
        except Exception:
            accent, mode = "#00ff41", "split"
        palette = self._build_palette_oklch(accent, mode)
        palette["mode"] = "custom"
        return palette

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

    @staticmethod
    def _build_palette_oklch(hex_color, mode):
        """Derive warm/cool/comp using OKLCH color space (perceptually uniform).
        Parallel to _build_palette() — not yet wired as default."""
        import math

        # --- Conversion utilities ---
        def multiply_matrix3(m, v):
            return [
                m[0]*v[0] + m[1]*v[1] + m[2]*v[2],
                m[3]*v[0] + m[4]*v[1] + m[5]*v[2],
                m[6]*v[0] + m[7]*v[1] + m[8]*v[2],
            ]

        def srgb_to_linear(c):
            if abs(c) <= 0.04045:
                return c / 12.92
            return (-1 if c < 0 else 1) * (((abs(c) + 0.055) / 1.055) ** 2.4)

        def linear_to_srgb(c):
            if abs(c) > 0.0031308:
                return (-1 if c < 0 else 1) * (1.055 * (abs(c) ** (1 / 2.4)) - 0.055)
            return 12.92 * c

        M_SRGB_TO_XYZ = [
            0.41239079926595934, 0.357584339383878,   0.1804807884018343,
            0.21263900587151027, 0.715168678767756,   0.07219231536073371,
            0.01933081871559182, 0.11919477979462598, 0.9505321522496607,
        ]
        M_XYZ_TO_SRGB = [
             3.2409699419045226,  -1.537383177570094,   -0.4986107602930034,
            -0.9692436362808796,   1.8759675015077202,   0.04155505740717559,
             0.05563007969699366, -0.20397695888897652,  1.0569715142428786,
        ]
        M_XYZ_TO_LMS = [
            0.8190224379967030, 0.3619062600528904, -0.1288737815209879,
            0.0329836539323885, 0.9292868615863434,  0.0361446663506424,
            0.0481771893596242, 0.2642395317527308,  0.6335478284694309,
        ]
        M_LMS_TO_OKLAB = [
            0.2104542683093140,  0.7936177747023054, -0.0040720430116193,
            1.9779985324311684, -2.4285922420485799,  0.4505937096174110,
            0.0259040424655478,  0.7827717124575296, -0.8086757549230774,
        ]
        M_OKLAB_TO_LMS = [
            1,  0.3963377773761749,  0.2158037573099136,
            1, -0.1055613458156586, -0.0638541728258133,
            1, -0.0894841775298119, -1.2914855480194092,
        ]
        M_LMS_TO_XYZ = [
             1.2268798758459243, -0.5578149944602171,  0.2813910456659647,
            -0.0405757452148008,  1.1122868032803170, -0.0717110580655164,
            -0.0763729366746601, -0.4214933324022432,  1.5869240198367816,
        ]

        def hex_to_oklch(hex_str):
            r = int(hex_str[1:3], 16) / 255
            g = int(hex_str[3:5], 16) / 255
            b = int(hex_str[5:7], 16) / 255
            lin = [srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b)]
            xyz = multiply_matrix3(M_SRGB_TO_XYZ, lin)
            lms = multiply_matrix3(M_XYZ_TO_LMS, xyz)
            lms_cbrt = [math.copysign(abs(x) ** (1/3), x) if x != 0 else 0 for x in lms]
            lab = multiply_matrix3(M_LMS_TO_OKLAB, lms_cbrt)
            L = lab[0]
            a, b_val = lab[1], lab[2]
            C = math.sqrt(a*a + b_val*b_val)
            H = 0 if (abs(a) < 0.0002 and abs(b_val) < 0.0002) else (math.degrees(math.atan2(b_val, a)) % 360)
            return (L, C, H)

        def oklch_to_srgb(l, c, h):
            h_rad = math.radians(h)
            a = c * math.cos(h_rad)
            b_val = c * math.sin(h_rad)
            lms_cbrt = multiply_matrix3(M_OKLAB_TO_LMS, [l, a, b_val])
            lms = [x*x*x for x in lms_cbrt]
            xyz = multiply_matrix3(M_LMS_TO_XYZ, lms)
            lin_rgb = multiply_matrix3(M_XYZ_TO_SRGB, xyz)
            return [linear_to_srgb(lin_rgb[0]), linear_to_srgb(lin_rgb[1]), linear_to_srgb(lin_rgb[2])]

        def in_gamut(rgb):
            return all(-0.001 <= ch <= 1.001 for ch in rgb)

        def oklch_to_hex(l, c, h):
            # Binary search chroma reduction for gamut clamping
            rgb = oklch_to_srgb(l, c, h)
            if not in_gamut(rgb):
                lo, hi = 0.0, c
                for _ in range(20):
                    mid = (lo + hi) / 2
                    rgb = oklch_to_srgb(l, mid, h)
                    if in_gamut(rgb):
                        lo = mid
                    else:
                        hi = mid
                rgb = oklch_to_srgb(l, lo, h)
            rgb = [max(0, min(1, ch)) for ch in rgb]
            return "#{:02x}{:02x}{:02x}".format(
                round(rgb[0] * 255), round(rgb[1] * 255), round(rgb[2] * 255))

        # --- Palette derivation ---
        L, C, H = hex_to_oklch(hex_color)
        L = max(L, 0.55)   # legibility floor
        # No chroma cap — gamut clamping handles out-of-gamut naturally

        if mode == "triadic":
            warm = oklch_to_hex(min(L * 0.9, 0.8), C, (H + 120) % 360)
            cool = oklch_to_hex(L * 0.8, C * 0.95, (H + 240) % 360)
            comp = oklch_to_hex(L * 0.7, C * 0.85, (H + 180) % 360)
        elif mode == "analogous":
            warm = oklch_to_hex(min(L * 0.95, 0.8), C, (H + 30) % 360)
            cool = oklch_to_hex(L * 0.85, C * 0.95, (H + 60) % 360)
            comp = oklch_to_hex(L * 0.75, C * 0.9, (H + 330) % 360)
        elif mode == "square":
            warm = oklch_to_hex(min(L * 0.9, 0.8), C, (H + 90) % 360)
            cool = oklch_to_hex(L * 0.8, C * 0.95, (H + 180) % 360)
            comp = oklch_to_hex(L * 0.7, C * 0.85, (H + 270) % 360)
        elif mode == "complement":
            warm = oklch_to_hex(min(L * 0.9, 0.8), C, (H + 180) % 360)
            cool = oklch_to_hex(L * 0.7, C * 0.85, (H + 180) % 360)
            comp = oklch_to_hex(L * 0.6, C * 0.6, H)
        else:  # split
            warm = oklch_to_hex(min(L * 0.9, 0.8), C, (H + 150) % 360)
            cool = oklch_to_hex(L * 0.8, C * 0.95, (H + 210) % 360)
            comp = oklch_to_hex(L * 0.7, C * 0.85, (H + 180) % 360)

        return {"accent": hex_color, "warm": warm, "cool": cool, "comp": comp}

    def get_steering(self):
        """Scan .kiro/steering/ and return list of files with their inclusion mode."""
        steering_dir = PORTAL_ROOT / ".kiro" / "steering"
        if not steering_dir.exists():
            return []
        files = []
        for f in sorted(steering_dir.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")[:500]
                inclusion = "manual"
                if text.startswith("---"):
                    end = text.index("---", 3)
                    front = text[3:end]
                    for line in front.strip().splitlines():
                        if line.startswith("inclusion:"):
                            inclusion = line.split(":", 1)[1].strip()
                files.append({"name": f.stem, "inclusion": inclusion})
            except Exception:
                continue
        return files

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
            # Don't mark sessions owned by ANY of our tabs as locked
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
        """Get PIDs of all kiro-cli processes owned by any tab in our pool."""
        pids = set()
        try:
            sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
            # Collect session IDs from ALL pool clients
            sessions_to_check = set()
            for client in self._pool._clients.values():
                sessions_to_check.update(client._owned_sessions)
                if client._session_id:
                    sessions_to_check.add(client._session_id)
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
            # Override with AI-generated titles
            saved_titles = self._acp._load_prefs().get("sessionTitles", {})
            for s in sessions:
                if s["id"] in saved_titles:
                    s["title"] = saved_titles[s["id"]]
            return {"sessions": sessions, "active": self._acp._session_id}
        except Exception as e:
            logger.error(f"list_sessions error: {e}")
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

    def rename_session(self, session_id, new_title):
        """Rename a session by updating the custom title in preferences.
        Also updates the tab title if the session belongs to a tab."""
        if not new_title:
            return False
        new_title = new_title.strip()[:60]
        try:
            # If no session_id given, use active tab's session
            if not session_id:
                client = self._pool.active_client
                session_id = client._session_id if client else None
            if not session_id:
                return False
            prefs = self._pool._load_prefs()
            titles = prefs.get("sessionTitles", {})
            titles[session_id] = new_title
            prefs["sessionTitles"] = titles
            self._pool._save_prefs(prefs)
            # Update tab title for any tab holding this session
            for tab_id, client in self._pool._clients.items():
                if client._session_id == session_id:
                    client._tab_title = new_title
                    client._push_js("__acpSessionTitle", {"title": new_title})
            self._pool.save_tab_state()
            return True
        except Exception as e:
            logger.error(f"rename_session error: {e}")
            return False

    def load_session(self, session_id):
        """Load an existing session by ID."""
        logger.info("load_session: session=%s (into active tab=%s)",
                    session_id[:8], str(self._pool._active_tab)[:6] if self._pool._active_tab else "-")
        # Guard: don't load a session that's already open in another tab
        for tab_id, client in self._pool._clients.items():
            if tab_id != self._pool._active_tab and client._session_id == session_id:
                logger.warning("load_session: session %s already in tab=%s", session_id[:8], tab_id[:6])
                self._acp._push_js("__acpError", {
                    "error": "Session already open in another tab"
                })
                return
        threading.Thread(
            target=self._load_session_async, args=(session_id,), daemon=True
        ).start()

    def delete_session(self, session_id):
        """Delete a session by removing its files directly.

        The kiro-cli subprocess approach doesn't work because the running ACP
        process holds an advisory lock on the session store, causing the
        separate kiro-cli delete command to silently fail.
        """
        if session_id == self._acp._session_id:
            return False  # Don't delete the active session
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
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    kind = entry.get("kind")
                    data = entry.get("data", {})
                    content = data.get("content", [])
                    # Extract timestamp (unix seconds) from entry meta if present
                    ts_meta = entry.get("meta") or data.get("meta") or {}
                    ts = ts_meta.get("timestamp") if isinstance(ts_meta, dict) else None
                    if kind == "Prompt":
                        for c in content:
                            if c.get("kind") == "text" and c.get("data"):
                                messages.append({"role": "user", "text": c["data"], "ts": ts})
                                break
                    elif kind == "AssistantMessage":
                        # Extract text content
                        text_parts = []
                        tools = []
                        for c in content:
                            if c.get("kind") == "text" and c.get("data"):
                                text_parts.append(c["data"])
                            elif c.get("kind") == "toolUse":
                                td = c.get("data", {})
                                tool_name = td.get("name", "unknown")
                                tool_input = td.get("input", {})
                                # Detect skill activation: read targeting a SKILL.md
                                skill_match = None
                                if tool_name == "read":
                                    # Extract paths from operations array
                                    ops = tool_input.get("operations", [])
                                    for op in ops:
                                        p = op.get("path", "")
                                        if p:
                                            skill_match = _SKILL_MD_PATTERN.search(p)
                                            if skill_match:
                                                break
                                    # Fallback: check raw path field
                                    if not skill_match:
                                        p = tool_input.get("path", "")
                                        if p:
                                            skill_match = _SKILL_MD_PATTERN.search(p)
                                if skill_match:
                                    skill_key = skill_match.group(1)
                                    meta = _SKILL_CACHE.get(skill_key, {"name": skill_key, "description": ""})
                                    tools.append({
                                        "role": "skill",
                                        "name": meta.get("name", skill_key),
                                        "description": meta.get("description", ""),
                                    })
                                else:
                                    tools.append({
                                        "role": "tool",
                                        "name": tool_name,
                                        "toolUseId": td.get("toolUseId", ""),
                                    })
                        # Emit text first (if any non-empty), then tool calls
                        combined_text = "".join(text_parts).strip()
                        if combined_text:
                            messages.append({"role": "agent", "text": combined_text, "ts": ts})
                        for t in tools:
                            messages.append(t)
                    elif kind == "ToolResults":
                        # Mark tool results (not rendered, but could enrich tool cards)
                        pass
            return messages
        except Exception as e:
            logger.error(f"get_session_history error: {e}")
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
                err = result["error"]
                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                logger.error(f"sidebar load failed: {result}")
                self._acp._push_js("__acpError", {"error": f"Failed to load session: {err_msg}", "source": "jsonrpc"})
            else:
                self._acp._session_id = session_id
                self._acp._save_session_id(session_id)
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


def _cleanup_stale_locks(sessions_dir):
    """Remove lock files whose owning kiro-cli process is orphaned (parent bridge dead).

    After a force-close, kiro-cli survives as an orphan (CREATE_NEW_PROCESS_GROUP).
    We detect this by checking if the locking process's parent is still alive.
    If not, we terminate the orphan and remove the lock.
    """
    if not sessions_dir or not sessions_dir.exists():
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_TERMINATE = 0x0001
    cleaned = 0

    # Get our own PID to avoid killing our own future kiro-cli processes
    my_pid = os.getpid()

    for lock_file in sessions_dir.glob("*.lock"):
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            if pid == 0:
                lock_file.unlink(missing_ok=True)
                cleaned += 1
                continue

            # Check if process is alive
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                # Process is dead — stale lock
                lock_file.unlink(missing_ok=True)
                cleaned += 1
                logger.info("startup: removed stale lock %s (pid %d dead)", lock_file.name, pid)
                continue
            kernel32.CloseHandle(handle)

            # Process is alive — check if it's orphaned (parent is dead)
            ppid = _get_parent_pid(pid)
            if ppid is not None and ppid != my_pid:
                # Check if parent is alive
                parent_handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, ppid)
                if parent_handle:
                    kernel32.CloseHandle(parent_handle)
                    # Parent is alive — this session belongs to another running instance
                    continue
                else:
                    # Parent is dead — orphaned kiro-cli, terminate it
                    term_handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                    if term_handle:
                        kernel32.TerminateProcess(term_handle, 1)
                        kernel32.CloseHandle(term_handle)
                        logger.info("startup: terminated orphaned kiro-cli pid %d (parent %d dead)", pid, ppid)
                    lock_file.unlink(missing_ok=True)
                    cleaned += 1
        except Exception:
            # If we can't read/parse the lock file, remove it
            try:
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

    # Clear stale tab/session associations from preferences (prevents "IN USE" ghost state)
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

    # Always start fresh with a single tab (restoring multi-tab state is unreliable)
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
        # Connect the initial tab
        pool.connect_tab(initial_tab)
        # Start theme file watcher
        _start_theme_watcher(window, api)

    webview.start(on_start, icon=icon_path, debug=False)
    # Save tab state before exiting
    pool.save_tab_state()
    pool.stop_all()


if __name__ == "__main__":
    main()
