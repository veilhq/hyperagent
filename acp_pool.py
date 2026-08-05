"""
ACPClientPool — manages multiple ACPClient instances for the tabbed UI.

One pool per application lifetime. Handles tab creation, switching, closing,
and preference persistence for tab/session state.
"""

import json
import threading
import uuid

from helpers import PREFS_FILE, logger
from acp_client import ACPClient


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
                client = self._clients.get(tab_id)
                if client:
                    try:
                        client._push_models_event()
                    except Exception as e:
                        logger.debug("switch_tab: _push_models_event failed: %s", e)
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
        """Persist session titles for sidebar display."""
        prefs = self._load_prefs()
        titles = prefs.get("sessionTitles", {})
        for client in self._clients.values():
            if client._session_id and hasattr(client, '_tab_title'):
                titles[client._session_id] = client._tab_title
        prefs["sessionTitles"] = titles
        prefs.pop("tabs", None)
        prefs.pop("active_tab", None)
        self._save_prefs(prefs)
