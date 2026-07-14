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
SKILLS_DIR = PORTAL_ROOT / ".kiro" / "skills"
PREFS_FILE = HYPERAGENT_DIR / "preferences.json"
ICON_FILE = HYPERVISOR_DIR / "assets" / "ha-box.ico"

# ---------------------------------------------------------------------------
# Structured logging (shared ecosystem logger)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(HYPERSPACE_ROOT))
from hyper_logging import setup_logger  # noqa: E402

logger = setup_logger("hyperagent")


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
            logger.warning("start_process: not authenticated, triggering visible login")
            if self._window:
                self._push_js("__acpAuthRequired", {"url": None})
            success = _do_login_visible()
            if not success or not _check_auth():
                logger.error("start_process: login failed")
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
        logger.info(f"start_process: listening on port {port}")

        bridge = str(HYPERAGENT_DIR / "acp_bridge.py")
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        self._process = subprocess.Popen(
            [sys.executable, bridge, str(port)],
            startupinfo=si,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        logger.info(f"start_process: bridge pid={self._process.pid}")

        # Accept connection from bridge
        self._server_sock.settimeout(10)
        try:
            self._socket, _ = self._server_sock.accept()
            self._sockfile = self._socket.makefile("rwb")
            logger.info("start_process: bridge connected")
        except socket.timeout:
            logger.error("start_process: bridge connection timeout")
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
            logger.debug(f"sent: id={msg.get('id')} method={msg.get('method','')}")
        except (BrokenPipeError, OSError) as e:
            logger.error(f"send error: {e}")

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
            logger.debug(f"on_init called: {str(result)[:100]}")
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
        logger.info(f"_on_session: {result}")
        if isinstance(result, dict) and "sessionId" in result:
            self._session_id = result["sessionId"]
            self._owned_sessions.add(self._session_id)
            self._save_session_id(self._session_id)
            self._state = "ready"
        elif isinstance(result, dict) and "error" in result:
            err = result["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            logger.warning(f"_on_session error, creating new: {err_msg}")
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
        # Lazy session creation: if no session exists yet, create one then prompt
        if not self._session_id:
            self._state = "prompting"
            self._prompt_start = time.time()
            self._push_state()
            def on_lazy_session(result):
                # Extract session ID without pushing "ready" state to frontend
                logger.debug(f"on_lazy_session: {result}")
                if isinstance(result, dict) and "sessionId" in result:
                    self._session_id = result["sessionId"]
                    self._owned_sessions.add(self._session_id)
                    self._save_session_id(self._session_id)
                elif isinstance(result, dict) and "error" in result:
                    err = result["error"]
                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    self._state = "ready"
                    self._push_state()
                    self._push_js("__acpError", {"error": f"Session creation failed: {err_msg}", "source": "jsonrpc"})
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
        logger.debug(f"prompt_done: {json.dumps(result)[:500]}")
        self._state = "ready"
        data = result or {}
        data["_elapsed"] = elapsed
        # Surface error details to frontend
        if isinstance(result, dict) and "error" in result:
            err = result["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            self._push_js("__acpError", {"error": f"Prompt failed: {err_msg}", "source": "jsonrpc"})
        if hasattr(self, '_last_metadata') and self._last_metadata:
            data["_metadata"] = self._last_metadata
            self._last_metadata = None
        self._skill_tool_ids.clear()
        self._todo_tool_ids.clear()
        self._push_js("__acpTurnEnd", data)
        self._push_state()

    def cancel(self, reason=None):
        reason = reason or "user"
        logger.info(f"cancel() called: reason={reason} state={self._state} session={self._session_id}")
        if self._state == "prompting" and self._session_id:
            self._cancelled.set()
            # Remove the pending callback for the active prompt so
            # the stale response from kiro-cli is silently dropped
            prompt_id = self._active_prompt_id
            if prompt_id is not None:
                self._pending.pop(prompt_id, None)
                logger.info(f"cancel: popped pending id={prompt_id}")
            self._active_prompt_id = None
            # Send both cancellation mechanisms:
            # 1. session/cancel (ACP notification — must NOT have an id)
            self._notify("session/cancel", {"sessionId": self._session_id})
            logger.info("cancel: sent session/cancel (notification)")
            # 2. $/cancel_request (ACP generic per-request cancellation, targets the prompt)
            if prompt_id is not None:
                self._notify("$/cancel_request", {"requestId": prompt_id})
                logger.info(f"cancel: sent $/cancel_request for id={prompt_id}")
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
            logger.info("cancel: pushed state=ready to frontend")
        else:
            logger.warning(f"cancel: SKIPPED (state={self._state}, session={self._session_id})")

    def new_session(self):
        if self._state not in ("ready",):
            return
        self._session_id = None
        self._clear_session_id()
        self._todo_tool_ids.clear()
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
                        logger.debug(f"recv: id={msg.get('id')} method={msg.get('method','')}")
                        self._dispatch(msg)
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error: {e}")
        except Exception as e:
            logger.error(f"reader exception: {e}")
        logger.info(f"reader exited, state={self._state}")
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
            logger.info(f"kiro-cli stderr: {text}")
            self._push_js("__acpError", {"error": text, "source": "stderr"})
            return
        if method == "session/update":
            # Suppress all session updates after cancel until next prompt
            if self._cancelled.is_set():
                logger.warning("session/update SUPPRESSED (cancelled)")
                return
            update = msg.get("params", {}).get("update", {})
            su_type = update.get("sessionUpdate", "unknown")
            if su_type != "agent_message_chunk":
                logger.debug(f"session_update: type={su_type} id={update.get('toolCallId','')[:20]} title={update.get('title','')}")
            # Detect skill activation: a tool_call reading a SKILL.md file
            if su_type == "tool_call":
                logger.debug(f"tool_call_full: {json.dumps(update)[:800]}")
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
                    logger.debug(f"todo_list tracked: {update.get('toolCallId', '')}")
                    # Push task update immediately from rawInput
                    raw_input = update.get("rawInput")
                    if raw_input and isinstance(raw_input, dict) and raw_input.get("command"):
                        self._push_js("__acpTaskUpdate", raw_input)
                        logger.debug(f"todo_list pushed: {json.dumps(raw_input)[:300]}")
            # Suppress tool_call_update for skill reads
            if su_type == "tool_call_update":
                if update.get("toolCallId", "") in self._skill_tool_ids:
                    return  # suppress completion event for skill reads
                # Intercept todo_list tool results for task panel
                if update.get("toolCallId", "") in self._todo_tool_ids:
                    logger.debug(f"todo_list result: {json.dumps(update)[:600]}")
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
                        logger.debug(f"todo_list pushed to frontend: {json.dumps(payload)[:300]}")
            self._push_js_throttled("__acpUpdate", update)
        elif method == "_kiro.dev/metadata":
            if self._cancelled.is_set():
                return
            params = msg.get("params", {})
            logger.debug(f"metadata: {json.dumps(params)[:500]}")
            self._last_metadata = params
        elif method == "_kiro.dev/session/update":
            if self._cancelled.is_set():
                return
            params = msg.get("params", {})
            logger.debug(f"session_update_dev: {json.dumps(params)[:500]}")
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
        payload = json.dumps(data).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        try:
            self._window.evaluate_js(
                f"if(window.{fn_name})window.{fn_name}(JSON.parse(`{payload}`))"
            )
            logger.debug(f"push_js OK: {fn_name}")
        except Exception as e:
            logger.error(f"push_js FAIL: {fn_name} -> {e}")

    def _push_js_throttled(self, fn_name, data):
        now = time.time()
        if now - self._last_push < 0.016:
            time.sleep(0.016 - (now - self._last_push))
        self._last_push = time.time()
        self._push_js(fn_name, data)

    def _push_state(self):
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
# PyWebView Bridge
# ---------------------------------------------------------------------------

class HyperagentAPI:
    def __init__(self, acp_client):
        self._acp = acp_client

    def send_prompt(self, text):
        if text and text.strip():
            threading.Thread(target=self._acp.prompt, args=(text.strip(),), daemon=True).start()

    def generate_title(self, user_message):
        """Generate a short session title from the user's first message."""
        def _run():
            try:
                # Simple heuristic: use first sentence/line, cleaned up
                text = user_message.strip()
                # Take first line or first sentence
                for sep in ['\n', '. ', '? ', '! ']:
                    if sep in text:
                        text = text[:text.index(sep)]
                        break
                # Remove common filler prefixes
                for prefix in ['can you ', 'could you ', 'please ', 'i want to ', 'i need to ', "let's ", 'help me ']:
                    if text.lower().startswith(prefix):
                        text = text[len(prefix):]
                        break
                # Capitalize and truncate
                title = text[:40].strip()
                if len(user_message) > 40:
                    title = title.rstrip('.!?, ') + '...'
                if title:
                    title = title[0].upper() + title[1:]
                else:
                    title = user_message[:30]

                self._acp._push_js("__acpSessionTitle", {"title": title})
                # Persist title for sidebar
                if self._acp._session_id:
                    prefs = self._acp._load_prefs()
                    titles = prefs.get("sessionTitles", {})
                    titles[self._acp._session_id] = title
                    prefs["sessionTitles"] = titles
                    self._acp._save_prefs(prefs)
            except Exception as e:
                logger.error(f"generate_title error: {e}")
                fallback = user_message[:30].strip()
                if len(user_message) > 30:
                    fallback += '...'
                self._acp._push_js("__acpSessionTitle", {"title": fallback})
        threading.Thread(target=_run, daemon=True).start()

    def cancel(self, reason=None):
        self._acp.cancel(reason=reason)

    def new_session(self):
        threading.Thread(target=self._acp.new_session, daemon=True).start()

    def reconnect(self):
        threading.Thread(target=self._acp.start, daemon=True).start()

    def get_state(self):
        return self._acp.state

    def toggle_fullscreen(self):
        if self._acp._window:
            self._acp._window.toggle_fullscreen()

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
            # Don't mark our own kiro-cli's sessions as locked
            if pid == self._get_own_kiro_pid():
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

    def _get_own_kiro_pid(self):
        """Get the PID of our kiro-cli process from any owned session lock."""
        try:
            sessions_to_check = self._acp._owned_sessions.copy()
            if self._acp._session_id:
                sessions_to_check.add(self._acp._session_id)
            sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
            for sid in sessions_to_check:
                lock_file = sessions_dir / f"{sid}.lock"
                if lock_file.exists():
                    data = json.loads(lock_file.read_text(encoding="utf-8"))
                    pid = int(data.get("pid", 0))
                    if pid:
                        return pid
        except Exception:
            pass
        return None

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
        """Rename a session by updating the custom title in preferences."""
        if not session_id or not new_title:
            return False
        try:
            prefs = self._acp._load_prefs()
            titles = prefs.get("sessionTitles", {})
            titles[session_id] = new_title.strip()[:60]
            prefs["sessionTitles"] = titles
            self._acp._save_prefs(prefs)
            return True
        except Exception as e:
            logger.error(f"rename_session error: {e}")
            return False

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
                    if kind == "Prompt":
                        for c in content:
                            if c.get("kind") == "text" and c.get("data"):
                                messages.append({"role": "user", "text": c["data"]})
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
                            messages.append({"role": "agent", "text": combined_text})
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

    acp = ACPClient()
    api = HyperagentAPI(acp)

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
        logger.info("on_start: window ready, connecting protocol")
        acp.connect()
        # Start theme file watcher
        _start_theme_watcher(window, api)

    webview.start(on_start, icon=icon_path, debug=False)
    acp.stop()


if __name__ == "__main__":
    main()
