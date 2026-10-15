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

import threading

from helpers import (
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


REGISTRY = {
    KiroBackend.name: KiroBackend,
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
