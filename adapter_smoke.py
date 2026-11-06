"""Smoke test for a claude-agent-acp install.

Usage: python adapter_smoke.py <path to the adapter's dist/index.js>

Launches the adapter over stdio and runs the protocol calls Hyperagent depends
on. Prints one JSON line: {"ok": bool, "checks": [{"name", "ok", "detail"}]}.
Runs as its own process because PyWebView corrupts child stdio pipes.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backends import ClaudeCodeBackend  # noqa: E402

START_TIMEOUT = 90
CALL_TIMEOUT = 30
PROMPT_TIMEOUT = 150


def probe_cwd():
    """Session directory outside the workspace so test sessions are never listed."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Hyperagent" / "adapter-test"
    base.mkdir(parents=True, exist_ok=True)
    return str(base).replace("\\", "/")


def clear_transcripts(cwd):
    """Remove the test session transcripts Claude Code leaves behind."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", cwd.replace("/", "\\"))
    for transcript in (Path.home() / ".claude" / "projects" / slug).glob("*.jsonl"):
        try:
            transcript.unlink()
        except OSError:
            pass


class AdapterProcess:
    """Minimal JSON-RPC client over the adapter's stdio."""

    def __init__(self, argv, env, cwd):
        self._proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=cwd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self._next_id = 0
        self._pending = {}
        self._lock = threading.Lock()
        self.updates = []
        self.stderr_tail = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stderr(self):
        for raw in self._proc.stderr:
            self.stderr_tail = (self.stderr_tail + [raw.decode("utf-8", "replace").rstrip()])[-5:]

    def _read_stdout(self):
        for raw in self._proc.stdout:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if "method" in msg and "id" in msg:
                self._send({"jsonrpc": "2.0", "id": msg["id"],
                            "error": {"code": -32601, "message": "not supported by the test client"}})
            elif msg.get("method") == "session/update":
                self.updates.append((msg.get("params") or {}).get("update") or {})
            elif "id" in msg:
                slot = self._pending.get(msg["id"])
                if slot:
                    slot["msg"] = msg
                    slot["event"].set()

    def _send(self, msg):
        with self._lock:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode())
            self._proc.stdin.flush()

    def call(self, method, params, timeout=CALL_TIMEOUT):
        """Send a request and return its result; raises on error or timeout."""
        self._next_id += 1
        rid = self._next_id
        slot = {"event": threading.Event(), "msg": None}
        self._pending[rid] = slot
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if not slot["event"].wait(timeout):
            detail = " | ".join(self.stderr_tail)
            raise RuntimeError("%s timed out after %ds%s" % (method, timeout, (" (stderr: %s)" % detail) if detail else ""))
        msg = slot["msg"]
        if "error" in msg:
            raise RuntimeError("%s failed: %s" % (method, json.dumps(msg["error"])[:200]))
        return msg.get("result") or {}

    def close(self):
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()


def run(entry):
    node = shutil.which("node")
    if not node:
        return [{"name": "launch", "ok": False, "detail": "node not found on PATH"}]
    cwd = probe_cwd()
    env = dict(os.environ, CLAUDE_CODE_ENABLE_TODO_TOOLS="1", ENABLE_CLAUDEAI_MCP_SERVERS="false")
    backend = ClaudeCodeBackend
    checks = []
    state = {}

    def step(name, fn):
        try:
            checks.append({"name": name, "ok": True, "detail": fn() or ""})
            return True
        except Exception as e:
            checks.append({"name": name, "ok": False, "detail": str(e)})
            return False

    adapter = AdapterProcess([node, entry], env, cwd)
    try:
        def initialize():
            capabilities = {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}
            capabilities.update(backend.client_capabilities_extra)
            result = adapter.call("initialize", {
                "protocolVersion": 1, "clientCapabilities": capabilities,
                "clientInfo": {"name": "hyperagent-smoke", "version": "1.0.0"}}, timeout=START_TIMEOUT)
            if "agentCapabilities" not in result:
                raise RuntimeError("no agentCapabilities in the initialize result")
            info = result.get("agentInfo") or {}
            return "%s %s" % (info.get("name", "agent"), info.get("version", ""))

        def new_session():
            result = adapter.call("session/new", {
                "cwd": cwd, "mcpServers": [], "_meta": backend.session_meta})
            state["session"] = result.get("sessionId")
            if not state["session"]:
                raise RuntimeError("no sessionId returned")
            options = {o.get("id") for o in result.get("configOptions") or [] if isinstance(o, dict)}
            missing = [o for o in (backend.model_config_option, backend.effort_config_option) if o not in options]
            if missing:
                raise RuntimeError("config options missing: %s" % ", ".join(missing))
            modes = [m.get("id") for m in (result.get("modes") or {}).get("availableModes") or [] if isinstance(m, dict)]
            state["mode"] = next((m for m in (backend.session_mode, backend.session_mode_fallback) if m in modes), None)
            if not state["mode"]:
                raise RuntimeError("neither %s nor %s offered (modes: %s)" % (
                    backend.session_mode, backend.session_mode_fallback, ", ".join(modes) or "none"))
            return "mode %s" % state["mode"]

        def set_mode():
            adapter.call("session/set_mode", {"sessionId": state["session"], "modeId": state["mode"]})

        def prompt():
            result = adapter.call("session/prompt", {
                "sessionId": state["session"],
                "prompt": [{"type": "text", "text": "Reply with the single word OK."}]}, timeout=PROMPT_TIMEOUT)
            if result.get("stopReason") != "end_turn":
                raise RuntimeError("stopReason was %r" % result.get("stopReason"))
            time.sleep(0.5)
            text = "".join((u.get("content") or {}).get("text") or ""
                           for u in adapter.updates if u.get("sessionUpdate") == "agent_message_chunk")
            if not text.strip():
                raise RuntimeError("no agent_message_chunk text streamed")
            return "replied %r" % text.strip()[:40]

        def usage_update():
            usage = [u for u in adapter.updates if u.get("sessionUpdate") == "usage_update"]
            if not any("used" in u and "size" in u for u in usage):
                raise RuntimeError("no usage_update with used/size during the turn")

        def list_sessions():
            result = adapter.call("session/list", {"cwd": cwd})
            if state["session"] not in [s.get("sessionId") for s in result.get("sessions") or []]:
                raise RuntimeError("the new session is missing from session/list")

        def delete_session():
            adapter.call("session/delete", {"sessionId": state["session"]})

        if not step("initialize", initialize) or not step("session/new", new_session):
            return checks
        step("session/set_mode", set_mode)
        if step("session/prompt streams a reply", prompt):
            step("usage_update has used/size", usage_update)
        step("session/list", list_sessions)
        step("session/delete", delete_session)
    finally:
        adapter.close()
        clear_transcripts(cwd)
    return checks


if __name__ == "__main__":
    results = run(sys.argv[1])
    print(json.dumps({"ok": all(c["ok"] for c in results), "checks": results}))
