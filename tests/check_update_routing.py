"""Checks session/update routing in ACPClient._dispatch: title, mode, ignored and unhandled types."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import acp_client  # noqa: E402
import backends  # noqa: E402


def make_client(prefs):
    client = acp_client.ACPClient()
    client._backend = backends.ClaudeCodeBackend()
    client._session_id = "sess-1"
    client._tab_id = "tab1"
    pushed = []
    client._push_js = lambda fn, data: pushed.append((fn, data))
    client._push_js_throttled = client._push_js
    client._load_prefs = lambda: prefs
    return client, pushed


def update(kind, **fields):
    return {"method": "session/update",
            "params": {"sessionId": fields.pop("sessionId", "sess-1"), "update": dict(sessionUpdate=kind, **fields)}}


def main():
    written = []
    acp_client.update_prefs = lambda mutate: written.append(mutate)

    client, pushed = make_client({})
    client._dispatch(update("session_info_update", title="Fix Login Flow"))
    assert pushed == [("__acpSessionTitle", {"title": "Fix Login Flow", "source": "agent"})], pushed
    assert client._tab_title == "Fix Login Flow" and len(written) == 1
    prefs = {}
    written[0](prefs)
    assert prefs["sessionTitles"] == {"sess-1": "Fix Login Flow"} and prefs["ownedSessions"] == ["sess-1"]

    # A renamed session keeps the user's title; another session's update is ignored.
    client, pushed = make_client({"renamedSessions": ["sess-1"]})
    client._dispatch(update("session_info_update", title="Agent Title"))
    client, pushed2 = make_client({})
    client._dispatch(update("session_info_update", title="Other", sessionId="sess-2"))
    assert pushed == [] and pushed2 == [], (pushed, pushed2)

    # Mode: names from the session result, then live current_mode_update.
    client, pushed = make_client({})
    client._capture_modes({"currentModeId": "bypassPermissions", "availableModes": [
        {"id": "bypassPermissions", "name": "Bypass Permissions"}, {"id": "plan", "name": "Plan Mode"}]})
    client._dispatch(update("current_mode_update", currentModeId="plan"))
    assert pushed[-2:] == [("__acpMode", {"id": "bypassPermissions", "name": "Bypass Permissions"}),
                           ("__acpMode", {"id": "plan", "name": "Plan Mode"})], pushed
    # kiro sets no session mode, so it shows no chip.
    client._backend = backends.KiroBackend()
    pushed.clear()
    client._dispatch(update("current_mode_update", currentModeId="x"))
    assert pushed == [], pushed

    # Ignored types reach neither the UI nor the warning log; config updates stop in Python.
    client, pushed = make_client({})
    for kind in ("compaction_update", "subagent_spawned", "session_message"):
        client._dispatch(update(kind))
    client._dispatch(update("config_option_update", configOptions=[]))
    assert not any(fn == "__acpUpdate" for fn, _ in pushed), pushed
    assert not client._warned_update_types

    # Advertised slash commands are normalized, stored, and pushed to the menu, not the stream.
    client, pushed = make_client({})
    client._dispatch(update("available_commands_update", availableCommands=[
        {"name": "usage", "description": "Show usage", "input": None},
        {"name": "mcp", "description": "MCP servers", "input": {"hint": "[reconnect]"}},
        {"description": "no name"}, "junk"]))
    expected = [{"name": "usage", "description": "Show usage", "hint": ""},
                {"name": "mcp", "description": "MCP servers", "hint": "[reconnect]"}]
    assert client._commands == expected, client._commands
    assert pushed == [("__acpCommands", {"commands": expected})], pushed
    assert not client._warned_update_types

    # Unknown types warn once per session and type, and still reach the UI.
    client, pushed = make_client({})
    client._dispatch(update("brand_new_update"))
    client._dispatch(update("brand_new_update"))
    assert client._warned_update_types == {("sess-1", "brand_new_update")}
    assert sum(1 for fn, _ in pushed if fn == "__acpUpdate") == 2

    # Handled stream types are forwarded without a warning.
    client._dispatch(update("agent_message_chunk", content={"type": "text", "text": "hi"}))
    assert ("sess-1", "agent_message_chunk") not in client._warned_update_types

    # A headless sink takes every update.
    client, pushed = make_client({})
    seen = []
    client._update_sink = seen.append
    client._dispatch(update("agent_message_chunk", content={"type": "text", "text": "x"}))
    assert len(seen) == 1 and pushed == []
    print("acp_client dispatch: ok")


if __name__ == "__main__":
    main()
