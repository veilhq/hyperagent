"""
ACPClient - manages the ACP agent subprocess and JSON-RPC protocol.

One instance per tab. Handles subprocess lifecycle, socket communication,
message dispatch, state machine transitions, and push-to-frontend events.
Agent-specific behavior (launch command, auth, self-update, model metadata)
comes from the active Backend in backends.py.
"""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import ultracode
from acp_models import ModelEffortMixin
from acp_ultracode import UltracodeMixin
from backends import get_backend
from compaction_watch import file_size, read_boundaries, transcript_path
from helpers import (
    HYPERAGENT_DIR,
    HYPERSPACE_ROOT,
    PORTAL_ROOT,
    PREFS_FILE,
    logger,
    read_prefs,
    update_prefs,
    version_key,
    prompt_key,
    _kill_inflight_title_subprocesses,
    _title_inflight_snapshot,
    _TITLE_SPAWN_SEQ,
    skill_info,
    skill_name_of,
    tool_input_of,
    tool_line_of,
    tool_name_of,
    tool_paths_of,
    AUTH_OK,
    AUTH_NO,
    AUTH_UNKNOWN,
    delete_session_files,
)

# Default skill -> model tier mapping for the Claude backend, used when
# preferences.json carries no skillModelMap. Tiers resolve to the available
# model whose id contains the alias. Opus for reasoning-heavy skills; the
# cheaper default (sonnet) for everything else.
DEFAULT_SKILL_MODEL_TIER = "sonnet"
DEFAULT_SKILL_MODEL_MAP = {
    "code-changes": "opus",
    "debugging": "opus",
    "pr-review": "opus",
    "hyperspace": "sonnet",
    "analysis": "sonnet",
    "architecture-visuals": "sonnet",
    "hypervisor-prototypes": "sonnet",
}


class ACPClient(ModelEffortMixin, UltracodeMixin):
    def __init__(self):
        self._backend = get_backend()
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
        # Last context-window occupancy percentage from a standard ACP usage_update.
        self._context_usage_pct = None
        # Last Claude plan-usage report (see _capture_rate_limit); None until one arrives.
        self._rate_limit = None
        self._active_prompt_id = None
        self._skill_tool_ids = set()
        self._skill_pushed = set()
        # Model tiers already suggested this session (one nudge per target).
        self._suggested_models = set()
        self._todo_tool_ids = set()
        # Edit tool calls waiting for their path / completion before HyperEye hears about them.
        self._pending_file_changes = {}
        self._cancelled = threading.Event()
        self._current_model_id = None
        self._available_models = []
        # Slash commands the agent advertises: [{name, description, hint}].
        self._commands = []
        self._effort = None  # {"current": value, "options": [{value, name}]} or None
        self._ultracode = False  # per-tab; off at start, never persisted
        # Run limits for armed prompts, seeded from the saved defaults. Mode "once"
        # disarms after the armed prompt is sent; "stay" keeps it on.
        limits = ultracode.normalize(read_prefs().get("ultracode"))
        self._ultracode_mode = limits["mode"]
        self._ultracode_budget = limits["budget"]
        self._ultracode_agent_cap = limits["agentCap"]
        self._ultracode_threshold = limits["threshold"]
        self._questions = {}  # toolCallId -> held elicitation request awaiting an answer
        self._workflow_plans = {}  # toolCallId -> held Workflow permission request awaiting approval
        self._async_tasks = {}  # asyncTaskId -> latest state of each background workflow
        self._prompt_start = None
        self._write_lock = threading.Lock()
        self._auto_recover_attempts = 0
        self._auto_recover_lock = threading.Lock()
        # Whether the connected agent advertised image support in its
        # initialize promptCapabilities. Gates the frontend attach affordance
        # so image content blocks are never sent to an agent that rejects them.
        self._image_capable = False
        # List collecting session/update notifications during a history
        # replay (session/load on an ACP-listed backend); None otherwise.
        self._replay_capture = None
        # Callable receiving every session/update instead of the UI (headless use); None otherwise.
        self._update_sink = None
        # Session permission mode id and the display names of the offered modes.
        self._mode = None
        self._mode_names = {}
        # (sessionId, sessionUpdate type) pairs already warned about as unhandled.
        self._warned_update_types = set()
        self._prompt_key = ""
        # Transcript byte offset already scanned for compaction records; None until baselined.
        self._compaction_offset = None
        self._compaction_lock = threading.Lock()
        self._compaction_recheck_pending = False

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
        """Spawn bridge + agent. Call BEFORE webview.start()."""
        auth = self._backend.check_auth()
        if auth is AUTH_UNKNOWN:
            logger.warning(
                "%sstart_process: auth state unknown (exe locked) - spawning anyway, "
                "skipping login path", self._tab_ctx(),
            )
        elif auth is AUTH_NO:
            logger.warning("%sstart_process: not authenticated, triggering visible login", self._tab_ctx())
            if self._window:
                self._push_js("__acpAuthRequired", {"url": None})
            success = self._backend.login_visible()
            if not success or self._backend.check_auth() is not AUTH_OK:
                logger.error("%sstart_process: login failed", self._tab_ctx())
                self._state = "crashed"
                if self._window:
                    self._push_js("__acpError", {"error": "Login failed - complete login in the console window, then click Reconnect"})
                    self._push_state()
                return
            if self._window:
                self._push_js("__acpAuthComplete", {})

        spawn = self._backend.spawn_command()
        if not spawn:
            # connect() sees no socket and reports the missing agent to the UI.
            logger.error("%sstart_process: %s not found", self._tab_ctx(), self._backend.display_name)
            self._state = "crashed"
            return

        # Create TCP server to accept bridge connection
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.bind(("127.0.0.1", 0))
        self._server_sock.listen(1)
        port = self._server_sock.getsockname()[1]
        logger.info("%sstart_process: bridge listening on port %d (backend=%s)",
                    self._tab_ctx(), port, self._backend.name)

        bridge = str(HYPERAGENT_DIR / "acp_bridge.py")
        # The bridge inherits env overrides through its own environment so
        # credentials never appear on a command line.
        bridge_env = os.environ.copy()
        bridge_env.update(spawn.get("env") or {})
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        self._process = subprocess.Popen(
            [sys.executable, bridge, str(port), json.dumps(spawn["argv"])],
            env=bridge_env,
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
            self._push_js("__acpError", {"error": "%s not found or failed to start" % self._backend.display_name})
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

    # Seconds before a slow initialize shows a "still starting" notice, and
    # before an unanswered initialize / session/load is treated as failed.
    SLOW_START_NOTICE = 15
    START_TIMEOUT = 150
    LOAD_TIMEOUT = 90

    # session/update types this client renders: the frontend __acpUpdate switch,
    # plus the ones intercepted here (notice, config options, usage, title, mode).
    _HANDLED_UPDATES = frozenset({
        "agent_message_chunk", "agent_thought_chunk", "user_message_chunk",
        "tool_call", "tool_call_update", "plan",
        "notice", "config_option_update", "usage_update",
        "session_info_update", "current_mode_update", "available_commands_update",
    })
    # Deliberately not shown. These are only sent to clients advertising capabilities
    # this one does not: compaction
    # updates (compaction arrives as a "Compact conversation" tool call instead)
    # and native subagents (subagent text arrives flattened in the parent session).
    _IGNORED_UPDATES = frozenset({
        "compaction_update", "compaction_summary_chunk",
        "subagent_spawned", "subagent_update", "subagent_state_update", "session_message",
    })
    # AIR async-task updates (background workflows): tracked, not streamed to the chat.
    _ASYNC_TASK_UPDATES = frozenset({"async_task_spawned", "async_task_progress", "async_task_state_update"})

    # Exit code 0xC0000138 (STATUS_DLL_NOT_FOUND) - kiro-cli self-update
    # replaced the binary in-place, killing all running instances.
    _UPDATE_EXIT_CODE = 3221225786  # 0xC0000138
    _UPDATE_POLL_INTERVAL = 5       # seconds between binary existence checks
    _UPDATE_POLL_MAX = 24           # max polls (~2 minutes total)

    def _is_self_update_exit(self, exit_code):
        """True when the agent exited because it is replacing its own binary."""
        return self._backend.handles_self_update and exit_code == self._UPDATE_EXIT_CODE

    def _auto_recover(self, exit_code):
        """Attempt a bounded automatic restart after an unexpected child exit."""
        # Detect self-update: route to a dedicated handler that polls for the
        # binary instead of consuming the normal recovery budget.
        if self._is_self_update_exit(exit_code):
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

        After reconnecting, reloads the previous session so the user doesn't
        lose their conversation context (session IDs from before the update
        no longer exist on the new kiro-cli process).
        """
        logger.info("%supdate-restart: kiro-cli self-update detected (exit 0xC0000138)",
                    self._tab_ctx())
        # Capture the session we need to reload BEFORE start() clears anything
        stale_session = self._session_id
        self._push_js("__acpUpdating", {"phase": "waiting"})

        def _poll_and_reconnect():
            # Wait for the binary to become available and stable (mtime settles)
            polls = 0
            last_mtime = None
            stable_count = 0
            while polls < self._UPDATE_POLL_MAX:
                time.sleep(self._UPDATE_POLL_INTERVAL)
                polls += 1
                kiro_path = self._backend.find_binary()
                if not kiro_path:
                    logger.debug("%supdate-restart: poll %d/%d - binary not found",
                                 self._tab_ctx(), polls, self._UPDATE_POLL_MAX)
                    self._push_js("__acpUpdating", {
                        "phase": "waiting",
                        "poll": polls,
                        "max": self._UPDATE_POLL_MAX,
                    })
                    last_mtime = None
                    stable_count = 0
                    continue

                # Binary exists - check if mtime has stabilized (not still being written)
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
                    self._backend.invalidate_version_cache()
                    # Capture the new binary's changelog into the persistent
                    # archive. Runs off-thread so it never delays reconnect -
                    # this is exactly when new changelog content is available.
                    threading.Thread(
                        target=self._capture_changelog_safe, daemon=True
                    ).start()
                    self._push_js("__acpUpdating", {"phase": "reconnecting"})
                    try:
                        # Suppress the normal on_init ready transition - we need
                        # to reload the session before marking ready.
                        if stale_session:
                            self._suppress_init_ready = True
                        self.start()
                        if stale_session:
                            self._reload_session_after_update(stale_session)
                        return
                    except Exception as e:
                        logger.error("%supdate-restart: reconnect failed: %s",
                                     self._tab_ctx(), e)
                        self._suppress_init_ready = False
                        # Fall through to retry on next poll
                        stable_count = 0
                        continue

                logger.debug(
                    "%supdate-restart: poll %d/%d - binary found, waiting for stability",
                    self._tab_ctx(), polls, self._UPDATE_POLL_MAX,
                )
                self._push_js("__acpUpdating", {
                    "phase": "waiting",
                    "poll": polls,
                    "max": self._UPDATE_POLL_MAX,
                })

            # Exhausted polling budget - fall back to crashed state
            logger.warning(
                "%supdate-restart: timed out after %d polls, falling back to crashed",
                self._tab_ctx(), self._UPDATE_POLL_MAX,
            )
            self._state = "crashed"
            self._push_state()
            self._push_js("__acpUpdating", {"phase": "timeout"})

        threading.Thread(target=_poll_and_reconnect, daemon=True).start()

    def _capture_changelog_safe(self):
        """Capture the updated binary's changelog into the archive.

        Wrapper around the backend's capture_changelog that never raises into its
        background thread - a changelog capture failure must not disrupt the
        update-restart flow.
        """
        try:
            added = self._backend.capture_changelog()
            if added:
                logger.info("%supdate-restart: archived %d new changelog version(s)",
                            self._tab_ctx(), added)
        except Exception as e:
            logger.debug("%supdate-restart: changelog capture failed: %s",
                         self._tab_ctx(), e)

    def _reload_session_after_update(self, session_id):
        """Reload a session on a fresh kiro-cli process after self-update.

        Uses session/new to initialize protocol state, then session/load to
        switch into the previous session. If the session no longer exists on
        disk, clears session_id and marks ready with a clean slate.
        """
        logger.info("%supdate-restart: reloading session %s on new process",
                    self._tab_ctx(), session_id[:8])

        scratch = [None]

        def on_load_result(result):
            if isinstance(result, dict) and "error" in result:
                err = result["error"]
                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                logger.warning(
                    "%supdate-restart: session reload failed (%s), starting fresh",
                    self._tab_ctx(), err_msg,
                )
                self._session_id = None
                self._clear_session_id()
                self._push_js("__acpError", {
                    "error": "Previous session unavailable after update - started fresh",
                    "source": "update_restart",
                })
            else:
                logger.info("%supdate-restart: session %s reloaded successfully",
                            self._tab_ctx(), session_id[:8])
                self._set_session_id(session_id)
                self._capture_and_push_models(result)
                self._apply_preferred_model()
            # Reap on both outcomes - a failed load leaves the scratch session
            # on disk just the same, where it surfaces as an empty list entry.
            self._reap_scratch_session(scratch[0])
            scratch[0] = None
            self._state = "ready"
            # The normal on_init ready transition that owns the version badge was
            # suppressed for the reload, so push the badge explicitly here - this
            # is the self-update path, exactly when the "what's new" cue matters.
            self._push_state()
            self._push_cli_version_badge()

        def on_scratch_session(result):
            scratch_id = result.get("sessionId") if isinstance(result, dict) else None
            if scratch_id:
                self._owned_sessions.add(scratch_id)
                scratch[0] = scratch_id
            cwd = str(PORTAL_ROOT).replace("\\", "/")
            self._request("session/load", {
                "sessionId": session_id,
                "cwd": cwd,
                "mcpServers": []
            }, on_load_result)

        # Create a scratch session to initialize protocol, then load the real one
        cwd = str(PORTAL_ROOT).replace("\\", "/")
        if self._backend.uses_scratch_session_for_load:
            self._request("session/new", {"cwd": cwd, "mcpServers": []}, on_scratch_session)
        else:
            on_scratch_session(None)

    def stop(self):
        self._cancel_questions()
        if self._state == "prompting" and self._session_id and self._sockfile:
            # Let the agent record a clean stop before its processes are killed.
            logger.info("%sstop: cancelling in-flight turn", self._tab_ctx())
            try:
                self._notify("session/cancel", {"sessionId": self._session_id})
                time.sleep(0.3)
            except Exception as e:
                logger.warning("%sstop: session/cancel failed: %s", self._tab_ctx(), e)
        self._state = "stopped"
        try:
            if self._socket:
                self._socket.close()
        except Exception:
            pass
        if self._process and self._process.poll() is None:
            self._kill_process_tree(self._process)
        self._process = None
        self._socket = None
        self._sockfile = None

    def _kill_process_tree(self, proc):
        """Kill the bridge and every agent process it spawned (npx/node chains included)."""
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("%sstop: taskkill failed: %s", self._tab_ctx(), e)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            logger.warning("%sstop: bridge pid=%d still running, killing", self._tab_ctx(), proc.pid)
            proc.kill()

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

    def _request(self, method, params=None, callback=None, timeout=None, on_timeout=None):
        rid = self._next_id()
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params and method in ("session/new", "session/load") and self._backend.session_meta:
            params = dict(params, _meta=self._backend.session_meta)
        if params:
            msg["params"] = params
        if callback:
            self._pending[rid] = callback
            if timeout:
                timer = threading.Timer(timeout, self._expire_request, args=(rid, method, timeout, on_timeout))
                timer.daemon = True
                timer.start()
        self._send(msg)
        return rid

    def _expire_request(self, rid, method, timeout, on_timeout):
        """Drop a request still unanswered after `timeout` seconds; a late reply is ignored."""
        if self._pending.pop(rid, None) is None:
            return
        logger.warning("%s%s (id=%s) unanswered after %ss", self._tab_ctx(), method, rid, timeout)
        if on_timeout:
            self._run_callback(lambda _result: on_timeout(), None, rid)

    def _notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        self._send(msg)

    def request_sync(self, method, params=None, timeout=15):
        """Send a request and block for its answer.

        Returns the result dict, {"error": ...} on a JSON-RPC error, or None on
        timeout / no connection. Never call from the socket reader thread.
        """
        if not self._sockfile:
            return None
        done = threading.Event()
        box = {}

        def on_result(result):
            box["result"] = result
            done.set()

        rid = self._request(method, params, on_result)
        if not done.wait(timeout):
            self._pending.pop(rid, None)
            logger.warning("%s%s timed out after %ss", self._tab_ctx(), method, timeout)
            return None
        # _dispatch hands callbacks None for an empty `{}` result; it was answered.
        return box.get("result") or {}

    # --- Protocol flow ---

    def _initialize(self):
        def on_init(result):
            logger.trace("%son_init: %s", self._tab_ctx(), str(result)[:100])
            # Capture the agent's advertised image prompt capability. Shape:
            # result.agentCapabilities.promptCapabilities.image (kiro-cli 2.22.0).
            try:
                if isinstance(result, dict):
                    caps = (result.get("agentCapabilities") or {}).get("promptCapabilities") or {}
                    self._image_capable = bool(caps.get("image"))
                    logger.info("%son_init: image_capable=%s", self._tab_ctx(), self._image_capable)
            except Exception as e:
                logger.warning("%son_init: failed to read promptCapabilities: %s", self._tab_ctx(), e)
            if getattr(self, '_suppress_init_ready', False):
                self._suppress_init_ready = False
                logger.debug("%son_init: suppressed ready (session load pending)", self._tab_ctx())
                return
            self._state = "ready"
            self._push_state()

        # Advertise only what _handle_server_request actually implements. An
        # agent told fs/terminal are available will route file reads and shell
        # commands to this client instead of doing them itself.
        capabilities = {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}
        capabilities.update(self._backend.client_capabilities_extra)
        rid = self._request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": capabilities,
            "clientInfo": {"name": "hyperagent", "version": "1.0.0"}
        }, on_init, timeout=self.START_TIMEOUT, on_timeout=self._on_start_timeout)
        notice = threading.Timer(self.SLOW_START_NOTICE, self._notice_slow_start, args=(rid,))
        notice.daemon = True
        notice.start()

    def _notice_slow_start(self, rid):
        """Tell the UI the agent is still starting when initialize is slow to answer."""
        if rid not in self._pending:
            return
        logger.info("%sinitialize still pending after %ss", self._tab_ctx(), self.SLOW_START_NOTICE)
        self._push_js("__acpStartSlow", {"agent": self._backend.display_name})

    def _on_start_timeout(self):
        """Agent never answered initialize: mark crashed so the UI offers Reconnect."""
        self._suppress_init_ready = False
        self._state = "crashed"
        self._push_js("__acpError", {
            "error": "%s did not finish starting within %ds" % (self._backend.display_name, self.START_TIMEOUT),
            "source": "start_timeout",
        })
        self._push_state()

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
            self._register_owned_session(session_id)
        self._baseline_compaction()
        self._push_js("__acpSessionIdChanged", {"sessionId": session_id})

    def _compaction_transcript(self):
        """Claude Code transcript for the current session, or None for other backends."""
        if not self._session_id or not self._backend.display_name.startswith("Claude"):
            return None
        return transcript_path(self._session_id, PORTAL_ROOT)

    def _baseline_compaction(self):
        """Skip compactions already in the transcript so only new ones raise a card."""
        path = self._compaction_transcript()
        self._compaction_offset = file_size(path) if path else None

    def _check_compaction(self):
        """Push a card for each compaction recorded since the last check."""
        path = self._compaction_transcript()
        if path is None or self._update_sink is not None:
            return
        with self._compaction_lock:
            if self._compaction_offset is None:
                return
            events, self._compaction_offset = read_boundaries(path, self._compaction_offset)
        for event in events:
            logger.info("%scompaction: %s %s -> %s tokens", self._tab_ctx(), event["trigger"],
                        event["preTokens"], event["postTokens"])
            self._push_js("__acpCompaction", event)

    def _schedule_compaction_check(self):
        """Check now and again shortly after, since the transcript can lag the signal."""
        self._check_compaction()
        with self._compaction_lock:
            if self._compaction_recheck_pending:
                return
            self._compaction_recheck_pending = True
        timer = threading.Timer(1.5, self._recheck_compaction)
        timer.daemon = True
        timer.start()

    def _recheck_compaction(self):
        with self._compaction_lock:
            self._compaction_recheck_pending = False
        self._check_compaction()

    def _reap_scratch_session(self, session_id):
        """Delete a scratch session's files and tell the sidebar to re-query.

        session/new writes its metadata .json before returning, so the scratch
        session appears in the session list the moment it is created. Removing
        the files is not enough on its own - the list is only rebuilt when the
        frontend asks for it, so push the signal too.
        """
        if not session_id:
            return
        self._owned_sessions.discard(session_id)
        delete_session_files(session_id)
        self._push_js("__acpSessionsChanged", {"removed": session_id})

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

    @staticmethod
    def _build_prompt_blocks(text, images):
        """Build the ACP session/prompt content-block array.

        A text block is included only when text is non-empty; one image block
        is appended per attachment. Each image is {mimeType, data} where data
        is raw base64 (no data-URI prefix). Per the ACP content spec, image
        blocks require the agent's 'image' prompt capability.
        """
        blocks = []
        if text:
            blocks.append({"type": "text", "text": text})
        for img in images or []:
            data = img.get("data")
            if not data:
                continue
            blocks.append({
                "type": "image",
                "mimeType": img.get("mimeType") or "image/png",
                "data": data,
            })
        return blocks

    def prompt(self, text, images=None):
        if self._state != "ready":
            return
        # Nothing to send if there is neither text nor a usable image.
        keyword = self._backend.ultracode_keyword if self._ultracode else None
        if keyword and (reason := self._ultracode_guard()):
            logger.info("%sultracode disarmed before send: %s", self._tab_ctx(), reason)
            self._ultracode = False
            keyword = None
            self._push_guard_notice(reason, "Ultracode turned off")
            self._push_effort_event()
        wire_text = ultracode.wire_text(keyword, self._ultracode_limits(), text) if keyword else text
        blocks = self._build_prompt_blocks(wire_text, images)
        if not blocks:
            return
        if keyword and self._ultracode_mode == "once":
            self._ultracode = False
            self._push_effort_event()
        self._cancelled.clear()
        self._prompt_key = prompt_key(text)
        logger.info(
            "%sprompt: chars=%d images=%d lazy=%s",
            self._tab_ctx(), len(text or ""), len(images or []), not self._session_id,
        )
        if not self._session_id:
            self._state = "prompting"
            self._prompt_start = time.time()
            self._push_state()
            def on_lazy_session(result):
                logger.debug("%son_lazy_session: %s", self._tab_ctx(), result)
                if isinstance(result, dict) and "sessionId" in result:
                    self._set_session_id(result["sessionId"])
                    self._capture_and_push_models(result, apply_mode=False)
                    self._apply_preferred_model()
                elif isinstance(result, dict) and "error" in result:
                    err = result["error"]
                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    self._state = "ready"
                    self._push_state()
                    self._push_js("__acpError", {"error": f"Session creation failed: {err_msg}", "source": "jsonrpc"})
                    logger.warning("%slazy session creation failed: %s", self._tab_ctx(), err_msg)
                    return

                def send_prompt():
                    if not self._session_id:
                        return
                    rid = self._request("session/prompt", {
                        "sessionId": self._session_id,
                        "prompt": blocks
                    }, self._on_prompt_done)
                    self._active_prompt_id = rid

                # The permission mode must be in place before the first turn runs.
                self._apply_session_mode(result, then=send_prompt)
            cwd = str(PORTAL_ROOT).replace("\\", "/")
            self._request("session/new", {"cwd": cwd, "mcpServers": []}, on_lazy_session)
            return
        self._state = "prompting"
        self._prompt_start = time.time()
        self._push_state()
        rid = self._request("session/prompt", {
            "sessionId": self._session_id,
            "prompt": blocks
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
        self._schedule_compaction_check()
        data = result or {}
        data["_elapsed"] = elapsed
        if isinstance(result, dict) and "error" in result:
            err = result["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            err_data = err.get("data", "") if isinstance(err, dict) else ""

            # Safety net: detect orphaned session and auto-reload
            if "no session found" in str(err_data).lower() or "no session found" in err_msg.lower():
                logger.warning("%sprompt failed with orphaned session, attempting auto-reload",
                               self._tab_ctx())
                self._attempt_session_reload()
                return

            data["_failed"] = True
            data["_failedMessage"] = err_msg
            err_code = err.get("code") if isinstance(err, dict) else None
            hint = self._backend.auth_error_hint
            is_rate_limit = isinstance(err_data, dict) and err_data.get("errorKind") == "rate_limit"
            if hint and (err_code == -32000 or "authentication required" in err_msg.lower()):
                # The adapter starts sessions without credentials and only
                # rejects the first prompt, so this is where missing auth shows.
                self._backend.invalidate_auth_cache()
                data["_failedMessage"] = hint
                self._push_js("__acpError", {"error": hint, "source": "auth"})
            elif is_rate_limit:
                # Plan usage limit: show the agent's own wording ("resets 1:40pm")
                # without the JSON-RPC "Internal error:" prefix.
                limit_msg = re.sub(r"^\s*Internal error:\s*", "", err_msg)
                data["_failedMessage"] = limit_msg
                self._capture_rate_limit({"status": "rejected"})
                self._push_js("__acpError", {"error": "Usage limit reached: " + limit_msg, "source": "rate_limit"})
            else:
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
            self._merge_prompt_usage(result)
        if hasattr(self, '_last_metadata') and self._last_metadata:
            data["_metadata"] = self._last_metadata
            self._last_metadata = None
        self._skill_tool_ids.clear()
        self._skill_pushed.clear()
        self._todo_tool_ids.clear()
        self._pending_file_changes.clear()
        self._persist_turn_end(data)
        self._push_js("__acpTurnEnd", data)
        self._push_state()

    def _persist_turn_end(self, data):
        """Save the turn-end line so it can be redrawn when the session reloads."""
        sid = self._session_id
        if not sid:
            return
        keep = {k: data[k] for k in ("_elapsed", "_metadata", "_cancelled", "_failed", "_failedMessage")
                if data.get(k)}
        record = {"key": self._prompt_key, "data": keep}

        def mutate(prefs):
            turns = prefs.get("turnEnds", {})
            turns.setdefault(sid, []).append(record)
            prefs["turnEnds"] = turns
        try:
            update_prefs(mutate)
        except Exception as e:
            logger.warning("%sturn-end not persisted: %s", self._tab_ctx(), e)

    def _attempt_session_reload(self):
        """Safety net: reload a session that the new kiro-cli process doesn't recognize.

        Called when session/prompt returns 'No session found'. Uses the same
        session/new + session/load pattern as update-restart recovery.
        """
        session_id = self._session_id
        if not session_id:
            logger.warning("%s_attempt_session_reload: no session_id to reload", self._tab_ctx())
            self._push_state()
            return

        logger.info("%s_attempt_session_reload: reloading session %s", self._tab_ctx(), session_id[:8])
        self._state = "starting"
        self._push_state()
        self._push_js("__acpUpdating", {"phase": "reconnecting"})

        scratch = [None]

        def on_load_result(result):
            if isinstance(result, dict) and "error" in result:
                err = result["error"]
                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                logger.warning(
                    "%s_attempt_session_reload: reload failed (%s), clearing session",
                    self._tab_ctx(), err_msg,
                )
                self._session_id = None
                self._clear_session_id()
                self._push_js("__acpError", {
                    "error": "Session lost - please start a new conversation or reload from sidebar",
                    "source": "session_reload",
                })
            else:
                logger.info("%s_attempt_session_reload: session %s restored",
                            self._tab_ctx(), session_id[:8])
                self._set_session_id(session_id)
                self._capture_and_push_models(result)
                self._apply_preferred_model()
                self._push_js("__acpError", {
                    "error": "Session recovered - please resend your last message",
                    "source": "session_reload",
                })
            # Reap on both outcomes - a failed load leaves the scratch session
            # on disk just the same, where it surfaces as an empty list entry.
            self._reap_scratch_session(scratch[0])
            scratch[0] = None
            self._state = "ready"
            self._push_state()

        def on_scratch_session(result):
            scratch_id = result.get("sessionId") if isinstance(result, dict) else None
            if scratch_id:
                self._owned_sessions.add(scratch_id)
                scratch[0] = scratch_id
            cwd = str(PORTAL_ROOT).replace("\\", "/")
            self._request("session/load", {
                "sessionId": session_id,
                "cwd": cwd,
                "mcpServers": []
            }, on_load_result)

        cwd = str(PORTAL_ROOT).replace("\\", "/")
        if self._backend.uses_scratch_session_for_load:
            self._request("session/new", {"cwd": cwd, "mcpServers": []}, on_scratch_session)
        else:
            on_scratch_session(None)

    def cancel(self, reason=None):
        reason = reason or "user"
        logger.info("%scancel: reason=%s state=%s", self._tab_ctx(), reason, self._state)
        self._cancel_workflow_plans()
        self._cancel_questions()
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
            self._skill_pushed.clear()
            self._pending_file_changes.clear()
            cancel_data = {"_cancelled": True}
            cancel_data["_elapsed"] = round(time.time() - getattr(self, '_prompt_start', time.time()), 1)
            if self._last_metadata:
                cancel_data["_metadata"] = self._last_metadata
                self._last_metadata = None
            self._persist_turn_end(cancel_data)
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
        self._context_usage_pct = None
        self._suggested_models.clear()
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

    def _run_callback(self, cb, result, rid):
        """Run a response callback, logging any exception it raises.

        Callbacks run on their own threads, where an uncaught exception would
        otherwise vanish under pythonw.
        """
        try:
            cb(result)
        except Exception:
            logger.exception("%sresponse callback for id=%s failed", self._tab_ctx(), rid)

    def _dispatch(self, msg):
        # Response to a request we sent. Popped atomically: a timed-out request
        # may already have been removed by _expire_request.
        if "id" in msg and "method" not in msg:
            cb = self._pending.pop(msg["id"], None)
            if cb is None:
                logger.debug("%sresponse id=%s has no pending request (late or untracked)",
                             self._tab_ctx(), msg["id"])
                return
            result = msg.get("result") or msg.get("error")
            if msg.get("error"):
                result = {"error": msg["error"]}
            threading.Thread(target=self._run_callback, args=(cb, result, msg["id"]), daemon=True).start()
            return

        # Server-initiated request (permission prompts; everything else is rejected)
        if "id" in msg and "method" in msg:
            self._handle_server_request(msg)
            return

        # Notification (session/update)
        method = msg.get("method", "")
        if method == "_bridge/stderr":
            text = msg.get("params", {}).get("text", "")
            if not self._backend.stderr_to_ui:
                logger.debug("%s%s stderr: %s", self._tab_ctx(), self._backend.name, text)
                return
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
                # Suppress the raw error push for self-update exits - the
                # update handler pushes a calm "updating" message instead.
                if not self._is_self_update_exit(exit_code):
                    self._push_js("__acpError", {
                        "error": f"{self._backend.display_name} exited (code={exit_code})",
                        "source": "child_exited",
                    })
                self._auto_recover(exit_code)
            return
        if method == "session/update":
            if self._replay_capture is not None:
                # session/load replays history as session/update notifications;
                # collect them for the history view instead of streaming them.
                self._replay_capture.append(msg.get("params", {}).get("update", {}))
                return
            if self._cancelled.is_set():
                logger.debug("%ssession/update suppressed (cancelled)", self._tab_ctx())
                return
            update = msg.get("params", {}).get("update", {})
            su_type = update.get("sessionUpdate", "unknown")
            if self._update_sink is not None:
                # Headless client (plan-usage probe): updates go to the sink, not a tab.
                self._update_sink(update)
                return
            if su_type == "session_info_update":
                self._capture_agent_title(update.get("title"), msg.get("params", {}).get("sessionId"))
                return
            if su_type == "current_mode_update":
                self._set_mode(update.get("currentModeId"))
                return
            if su_type == "available_commands_update":
                self._capture_commands(update.get("availableCommands"))
                return
            if su_type in self._ASYNC_TASK_UPDATES:
                self._capture_async_task(update)
                return
            if su_type in self._IGNORED_UPDATES:
                logger.trace("%ssession_update ignored: type=%s", self._tab_ctx(), su_type)
                return
            if su_type not in self._HANDLED_UPDATES:
                self._warn_unhandled_update(su_type)
            if su_type == "notice":
                # Notices can arrive outside a turn (e.g. at session start), so
                # they bypass the turn-gated __acpUpdate stream.
                logger.info("%snotice (%s): %s", self._tab_ctx(), update.get("severity"), update.get("title"))
                self._push_js("__acpNotice", {"severity": update.get("severity"), "title": update.get("title"),
                                              "description": update.get("description")})
                return
            if su_type == "config_option_update":
                self._capture_config_options(update.get("configOptions"))
                return
            if su_type == "usage_update":
                self._capture_context_usage(update)
                return
            if su_type != "agent_message_chunk":
                logger.trace("%ssession_update: type=%s id=%s title=%s", self._tab_ctx(), su_type, update.get('toolCallId','')[:20], update.get('title',''))
            if su_type == "tool_call":
                logger.debug("%stool_call: %s (id=%s)", self._tab_ctx(), update.get('title','?'), update.get('toolCallId','')[:20])
                logger.trace("%stool_call_full: %s", self._tab_ctx(), json.dumps(update)[:800])
                tool_id = update.get("toolCallId", "")
                if self._detect_skill_activation(update):
                    # Skill loads render as a skill card instead of a tool card.
                    self._skill_tool_ids.add(tool_id)
                    self._push_skill_activation(tool_id, update)
                    return
                tool_name_lower = tool_name_of(update).lower()
                title = (update.get("title", "") or "").lower()
                if "todo_list" in tool_name_lower or "todo_list" in title:
                    self._todo_tool_ids.add(tool_id)
                    raw_input = update.get("rawInput")
                    if raw_input and isinstance(raw_input, dict) and raw_input.get("command"):
                        self._push_js("__acpTaskUpdate", raw_input)
                        logger.trace("%stodo_list push (call): %s", self._tab_ctx(), json.dumps(raw_input)[:300])
                # --- Hypereye event emission: file writes ---
                if (update.get("kind", "") or "").lower() == "edit":
                    self._track_file_change(tool_id, update)
            if su_type == "tool_call_update":
                tool_id = update.get("toolCallId", "")
                if tool_id in self._skill_tool_ids:
                    self._push_skill_activation(tool_id, update)
                    return
                if tool_id in self._pending_file_changes:
                    self._track_file_change(tool_id, update)
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
            # ACP: a request answered while its prompt turn is cancelled MUST get
            # the "cancelled" outcome, not a selected reject option.
            if self._cancelled.is_set():
                self._send({"jsonrpc": "2.0", "id": rid,
                    "result": {"outcome": {"outcome": "cancelled"}}})
                return
            options = msg.get("params", {}).get("options", [])
            if self._hold_workflow_plan(msg, options):
                return
            # Auto-allow mirrors the agent's trust-all-tools mode. "selected"
            # must carry an optionId, so a request with no options is answered
            # as cancelled rather than with an empty selection.
            allow = next((o for o in options if "allow" in o.get("kind", "")), options[0] if options else None)
            if allow and allow.get("optionId"):
                self._send({"jsonrpc": "2.0", "id": rid,
                    "result": {"outcome": {"outcome": "selected", "optionId": allow["optionId"]}}})
            else:
                logger.warning("%spermission request id=%s has no selectable option, replying cancelled",
                               self._tab_ctx(), rid)
                self._send({"jsonrpc": "2.0", "id": rid,
                    "result": {"outcome": {"outcome": "cancelled"}}})
            return

        if method == "elicitation/create" and self._hold_question(msg):
            return

        # Every agent-to-client request must get a response: the agent blocks on
        # it, so a dropped request hangs the turn with no error. Anything this
        # client does not implement (fs/*, terminal/*, extensions) gets JSON-RPC
        # "method not found" so the agent can fall back or fail visibly.
        logger.warning("%sunsupported agent request %s (id=%s), replying -32601",
                       self._tab_ctx(), method, msg.get("id"))
        self._send({"jsonrpc": "2.0", "id": msg["id"],
            "error": {"code": -32601, "message": "Method not found: %s" % method}})

    def _hold_workflow_plan(self, msg, options):
        """Park a Workflow launch request and show its script for approval; False for any other tool."""
        tool_call = msg.get("params", {}).get("toolCall") or {}
        script = tool_input_of(tool_call).get("script")
        if tool_call.get("title") != "Workflow" or not isinstance(script, str):
            return False
        call_id = tool_call.get("toolCallId") or str(msg["id"])
        self._workflow_plans[call_id] = {"rid": msg["id"], "options": options}
        logger.info("%sworkflow plan held for approval: %s", self._tab_ctx(), call_id[:12])
        plan = dict(ultracode.summarize_script(script), toolCallId=call_id, script=script)
        self._push_js("__acpWorkflowPlan", dict(plan, limits=self._ultracode_limits()))
        return True

    def answer_workflow_plan(self, call_id, allow):
        """Answer a held Workflow launch: allow it, or reject it so the agent is told no."""
        plan = self._workflow_plans.pop(call_id, None)
        if not plan:
            return False
        kinds = ("allow",) if allow else ("reject",)
        pick = next((o for o in plan["options"] if o.get("kind", "").startswith(kinds)), None)
        outcome = ({"outcome": "selected", "optionId": pick["optionId"]}
                   if pick and pick.get("optionId") else {"outcome": "cancelled"})
        logger.info("%sworkflow plan %s: %s", self._tab_ctx(), call_id[:12], "allowed" if allow else "rejected")
        self._send({"jsonrpc": "2.0", "id": plan["rid"], "result": {"outcome": outcome}})
        self._push_js("__acpWorkflowPlanClosed", {"toolCallId": call_id})
        return True

    def _cancel_workflow_plans(self):
        """Answer every held Workflow launch as cancelled and close its card."""
        for call_id, plan in list(self._workflow_plans.items()):
            self._send({"jsonrpc": "2.0", "id": plan["rid"], "result": {"outcome": {"outcome": "cancelled"}}})
            self._push_js("__acpWorkflowPlanClosed", {"toolCallId": call_id})
        self._workflow_plans.clear()

    @staticmethod
    def _parse_questions(schema):
        """Turn an elicitation form schema into [{id, title, description, multiSelect, options, custom}] in order."""
        props = (schema or {}).get("properties") or {}
        found = []
        for key, spec in props.items():
            m = re.fullmatch(r"question_(\d+)", key)
            if m and isinstance(spec, dict):
                found.append((int(m.group(1)), key, spec))
        questions = []
        for _, key, spec in sorted(found, key=lambda f: f[0]):
            multi = spec.get("type") == "array"
            raw = ((spec.get("items") or {}).get("anyOf") if multi else spec.get("oneOf")) or []
            questions.append({
                "id": key,
                "title": spec.get("title") or "",
                "description": spec.get("description") or "",
                "multiSelect": multi,
                "options": [{"value": o.get("const"), "title": o.get("title") or o.get("const") or "",
                             "description": o.get("description") or ""} for o in raw if isinstance(o, dict)],
                "custom": (key + "_custom") in props,
            })
        return questions

    def _hold_question(self, msg):
        """Park a form elicitation (an agent question) and show it as a card; False for any other mode."""
        params = msg.get("params") or {}
        call_id = params.get("toolCallId")
        if params.get("mode") != "form" or not call_id:
            return False
        if self._cancelled.is_set():
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {"action": "cancel"}})
            return True
        questions = self._parse_questions(params.get("requestedSchema"))
        self._questions[call_id] = {"rid": msg["id"]}
        logger.info("%squestion held for answer: %s (%d)", self._tab_ctx(), call_id[:12], len(questions))
        self._push_js("__acpQuestion", {"toolCallId": call_id, "message": params.get("message") or "",
                                        "questions": questions})
        return True

    def answer_question(self, call_id, answers, action):
        """Answer a held question with accept (answers), decline (skipped) or cancel; False for an unknown id."""
        held = self._questions.pop(call_id, None)
        if not held:
            return False
        action = action if action in ("accept", "decline", "cancel") else "cancel"
        result = {"action": action}
        if action == "accept":
            content = {}
            for qid, ans in (answers or {}).items():
                value = ans.get("value")
                if value not in (None, "", []):
                    content[qid] = value
                if ans.get("custom"):
                    content[qid + "_custom"] = ans["custom"]
            result["content"] = content
        logger.info("%squestion %s: %s", self._tab_ctx(), call_id[:12], action)
        self._send({"jsonrpc": "2.0", "id": held["rid"], "result": result})
        self._push_js("__acpQuestionClosed", {"toolCallId": call_id, "action": action})
        return True

    def _cancel_questions(self):
        """Answer every held question as cancelled and close its card."""
        for call_id in list(self._questions):
            self.answer_question(call_id, None, "cancel")

    def _detect_skill_activation(self, update):
        """Return the skill a tool_call loads, "_unknown" for a skill load whose name is not known yet, or None.

        Claude Code loads skills through its Skill tool; kiro reads a SKILL.md file.
        """
        if tool_name_of(update) != "Skill" and "SKILL.md" not in (update.get("title") or ""):
            return None
        return skill_name_of(update) or "_unknown"

    def _push_skill_activation(self, tool_id, update):
        """Show the skill card once per skill-load call, as soon as its name is known.

        Claude's Skill call names the skill only in a later update, so an unnamed
        Skill call waits; anything else shows a generic card right away.
        """
        if tool_id in self._skill_pushed:
            return
        name = self._detect_skill_activation(update) if update.get("sessionUpdate") == "tool_call" else skill_name_of(update)
        if not name or name == "_unknown":
            still_streaming = update.get("status") not in ("completed", "failed")
            if tool_name_of(update) == "Skill" or (update.get("sessionUpdate") == "tool_call_update" and still_streaming):
                return
            meta = {"name": "skill", "description": "Skill context activated"}
        else:
            meta = skill_info(name)
        self._skill_pushed.add(tool_id)
        self._push_js("__acpSkillActivation", meta)
        if name and name != "_unknown":
            self._maybe_suggest_model(name)

    def _maybe_suggest_model(self, skill_name):
        """Suggest a model switch when a loaded skill maps to a different tier.

        Claude-backend only (model is a session config option there; kiro uses
        modes). Pushes a suggestion the user confirms - never switches on its
        own, since a mid-session switch forces a one-turn prompt-cache rebuild.
        """
        if self._backend.name != "claude-code":
            return
        prefs = read_prefs()
        skill_map = prefs.get("skillModelMap")
        if not isinstance(skill_map, dict):
            skill_map = DEFAULT_SKILL_MODEL_MAP
        default_tier = prefs.get("skillModelDefault", DEFAULT_SKILL_MODEL_TIER)
        tier = skill_map.get(skill_name, default_tier)
        if not tier:
            return
        target = self._resolve_model_tier(tier)
        if not target or target == self._current_model_id:
            return
        # One suggestion per target per session; the user may have declined.
        if target in self._suggested_models:
            return
        self._suggested_models.add(target)
        current_name = self._model_display_name(self._current_model_id)
        target_name = self._model_display_name(target)
        logger.info("%smodel suggestion: skill=%s %s -> %s",
                    self._tab_ctx(), skill_name, current_name, target_name)
        self._push_js("__acpModelSuggestion", {
            "skill": skill_name,
            "targetModelId": target,
            "targetName": target_name,
            "currentName": current_name,
        })

    def _track_file_change(self, tool_id, update):
        """Emit a HyperEye file-change event once an edit's path is known and it has been applied.

        kiro sends the complete edit on the tool_call; Claude Code streams the
        path and input across updates and reports status=completed at the end.
        """
        pending = self._pending_file_changes.setdefault(tool_id, {"path": "", "line": None, "raw": {}})
        paths = tool_paths_of(update)
        if paths:
            pending["path"] = paths[0]
        line = tool_line_of(update)
        if line is not None:
            pending["line"] = line
        if tool_input_of(update):
            pending["raw"] = tool_input_of(update)
        status = update.get("status")
        if status == "failed":
            self._pending_file_changes.pop(tool_id, None)
            return
        complete_on_call = update.get("sessionUpdate") == "tool_call" and bool(tool_input_of(update))
        if pending["path"] and (status == "completed" or complete_on_call):
            self._pending_file_changes.pop(tool_id, None)
            self._emit_file_change_event(pending["path"], pending["line"], pending["raw"])


    # --- Push to frontend ---

    @staticmethod
    def _normalize_commands(raw):
        """[{name, description, hint}] from an availableCommands list, skipping malformed entries."""
        commands = []
        for item in raw if isinstance(raw, list) else []:
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str) or not name:
                continue
            hint = (item.get("input") or {}).get("hint") if isinstance(item.get("input"), dict) else None
            commands.append({"name": name, "description": item.get("description") or "",
                             "hint": hint if isinstance(hint, str) else ""})
        return commands

    def _capture_commands(self, raw):
        """Store the advertised slash commands and push them to the command menu."""
        self._commands = self._normalize_commands(raw)
        logger.info("%scommands: %d advertised", self._tab_ctx(), len(self._commands))
        self._push_js("__acpCommands", {"commands": self._commands})

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

    def _emit_file_change_event(self, filepath, start_line, raw_input):
        """Append a file-change event to .events/file-changes.jsonl for Hypereye.

        Non-blocking, best-effort - failures are logged but never interrupt the
        main protocol flow.
        """
        try:
            # Make path workspace-relative if absolute. realpath expands 8.3
            # short names (C:/Users/JOSHWO~1/...) so the prefix comparison works.
            try:
                p = Path(filepath)
                if p.is_absolute():
                    filepath = str(Path(os.path.realpath(p)).relative_to(PORTAL_ROOT)).replace("\\", "/")
            except (ValueError, TypeError, OSError):
                pass

            # Line range: [0, 0] means "unknown region" - viewer shows file without highlight
            lines = [0, 0]
            if start_line:
                inserted = raw_input.get("content") if raw_input.get("insertLine") is not None else raw_input.get("new_string")
                end = start_line + inserted.count("\n") if isinstance(inserted, str) else start_line
                lines = [start_line, end]

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
                # Push CLI version to frontend badge. Flag whether this version's
                # release notes are still unseen so the badge can cue "what's new".
                self._push_cli_version_badge()
        self._push_js("__acpStateChange", {"state": self._state, "imageCapable": self._image_capable})

    def _push_cli_version_badge(self):
        """Push the kiro-cli version to the frontend badge, flagging unseen notes.

        Resolves the binary version and pushes `__acpCliVersion` with an `isNew`
        flag (true when the running version differs from the acknowledged
        changelog version). The version is re-read once if the first read comes
        back empty: right after an in-place self-update swap the binary can be
        momentarily unreadable, and a silently dropped push leaves the "what's
        new" cue un-fired on the exact update that produced the new notes.
        """
        version = self._backend.version()
        if not version:
            # Transient miss right after a binary swap - invalidate and retry once.
            self._backend.invalidate_version_cache()
            version = self._backend.version()
        if not version:
            logger.debug("%scli-version badge: version unresolved, skipping push", self._tab_ctx())
            return
        seen = self._load_prefs().get("changelogSeenVersion")
        self._push_js("__acpCliVersion", {
            "version": version,
            "isNew": version != seen,
        })
        threading.Thread(target=self._push_update_available, args=(version,), daemon=True).start()

    def _push_update_available(self, installed):
        """Push `__acpCliUpdate` when the backend reports a newer release.

        Runs off the state thread because the check may fetch over the network.
        """
        try:
            latest = self._backend.latest_version()
            if latest and version_key(latest) > version_key(installed):
                logger.info("%supdate available: %s -> %s", self._tab_ctx(), installed, latest)
                self._push_js("__acpCliUpdate", {"installed": installed, "latest": latest})
        except Exception as e:
            logger.debug("%supdate check failed: %s", self._tab_ctx(), e)

    # --- Session persistence ---

    def _load_prefs(self):
        return read_prefs()

    def _load_session_id(self):
        return self._load_prefs().get("sessionId")

    def _save_session_id(self, sid):
        update_prefs(lambda prefs: prefs.__setitem__("sessionId", sid))

    def _register_owned_session(self, sid):
        """Add a session to the ownedSessions set in preferences.

        Only user-created sessions are registered. Ephemeral (automation)
        sessions are excluded so they never appear in the sidebar.
        """
        def mutate(prefs):
            owned = set(prefs.get("ownedSessions", []))
            owned.add(sid)
            prefs["ownedSessions"] = sorted(owned)
        update_prefs(mutate)

    def _unregister_owned_session(self, sid):
        """Remove a session from the ownedSessions set in preferences."""
        def mutate(prefs):
            owned = set(prefs.get("ownedSessions", []))
            owned.discard(sid)
            prefs["ownedSessions"] = sorted(owned)
        update_prefs(mutate)

    def _clear_session_id(self):
        update_prefs(lambda prefs: prefs.pop("sessionId", None))

    # --- Model switching ---

    def _capture_and_push_models(self, session_result, apply_mode=True):
        """Capture models from a session/new or session/load response.

        kiro-cli returns a `models` field; agents that expose the model as a
        session config option (backend.model_config_option) return it under
        `configOptions`. Also applies the backend's permission mode unless the
        caller sequences that itself.
        """
        if not isinstance(session_result, dict):
            return
        if apply_mode:
            self._apply_session_mode(session_result)
        self._capture_effort(session_result.get("configOptions"))
        models = session_result.get("models")
        if not isinstance(models, dict):
            models = self._models_from_config_options(session_result.get("configOptions"))
        if not isinstance(models, dict):
            return
        self._current_model_id = models.get("currentModelId") or self._current_model_id
        available = models.get("availableModels")
        if isinstance(available, list) and available:
            self._available_models = available
        self._push_models_event()

    def _capture_config_options(self, config_options):
        """Refresh model and effort state from a configOptions list (set_config_option
        responses and config_option_update notifications)."""
        if not isinstance(config_options, list):
            return
        self._capture_effort(config_options)
        models = self._models_from_config_options(config_options)
        if isinstance(models, dict):
            self._current_model_id = models.get("currentModelId") or self._current_model_id
            if models.get("availableModels"):
                self._available_models = models["availableModels"]
            self._push_models_event()

    # --- Context usage (standard ACP) ---

    def _capture_context_usage(self, update):
        """Track a usage_update ({used, size} tokens) and push it to the ctx meter.

        Pushed immediately rather than at turn end because the agent also sends
        usage_update outside a turn (compaction, autonomous results).
        """
        meta = update.get("_meta")
        if isinstance(meta, dict) and isinstance(meta.get("_claude/rateLimit"), dict):
            self._capture_rate_limit(meta["_claude/rateLimit"])
        used, size = update.get("used"), update.get("size")
        if not isinstance(used, (int, float)) or not isinstance(size, (int, float)) or size <= 0:
            logger.trace("%susage_update ignored: %s", self._tab_ctx(), json.dumps(update)[:300])
            return
        pct = min(100.0, max(0.0, used * 100.0 / size))
        self._context_usage_pct = pct
        logger.debug("%scontext usage: %d/%d (%.1f%%)", self._tab_ctx(), used, size, pct)
        self._push_js("__acpContextUsage", {"pct": pct, "used": used, "size": size})
        self._schedule_compaction_check()

    def _capture_rate_limit(self, info):
        """Track Claude plan usage ({status, rateLimitType, utilization 0-1, resetsAt})
        and push it to the plan-usage chip. Account-wide, so the newest report wins."""
        snapshot = {
            "status": info.get("status"),
            "rateLimitType": info.get("rateLimitType"),
            "utilization": info.get("utilization"),
            "resetsAt": info.get("resetsAt"),
            "receivedAt": time.time(),
        }
        self._rate_limit = snapshot
        logger.info("%srate limit: %s %s util=%s resets=%s", self._tab_ctx(), snapshot["status"],
                    snapshot["rateLimitType"], snapshot["utilization"], snapshot["resetsAt"])
        self._push_js("__acpRateLimit", snapshot)

    # --- Agent-sent session info (title, mode, unhandled types) ---

    def _warn_unhandled_update(self, su_type):
        """Log once per session and type when the agent sends an update this client has no handler for."""
        key = (self._session_id, su_type)
        if key in self._warned_update_types:
            return
        self._warned_update_types.add(key)
        logger.warning("%sunhandled session/update type %r (session=%s); shown only as a console debug line",
                       self._tab_ctx(), su_type, (self._session_id or "-")[:8])

    def _capture_agent_title(self, title, session_id=None):
        """Adopt a title the agent assigned, unless the user renamed the session in Hyperagent."""
        sid = self._session_id
        if not isinstance(title, str) or not title.strip() or not sid:
            return
        if session_id and session_id != sid:
            return
        title = title.strip()[:60]
        if sid in (self._load_prefs().get("renamedSessions") or []):
            logger.debug("%sagent title %r ignored: session %s was renamed", self._tab_ctx(), title, sid[:8])
            return
        if title == getattr(self, "_tab_title", None):
            return
        self._tab_title = title
        logger.info("%sagent title: %r (session=%s)", self._tab_ctx(), title, sid[:8])
        self._push_js("__acpSessionTitle", {"title": title, "source": "agent"})

        def mutate(prefs):
            titles = prefs.get("sessionTitles", {})
            titles[sid] = title
            prefs["sessionTitles"] = titles
            owned = set(prefs.get("ownedSessions", []))
            owned.add(sid)
            prefs["ownedSessions"] = sorted(owned)

        update_prefs(mutate)

    def _capture_modes(self, modes):
        """Record the offered mode names and current mode from a session result's `modes`."""
        if not isinstance(modes, dict):
            return
        for mode in modes.get("availableModes") or []:
            if isinstance(mode, dict) and mode.get("id"):
                self._mode_names[mode["id"]] = mode.get("name") or mode["id"]
        self._set_mode(modes.get("currentModeId"))

    def _set_mode(self, mode_id):
        """Track the session's permission mode and show it in the mode chip."""
        if not mode_id or not self._backend.session_mode:
            return
        if mode_id != self._mode:
            logger.info("%ssession mode: %s", self._tab_ctx(), mode_id)
        self._mode = mode_id
        self._push_mode_event()

    def _push_mode_event(self):
        """Push the current mode; a null id hides the chip. Only backends that set a mode show one."""
        if not self._backend.session_mode:
            return
        name = self._mode_names.get(self._mode, self._mode) if self._mode else None
        self._push_js("__acpMode", {"id": self._mode, "name": name})

    def _merge_prompt_usage(self, result):
        """Fold a PromptResponse.usage block into the turn-end metadata.

        Agents that report usage through _kiro.dev/metadata keep their values;
        only missing keys are filled. Input counts the cached tokens the model read.
        """
        usage = result.get("usage") if isinstance(result, dict) else None
        if not isinstance(usage, dict):
            return
        meta = dict(self._last_metadata or {})
        meta.setdefault("inputTokens", (usage.get("inputTokens") or 0)
                        + (usage.get("cachedReadTokens") or 0)
                        + (usage.get("cachedWriteTokens") or 0))
        meta.setdefault("outputTokens", usage.get("outputTokens") or 0)
        if self._context_usage_pct is not None:
            meta.setdefault("contextUsagePercentage", self._context_usage_pct)
        self._last_metadata = meta

    def _apply_session_mode(self, session_result, then=None):
        """Switch a new or loaded session to the backend's permission mode.

        Falls back to backend.session_mode_fallback when the preferred mode is
        not offered (e.g. an org policy disables bypass) and tells the user.
        `then` runs once the mode request is answered, or immediately when no
        mode change applies.
        """
        wanted = self._backend.session_mode
        sid = session_result.get("sessionId") if isinstance(session_result, dict) else None
        sid = sid or self._session_id
        modes = (session_result.get("modes") or {}) if isinstance(session_result, dict) else {}
        self._capture_modes(modes)
        offered = [m.get("id") for m in modes.get("availableModes") or [] if isinstance(m, dict)]
        if not wanted or not sid or not offered:
            if then:
                then()
            return
        target = wanted
        if wanted not in offered:
            target = self._backend.session_mode_fallback if self._backend.session_mode_fallback in offered else None
            logger.warning("%ssession mode %s not offered (modes=%s), falling back to %s",
                           self._tab_ctx(), wanted, offered, target)
            self._push_js("__acpError", {
                "error": "%s will not run without permission prompts here (%s mode unavailable, "
                         "likely an org policy). Using %s mode instead."
                         % (self._backend.display_name, wanted, target or modes.get("currentModeId")),
                "source": "session_mode",
            })
        if not target or target == modes.get("currentModeId"):
            if then:
                then()
            return

        def on_mode(result):
            if isinstance(result, dict) and "error" in result:
                logger.warning("%sset_mode(%s) failed: %s", self._tab_ctx(), target, result["error"])
            else:
                logger.info("%ssession %s mode set: %s", self._tab_ctx(), sid[:8], target)
                self._set_mode(target)
            if then:
                then()

        self._request("session/set_mode", {"sessionId": sid, "modeId": target}, on_mode)

