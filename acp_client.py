"""
ACPClient — manages the kiro-cli subprocess and JSON-RPC protocol.

One instance per tab. Handles subprocess lifecycle, socket communication,
message dispatch, state machine transitions, and push-to-frontend events.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from helpers import (
    HYPERAGENT_DIR,
    HYPERSPACE_ROOT,
    PORTAL_ROOT,
    PREFS_FILE,
    logger,
    _check_auth,
    _do_login_visible,
    _find_kiro,
    _kill_inflight_title_subprocesses,
    _title_inflight_snapshot,
    _TITLE_SPAWN_SEQ,
    _SKILL_CACHE,
    _SKILL_MD_PATTERN,
    AUTH_OK,
    AUTH_NO,
    AUTH_UNKNOWN,
    get_kiro_version,
    get_model_rates,
    invalidate_kiro_version_cache,
    read_kiro_default_model,
)


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
        self._owned_sessions = set()
        self._lock = threading.Lock()
        self._last_push = 0
        self._server_sock = None
        self._last_metadata = None
        self._active_prompt_id = None
        self._skill_tool_ids = set()
        self._todo_tool_ids = set()
        self._cancelled = threading.Event()
        self._current_model_id = None
        self._available_models = []
        self._prompt_start = None
        self._write_lock = threading.Lock()
        self._auto_recover_attempts = 0
        self._auto_recover_lock = threading.Lock()

    def _tab_ctx(self):
        """Return '[tab=abcdef]' prefix for logs."""
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
        auth = _check_auth()
        if auth is AUTH_UNKNOWN:
            logger.warning(
                "%sstart_process: auth state unknown (exe locked) — spawning anyway, "
                "skipping login path", self._tab_ctx(),
            )
        elif auth is AUTH_NO:
            logger.warning("%sstart_process: not authenticated, triggering visible login", self._tab_ctx())
            if self._window:
                self._push_js("__acpAuthRequired", {"url": None})
            success = _do_login_visible()
            if not success or _check_auth() is not AUTH_OK:
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

    AUTO_RECOVER_MAX = 2

    # Exit code 0xC0000138 (STATUS_DLL_NOT_FOUND) — kiro-cli self-update
    # replaced the binary in-place, killing all running instances.
    _UPDATE_EXIT_CODE = 3221225786  # 0xC0000138
    _UPDATE_POLL_INTERVAL = 5       # seconds between binary existence checks
    _UPDATE_POLL_MAX = 24           # max polls (~2 minutes total)

    def _auto_recover(self, exit_code):
        """Attempt a bounded automatic restart after an unexpected child exit."""
        # Detect self-update: route to a dedicated handler that polls for the
        # binary instead of consuming the normal recovery budget.
        if exit_code == self._UPDATE_EXIT_CODE:
            self._handle_update_restart()
            return

        with self._auto_recover_lock:
            if self._auto_recover_attempts >= self.AUTO_RECOVER_MAX:
                logger.warning(
                    "%sauto-recover: budget exhausted (%d/%d), leaving crashed for manual reconnect",
                    self._tab_ctx(), self._auto_recover_attempts, self.AUTO_RECOVER_MAX,
                )
                self._push_js("__acpRecovery", {
                    "phase": "exhausted",
                    "attempts": self._auto_recover_attempts,
                    "exitCode": exit_code,
                })
                return
            self._auto_recover_attempts += 1
            attempt = self._auto_recover_attempts

        logger.info("%sauto-recover: attempt %d/%d after exit code=%s",
                    self._tab_ctx(), attempt, self.AUTO_RECOVER_MAX, exit_code)
        self._push_js("__acpRecovery", {
            "phase": "attempting",
            "attempt": attempt,
            "max": self.AUTO_RECOVER_MAX,
            "exitCode": exit_code,
        })

        def _run():
            killed = _kill_inflight_title_subprocesses()
            if killed:
                logger.info("%sauto-recover: killed %d title subprocess(es), waiting for DLL release",
                            self._tab_ctx(), killed)
                time.sleep(3)
            try:
                self.start()
            except Exception as e:
                logger.error("%sauto-recover: restart failed: %s", self._tab_ctx(), e)
                self._state = "crashed"
                self._push_state()
                self._push_js("__acpRecovery", {"phase": "failed", "attempt": attempt})

        threading.Thread(target=_run, daemon=True).start()

    def _handle_update_restart(self):
        """Handle kiro-cli self-update: poll for binary availability then reconnect.

        Does NOT consume the normal auto-recover budget. Pushes a friendly
        'updating' UI state instead of the alarming crash/error sequence.
        """
        logger.info("%supdate-restart: kiro-cli self-update detected (exit 0xC0000138)",
                    self._tab_ctx())
        self._push_js("__acpUpdating", {"phase": "waiting"})

        def _poll_and_reconnect():
            # Wait for the binary to become available and stable (mtime settles)
            polls = 0
            last_mtime = None
            stable_count = 0
            while polls < self._UPDATE_POLL_MAX:
                time.sleep(self._UPDATE_POLL_INTERVAL)
                polls += 1
                kiro_path = self._find_kiro()
                if not kiro_path:
                    logger.debug("%supdate-restart: poll %d/%d — binary not found",
                                 self._tab_ctx(), polls, self._UPDATE_POLL_MAX)
                    self._push_js("__acpUpdating", {
                        "phase": "waiting",
                        "poll": polls,
                        "max": self._UPDATE_POLL_MAX,
                    })
                    last_mtime = None
                    stable_count = 0
                    continue

                # Binary exists — check if mtime has stabilized (not still being written)
                try:
                    mtime = Path(kiro_path).stat().st_mtime
                except OSError:
                    last_mtime = None
                    stable_count = 0
                    continue

                if last_mtime is not None and mtime == last_mtime:
                    stable_count += 1
                else:
                    stable_count = 0
                last_mtime = mtime

                # Require 2 consecutive stable polls before reconnecting
                if stable_count >= 1:
                    logger.info(
                        "%supdate-restart: binary stable after %d polls, reconnecting",
                        self._tab_ctx(), polls,
                    )
                    # Invalidate cached version so the fresh binary version is fetched
                    invalidate_kiro_version_cache()
                    self._push_js("__acpUpdating", {"phase": "reconnecting"})
                    try:
                        self.start()
                        return
                    except Exception as e:
                        logger.error("%supdate-restart: reconnect failed: %s",
                                     self._tab_ctx(), e)
                        # Fall through to retry on next poll
                        stable_count = 0
                        continue

                logger.debug(
                    "%supdate-restart: poll %d/%d — binary found, waiting for stability",
                    self._tab_ctx(), polls, self._UPDATE_POLL_MAX,
                )
                self._push_js("__acpUpdating", {
                    "phase": "waiting",
                    "poll": polls,
                    "max": self._UPDATE_POLL_MAX,
                })

            # Exhausted polling budget — fall back to crashed state
            logger.warning(
                "%supdate-restart: timed out after %d polls, falling back to crashed",
                self._tab_ctx(), self._UPDATE_POLL_MAX,
            )
            self._state = "crashed"
            self._push_state()
            self._push_js("__acpUpdating", {"phase": "timeout"})

        threading.Thread(target=_poll_and_reconnect, daemon=True).start()

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
        with self._write_lock:
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
            if getattr(self, '_suppress_init_ready', False):
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
        """Assign session_id and notify the frontend."""
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
            self._capture_and_push_models(result)
            self._apply_preferred_model()
        elif isinstance(result, dict) and "error" in result:
            err = result["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            logger.warning("%s_on_session error, creating new: %s", self._tab_ctx(), err_msg)
            self._push_js("__acpError", {"error": f"Session load failed ({err_msg}), creating new session", "source": "jsonrpc"})
            self._new_session()
            return
        else:
            self._state = "ready"
        self._push_state()

    def prompt(self, text):
        if self._state != "ready":
            return
        self._cancelled.clear()
        logger.info("%sprompt: chars=%d lazy=%s", self._tab_ctx(), len(text), not self._session_id)
        if not self._session_id:
            self._state = "prompting"
            self._prompt_start = time.time()
            self._push_state()
            def on_lazy_session(result):
                logger.debug("%son_lazy_session: %s", self._tab_ctx(), result)
                if isinstance(result, dict) and "sessionId" in result:
                    self._set_session_id(result["sessionId"])
                    self._capture_and_push_models(result)
                    self._apply_preferred_model()
                elif isinstance(result, dict) and "error" in result:
                    err = result["error"]
                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    self._state = "ready"
                    self._push_state()
                    self._push_js("__acpError", {"error": f"Session creation failed: {err_msg}", "source": "jsonrpc"})
                    logger.warning("%slazy session creation failed: %s", self._tab_ctx(), err_msg)
                    return
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
        if isinstance(result, dict) and "error" in result:
            err = result["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            self._push_js("__acpError", {"error": f"Prompt failed: {err_msg}", "source": "jsonrpc"})
            logger.error("%sprompt failed: %s | full payload: %s", self._tab_ctx(), err_msg, json.dumps(result))
        else:
            _benign_reasons = {"", "end_turn", "cancelled", "canceled",
                               "max_tokens", "max_turn_requests", "refusal"}
            if stop_reason and stop_reason not in _benign_reasons:
                self._push_js("__acpError", {
                    "error": f"Prompt ended with stopReason={stop_reason}",
                    "source": "stop_reason",
                })
                logger.warning("%sprompt ended abnormally: stopReason=%s",
                               self._tab_ctx(), stop_reason)
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
            prompt_id = self._active_prompt_id
            if prompt_id is not None:
                self._pending.pop(prompt_id, None)
                logger.debug("%scancel: dropped pending id=%s", self._tab_ctx(), prompt_id)
            self._active_prompt_id = None
            self._notify("session/cancel", {"sessionId": self._session_id})
            if prompt_id is not None:
                self._notify("$/cancel_request", {"requestId": prompt_id})
            self._state = "ready"
            self._skill_tool_ids.clear()
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
                        head = line[:60].decode("utf-8", errors="replace").rstrip()
                        tail = line[-60:].decode("utf-8", errors="replace").rstrip()
                        logger.error(
                            "%sJSON decode error: %s | len=%d head=%r tail=%r",
                            self._tab_ctx(), e, len(line), head, tail,
                        )
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
            logger.error("%skiro-cli exited: code=%s (state was %s)", self._tab_ctx(), exit_code, self._state)
            try:
                _inflight = _title_inflight_snapshot()
                if _inflight:
                    for _pid, _age, _seq, _sid, _tabs in _inflight:
                        logger.error(
                            "%sCRASH-CORRELATION code=%s | title subprocess IN FLIGHT "
                            "seq=%d pid=%d age=%.3fs spawn_session=%s spawn_tabs=%s",
                            self._tab_ctx(), exit_code, _seq, _pid, _age, _sid, _tabs,
                        )
                else:
                    logger.error(
                        "%sCRASH-CORRELATION code=%s | no title subprocess in flight "
                        "(last spawn seq=%d)",
                        self._tab_ctx(), exit_code, _TITLE_SPAWN_SEQ,
                    )
            except Exception as _ce:
                logger.error("%sCRASH-CORRELATION logging failed: %s", self._tab_ctx(), _ce)
            if self._state not in ("stopped", "crashed"):
                self._state = "crashed"
                self._push_state()
                self._push_js("__acpError", {
                    "error": f"kiro-cli exited (code={exit_code})",
                    "source": "child_exited",
                })
                self._auto_recover(exit_code)
            return
        if method == "session/update":
            if self._cancelled.is_set():
                logger.debug("%ssession/update suppressed (cancelled)", self._tab_ctx())
                return
            update = msg.get("params", {}).get("update", {})
            su_type = update.get("sessionUpdate", "unknown")
            if su_type != "agent_message_chunk":
                logger.trace("%ssession_update: type=%s id=%s title=%s", self._tab_ctx(), su_type, update.get('toolCallId','')[:20], update.get('title',''))
            if su_type == "tool_call":
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
                    return
                tool_name_meta = ""
                meta_block = update.get("_meta", {})
                if isinstance(meta_block, dict):
                    tool_name_meta = (meta_block.get("kiro", {}).get("toolName", "") or "").lower()
                title = (update.get("title", "") or "").lower()
                if "todo_list" in tool_name_meta or "todo_list" in title:
                    self._todo_tool_ids.add(update.get("toolCallId", ""))
                    raw_input = update.get("rawInput")
                    if raw_input and isinstance(raw_input, dict) and raw_input.get("command"):
                        self._push_js("__acpTaskUpdate", raw_input)
                        logger.trace("%stodo_list push (call): %s", self._tab_ctx(), json.dumps(raw_input)[:300])
                # --- Hypereye event emission: file writes ---
                kind = (update.get("kind", "") or "").lower()
                if kind == "edit":
                    self._emit_file_change_event(update)
            if su_type == "tool_call_update":
                if update.get("toolCallId", "") in self._skill_tool_ids:
                    return
                if update.get("toolCallId", "") in self._todo_tool_ids:
                    output = update.get("output") or update.get("result") or update.get("content")
                    raw_input = update.get("rawInput")
                    payload = None
                    if output:
                        try:
                            payload = json.loads(output) if isinstance(output, str) else output
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if not payload and raw_input:
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
            if self._cancelled.is_set():
                options = msg.get("params", {}).get("options", [])
                deny = next((o for o in options if "deny" in o.get("kind", "") or "reject" in o.get("kind", "")), None)
                if deny:
                    self._send({"jsonrpc": "2.0", "id": rid,
                        "result": {"outcome": {"outcome": "selected", "optionId": deny["optionId"]}}})
                else:
                    self._send({"jsonrpc": "2.0", "id": rid,
                        "result": {"outcome": {"outcome": "dismissed"}}})
                return
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
        locations = update.get("locations")
        if locations and isinstance(locations, list):
            for loc in locations:
                p = loc.get("path", "")
                m = _SKILL_MD_PATTERN.search(p)
                if m:
                    return m.group(1)
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
            p = raw.get("path", "")
            if p:
                m = _SKILL_MD_PATTERN.search(p)
                if m:
                    return m.group(1)
        return "_unknown"


    # --- Push to frontend ---

    def _push_js(self, fn_name, data):
        if not self._window:
            return
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

    def _emit_file_change_event(self, update: dict):
        """Append a file-change event to .events/file-changes.jsonl for Hypereye.

        Fires on every edit-kind tool call. Non-blocking, best-effort — failures
        are logged but never interrupt the main protocol flow.
        """
        try:
            # Extract file path — try content[].path first (diff format), then rawInput.path
            filepath = ""
            content_list = update.get("content")
            if isinstance(content_list, list) and content_list:
                for item in content_list:
                    if isinstance(item, dict) and item.get("path"):
                        filepath = item["path"]
                        break
            if not filepath:
                raw_input = update.get("rawInput")
                if isinstance(raw_input, dict):
                    filepath = raw_input.get("path", "")
            if not filepath:
                return

            # Make path workspace-relative if absolute
            try:
                p = Path(filepath)
                if p.is_absolute():
                    filepath = str(p.relative_to(PORTAL_ROOT)).replace("\\", "/")
            except (ValueError, TypeError):
                pass

            # Line range: [0, 0] means "unknown region" — viewer shows file without highlight
            lines = [0, 0]
            raw_input = update.get("rawInput")
            if isinstance(raw_input, dict):
                if raw_input.get("insertLine") is not None:
                    start = int(raw_input["insertLine"]) + 1
                    content_str = raw_input.get("content", "")
                    end = start + content_str.count("\n") if content_str else start
                    lines = [start, end]

            from datetime import datetime, timezone
            event = {
                "event": "file_changed",
                "path": filepath,
                "lines": lines,
                "session_id": self._session_id or "",
                "tab_id": getattr(self, "_tab_id", "") or "",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            }

            events_file = HYPERSPACE_ROOT / ".events" / "file-changes.jsonl"
            events_file.parent.mkdir(parents=True, exist_ok=True)
            with open(events_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
            logger.debug("%sevent emitted: file_changed %s", self._tab_ctx(), filepath)
        except Exception as e:
            logger.warning("%sevent emission failed: %s", self._tab_ctx(), e)

    def _push_state(self):
        prev = getattr(self, "_last_logged_state", None)
        if self._state != prev:
            logger.info("%sstate: %s -> %s", self._tab_ctx(), prev or "-", self._state)
            self._last_logged_state = self._state
            if self._state == "ready" and prev == "starting":
                with self._auto_recover_lock:
                    if self._auto_recover_attempts:
                        logger.info("%sauto-recover: reached ready, resetting budget", self._tab_ctx())
                        self._auto_recover_attempts = 0
                        self._push_js("__acpRecovery", {"phase": "recovered"})
                # Push CLI version to frontend badge
                version = get_kiro_version()
                if version:
                    self._push_js("__acpCliVersion", {"version": version})
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

    # --- Model switching ---

    def _capture_and_push_models(self, session_result):
        """Extract kiro-cli's `models` field from a session/new or session/load response."""
        if not isinstance(session_result, dict):
            return
        models = session_result.get("models")
        if not isinstance(models, dict):
            return
        self._current_model_id = models.get("currentModelId") or self._current_model_id
        available = models.get("availableModels")
        if isinstance(available, list) and available:
            self._available_models = available
        self._push_models_event()

    def _push_models_event(self):
        """Push the current model state to the frontend."""
        rates = get_model_rates()
        enriched = []
        for m in (self._available_models or []):
            mid = m.get("modelId") if isinstance(m, dict) else None
            if mid and mid in rates:
                m2 = dict(m)
                m2["rateMultiplier"] = rates[mid]
                enriched.append(m2)
            else:
                enriched.append(m)
        self._push_js("__acpModels", {
            "currentModelId": self._current_model_id,
            "availableModels": enriched,
            "defaultModelId": read_kiro_default_model(),
        })

    def _apply_preferred_model(self):
        """After session setup, apply stored hyperagent-level default if it differs."""
        preferred = self._load_prefs().get("lastModelId")
        if not preferred or preferred == self._current_model_id:
            return
        if not self._session_id:
            return
        if not any(m.get("modelId") == preferred for m in (self._available_models or [])):
            logger.debug("%spreferred model %s not in available list, skipping", self._tab_ctx(), preferred)
            return
        logger.info("%sapplying preferred model: %s (was %s)", self._tab_ctx(), preferred, self._current_model_id)
        self._request_set_model(preferred, remember=False)

    def _request_set_model(self, model_id, remember=True):
        """Send session/set_model and update local state on success."""
        if not self._session_id:
            return

        def on_set(result):
            if isinstance(result, dict) and "error" in result:
                err = result["error"]
                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                logger.warning("%sset_model(%s) failed: %s", self._tab_ctx(), model_id, err_msg)
                self._push_js("__acpError", {"error": f"Model switch failed: {err_msg}"})
                self._push_models_event()
                return
            logger.info("%smodel switched: %s", self._tab_ctx(), model_id)
            self._current_model_id = model_id
            if remember:
                prefs = self._load_prefs()
                prefs["lastModelId"] = model_id
                self._save_prefs(prefs)
            self._push_models_event()

        self._request("session/set_model", {
            "sessionId": self._session_id,
            "modelId": model_id,
        }, on_set)

    def set_model(self, model_id):
        """Public entry point from the bridge API."""
        if not model_id:
            return False
        if not self._session_id:
            logger.warning("%sset_model called with no active session", self._tab_ctx())
            return False
        self._request_set_model(model_id, remember=True)
        return True
