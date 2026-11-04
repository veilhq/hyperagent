"""Agent backends - the per-agent surface behind the ACP seam.

Hyperagent speaks standard ACP to whatever agent process the bridge spawns.
Everything that is NOT protocol - how to launch the agent, how to check its
auth, whether it self-updates in place, where model rates/version/changelog
come from, and how to run one-shot title/usage commands - lives on a Backend.

Call sites ask the active backend (`get_backend()`) instead of reaching for
kiro-cli directly. Adding an agent means adding a Backend subclass and a
REGISTRY entry; no call site changes.

The backend is chosen by the `backend` key in preferences.json (default
"kiro") and resolved once per process, so every tab in a process talks to the
same agent. Changing the preference takes effect on the next app start.
"""

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from helpers import (
    AUTH_NO,
    AUTH_OK,
    AUTH_UNKNOWN,
    _check_auth,
    _do_login_visible,
    _find_kiro,
    capture_changelog_cache,
    get_kiro_changelog,
    get_kiro_version,
    get_model_rates,
    invalidate_kiro_version_cache,
    logger,
    read_kiro_default_model,
    read_prefs,
    write_kiro_default_model,
)

DEFAULT_BACKEND = "kiro"


class Backend:
    """Contract for an agent backend.

    `spawn_command` and `check_auth` are required. Every other member has a
    neutral default meaning "this agent does not offer the feature", which the
    call sites already handle (heuristic title, empty rates, no version badge).
    """

    name = ""
    display_name = ""
    # True only for agents that replace their own binary in place and exit
    # with a recognizable code while doing so (see ACPClient._auto_recover).
    handles_self_update = False
    # Push each agent stderr line to the UI error bar (False: log only).
    stderr_to_ui = True
    # Loading a session first needs a throwaway session/new on the connection.
    uses_scratch_session_for_load = True
    # Sessions are listed, replayed, and deleted over ACP instead of read from
    # kiro-cli's session files.
    lists_sessions_over_acp = False
    # Permission mode set with session/set_mode after every session/new and
    # session/load, and the mode to use when that one is not offered.
    session_mode = None
    session_mode_fallback = None
    # When set, models come from this session config option and are switched
    # with session/set_config_option instead of session/set_model.
    model_config_option = None
    # preferences.json key holding the last model picked under this backend.
    model_pref_key = "lastModelId"
    # User-facing guidance shown when the agent rejects a prompt as unauthenticated.
    auth_error_hint = None
    # Extra initialize clientCapabilities this client handles for this agent.
    client_capabilities_extra = {}
    # Session config option holding the effort level, or None when the agent has none.
    effort_config_option = None
    effort_pref_key = "lastEffort"
    # The agent turns on multi-agent orchestration for a turn whose prompt carries
    # this keyword (human-originated turns only); None when it has no such mode.
    ultracode_keyword = None
    # Extra `_meta` merged into every session/new and session/load request; None for none.
    session_meta = None
    # Plan usage comes from running /usage on a hidden session (plan_usage.py)
    # instead of a usage_command shell-out.
    usage_via_session = False

    def spawn_command(self):
        """Return {"argv": [...], "env": {...}} to launch the ACP agent over
        stdio, or None when the agent cannot be found. `env` holds overrides
        merged onto the inherited environment; it is passed to the bridge via
        its process environment, never on a command line."""
        raise NotImplementedError

    def check_auth(self):
        """Return AUTH_OK, AUTH_NO, or AUTH_UNKNOWN."""
        raise NotImplementedError

    def login_visible(self):
        """Run an interactive login. Return True on success."""
        return False

    def invalidate_auth_cache(self):
        """Forget any cached auth result so the next check_auth re-reads it."""

    def find_binary(self):
        """Path of the agent executable, used to watch for an in-place
        self-update to finish. None when not applicable."""
        return None

    def model_rates(self):
        """{modelId: rate_multiplier}, or {} when the agent reports none."""
        return {}

    def read_default_model(self):
        return None

    def write_default_model(self, model_id):
        return False

    def version(self):
        return None

    def invalidate_version_cache(self):
        pass

    def changelog(self, version):
        return None

    def capture_changelog(self):
        """Archive release notes for the current binary. Returns the count
        of newly archived versions."""
        return 0

    def title_command(self, prompt):
        """argv for a one-shot, non-interactive completion of `prompt` whose
        stdout carries the reply, or None to use the heuristic title."""
        return None

    def usage_command(self):
        """argv that prints plan usage, or None when the agent has no usage
        report."""
        return None


class KiroBackend(Backend):
    name = "kiro"
    display_name = "kiro-cli"
    handles_self_update = True

    # Workspace agent the ACP session runs under. The built-in default agent
    # cannot carry hooks - hooks are only definable in an agent config file -
    # and this workspace relies on a preToolUse hook to validate document
    # writes. kiro-cli resolves it from `.kiro/agents/<name>.json` relative to
    # the spawn cwd; if the file is absent it logs a warning and falls back to
    # its default agent, so a missing config degrades rather than breaks.
    ACP_AGENT = "hyperspace"

    def spawn_command(self):
        kiro = _find_kiro()
        if not kiro:
            return None
        return {
            "argv": [kiro, "acp", "--trust-all-tools", "--agent", self.ACP_AGENT],
            # Ask the Rust runtime for a panic backtrace on stderr; it lands in
            # bridge.log via the stderr relay. Zero cost when nothing panics.
            "env": {"RUST_BACKTRACE": "1"},
        }

    def check_auth(self):
        return _check_auth()

    def login_visible(self):
        return _do_login_visible()

    def find_binary(self):
        return _find_kiro()

    def model_rates(self):
        return get_model_rates()

    def read_default_model(self):
        return read_kiro_default_model()

    def write_default_model(self, model_id):
        return write_kiro_default_model(model_id)

    def version(self):
        return get_kiro_version()

    def invalidate_version_cache(self):
        invalidate_kiro_version_cache()

    def changelog(self, version):
        return get_kiro_changelog(version)

    def capture_changelog(self):
        return capture_changelog_cache()

    def title_command(self, prompt):
        kiro = _find_kiro()
        return [kiro, "chat", "--no-interactive", prompt] if kiro else None

    def usage_command(self):
        kiro = _find_kiro()
        return [kiro, "chat", "--no-interactive", "/usage"] if kiro else None


class ClaudeCodeBackend(Backend):
    """Claude Code through the official ACP adapter (`claude-agent-acp`).

    The adapter bundles its own Claude Code runtime and shares the login in
    `~/.claude`. Instructions and skills live in `.claude/`; `.mcp.json` and
    `.claude/settings.json` are generated from .kiro by
    .kiro/hooks/sync_claude_config.py.
    """

    name = "claude-code"
    display_name = "Claude Code"

    ADAPTER_NAME = "@agentclientprotocol/claude-agent-acp"
    ADAPTER_VERSION = "0.94.0"
    ADAPTER_PACKAGE = ADAPTER_NAME + "@" + ADAPTER_VERSION

    # One adapter install per process, shared by every tab.
    _install_lock = threading.Lock()
    _install_started = False

    # Agent stderr carries routine adapter diagnostics, not user-facing errors.
    stderr_to_ui = False
    # session/load works on a fresh connection; no scratch session/new needed.
    uses_scratch_session_for_load = False
    # Sidebar, history replay, and delete go through ACP session/* methods.
    lists_sessions_over_acp = True
    # Run without permission prompts, matching kiro's --trust-all-tools.
    session_mode = "bypassPermissions"
    session_mode_fallback = "auto"
    # Model selection is a session config option, not session/set_model.
    model_config_option = "model"
    model_pref_key = "lastModelId:claude-code"
    effort_config_option = "effort"
    effort_pref_key = "lastEffort:claude-code"
    ultracode_keyword = "ultracode"
    usage_via_session = True
    # Notices arrive as `notice` updates (else as transcript text); subagent
    # text is replayed into reopened history (else dropped from replay); the
    # asyncTasks capability streams background workflow state and spend.
    client_capabilities_extra = {
        "session": {"notices": {}},
        "_meta": {
            "subagent-transcript": True,
            "jetbrains": {"air": {"version": 1, "capabilities": ["asyncTasks"]}},
        },
    }
    # Per-session Claude Code settings: advise a small workflow size, and ask
    # before any Workflow launch (the ask rule reaches the client even in bypass mode).
    session_meta = {"claudeCode": {"options": {"settings": {
        "workflowSizeGuideline": "small",
        "permissions": {"ask": ["Workflow"]},
    }}}}
    auth_error_hint = (
        "Claude Code is not signed in. Run `claude` in a terminal and use /login "
        "(or `claude auth login`), then click Reconnect."
    )

    _AUTH_TTL = 30.0
    # Model value meaning "the account's default"; stored as no `model` key.
    _ACCOUNT_DEFAULT_MODEL = "default"

    def __init__(self):
        self._auth_cache = None  # (timestamp, result)

    @staticmethod
    def user_settings_path():
        """Claude Code's user settings file, which the adapter reads `model` from for new sessions."""
        config_dir = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
        return Path(config_dir) / "settings.json"

    @classmethod
    def _read_user_settings(cls):
        """User settings dict; {} when the file is missing; None when it exists but cannot be parsed."""
        path = cls.user_settings_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError) as e:
            logger.warning("claude-code: cannot read %s: %s", path, e)
            return None
        return data if isinstance(data, dict) else None

    def read_default_model(self):
        """The `model` from Claude Code's user settings, or the account default when unset."""
        settings = self._read_user_settings()
        if settings is None:
            return None
        model = settings.get("model")
        return model if isinstance(model, str) and model.strip() else self._ACCOUNT_DEFAULT_MODEL

    def write_default_model(self, model_id):
        """Set `model` in Claude Code's user settings, keeping every other key.

        Global, like kiro's chat.defaultModel: it also applies to the `claude`
        CLI. Choosing the account default removes the key. An unparsable
        settings file is left untouched.
        """
        if not model_id:
            return False
        settings = self._read_user_settings()
        if settings is None:
            return False
        if model_id == self._ACCOUNT_DEFAULT_MODEL:
            settings.pop("model", None)
        else:
            settings["model"] = model_id
        path = self.user_settings_path()
        tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            logger.error("claude-code: writing default model to %s failed: %s", path, e)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        logger.info("claude-code: default model set to %s", model_id)
        return True

    @staticmethod
    def _find_npx():
        return shutil.which("npx")

    @staticmethod
    def find_cli():
        """Installed `claude` CLI, used for auth status and login only."""
        found = shutil.which("claude")
        if found:
            return found
        fallback = Path(os.environ.get("USERPROFILE", "")) / ".local" / "bin" / "claude.exe"
        return str(fallback) if fallback.exists() else None

    @classmethod
    def adapter_dir(cls):
        """App-owned install location for the pinned adapter version."""
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Hyperagent"
        return base / ("claude-agent-acp-" + cls.ADAPTER_VERSION)

    @classmethod
    def adapter_entry(cls):
        return (cls.adapter_dir() / "node_modules" / "@agentclientprotocol"
                / "claude-agent-acp" / "dist" / "index.js")

    @classmethod
    def _install_adapter_in_background(cls):
        """Install the pinned adapter once so later launches skip npx."""
        with cls._install_lock:
            if cls._install_started:
                return
            cls._install_started = True
        threading.Thread(target=cls._install_adapter, daemon=True).start()

    @classmethod
    def _install_adapter(cls):
        npm = shutil.which("npm")
        if not npm:
            logger.warning("claude-code: npm not found; adapter stays on npx launches")
            return
        target = cls.adapter_dir()
        logger.info("claude-code: installing %s into %s", cls.ADAPTER_PACKAGE, target)
        started = time.time()
        try:
            target.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                [npm, "install", "--prefix", str(target), "--no-audit", "--no-fund",
                 "--loglevel=error", cls.ADAPTER_PACKAGE],
                capture_output=True, text=True, timeout=600,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("claude-code: adapter install failed: %s", e)
            return
        if proc.returncode != 0 or not cls.adapter_entry().exists():
            logger.error("claude-code: adapter install failed (exit=%d): %s",
                         proc.returncode, (proc.stderr or proc.stdout or "")[-500:])
            return
        logger.info("claude-code: adapter installed in %.1fs", time.time() - started)

    def spawn_command(self):
        connectors = bool(read_prefs().get("claudeAiConnectors"))
        env = {
            # Todo tools are off by default in SDK sessions; they drive the
            # ACP `plan` updates that populate the task panel.
            "CLAUDE_CODE_ENABLE_TODO_TOOLS": "1",
            # Org claude.ai connectors load into every session unless disabled.
            "ENABLE_CLAUDEAI_MCP_SERVERS": "true" if connectors else "false",
        }
        # Launch the locally installed adapter with node: npx re-resolves the
        # package on every launch, which has taken from 3s to over 100s.
        node = shutil.which("node")
        entry = self.adapter_entry()
        if node and entry.exists():
            return {"argv": [node, str(entry)], "env": env}

        self._install_adapter_in_background()
        npx = self._find_npx()
        if not npx:
            logger.error("claude-code: npx not found on PATH (Node.js is required for the ACP adapter)")
            return None
        logger.info("claude-code: local adapter not installed yet, launching via npx")
        return {"argv": [npx, "-y", self.ADAPTER_PACKAGE], "env": env}

    def check_auth(self):
        now = time.time()
        if self._auth_cache and now - self._auth_cache[0] < self._AUTH_TTL:
            return self._auth_cache[1]
        result = self._read_auth_status()
        # Only cache definitive answers so a transient failure is retried.
        if result is not AUTH_UNKNOWN:
            self._auth_cache = (now, result)
        return result

    def _read_auth_status(self):
        cli = self.find_cli()
        if not cli:
            logger.warning("claude-code: claude CLI not found; auth state unknown")
            return AUTH_UNKNOWN
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            proc = subprocess.run([cli, "auth", "status"], capture_output=True,
                                  text=True, startupinfo=si, timeout=15)
            status = json.loads(proc.stdout or "{}")
        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            logger.warning("claude-code: auth status check failed: %s", e)
            return AUTH_UNKNOWN
        logged_in = bool(status.get("loggedIn"))
        logger.info("claude-code: auth status loggedIn=%s method=%s",
                    logged_in, status.get("authMethod"))
        return AUTH_OK if logged_in else AUTH_NO

    def invalidate_auth_cache(self):
        self._auth_cache = None

    def login_visible(self):
        cli = self.find_cli()
        if not cli:
            logger.error("claude-code: login requested but claude CLI not found")
            return False
        try:
            proc = subprocess.Popen([cli, "auth", "login"],
                                    creationflags=subprocess.CREATE_NEW_CONSOLE)
            proc.wait(timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.error("claude-code: login failed: %s", e)
            return False
        self.invalidate_auth_cache()
        logger.info("claude-code: login exit=%d", proc.returncode)
        return proc.returncode == 0

    def title_command(self, prompt):
        # One-shot low-effort haiku call with no tools, MCP servers, skills, settings, or saved session.
        cli = self.find_cli()
        if not cli:
            return None
        return [
            cli, "-p", prompt, "--model", "haiku", "--effort", "low", "--no-session-persistence",
            "--strict-mcp-config", "--disable-slash-commands", "--tools", "",
            "--setting-sources", "",
            "--system-prompt", "You write short titles. Reply with only the requested title text.",
        ]


REGISTRY = {
    KiroBackend.name: KiroBackend,
    ClaudeCodeBackend.name: ClaudeCodeBackend,
}

_active = None
_active_lock = threading.Lock()


def get_backend():
    """Return the process-wide backend selected by preferences.json.

    An unknown `backend` value logs a warning and falls back to the default
    rather than failing startup.
    """
    global _active
    with _active_lock:
        if _active is None:
            requested = read_prefs().get("backend") or DEFAULT_BACKEND
            cls = REGISTRY.get(requested)
            if cls is None:
                logger.warning("backend %r is not registered; using %r", requested, DEFAULT_BACKEND)
                cls = REGISTRY[DEFAULT_BACKEND]
            _active = cls()
            logger.info("backend selected: %s", _active.name)
        return _active
