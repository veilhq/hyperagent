"""Checks the ultracode toggle: keyword on the wire only when on, and only for backends that support it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import acp_client  # noqa: E402
import acp_ultracode  # noqa: E402
import backends  # noqa: E402
import ultracode  # noqa: E402


class Stub:
    """Just enough of ACPClient for prompt() and set_ultracode()."""
    _tab_ctx = lambda self: ""  # noqa: E731
    _build_prompt_blocks = staticmethod(acp_client.ACPClient._build_prompt_blocks)
    set_ultracode = acp_client.ACPClient.set_ultracode
    set_ultracode_limits = acp_client.ACPClient.set_ultracode_limits
    _ultracode_limits = acp_client.ACPClient._ultracode_limits
    _ultracode_guard = acp_client.ACPClient._ultracode_guard
    _push_guard_notice = acp_client.ACPClient._push_guard_notice
    _capture_async_task = acp_client.ACPClient._capture_async_task
    _plan_readings = acp_client.ACPClient._plan_readings
    _push_workflow_card = acp_client.ACPClient._push_workflow_card
    _log_async_task = acp_client.ACPClient._log_async_task
    _watch_async_task = acp_client.ACPClient._watch_async_task
    stop_async_task = acp_client.ACPClient.stop_async_task
    _hold_workflow_plan = acp_client.ACPClient._hold_workflow_plan
    answer_workflow_plan = acp_client.ACPClient.answer_workflow_plan
    _cancel_workflow_plans = acp_client.ACPClient._cancel_workflow_plans
    prompt = acp_client.ACPClient.prompt

    def __init__(self, backend):
        self._backend = backend
        self._ultracode = False
        self._ultracode_mode = "stay"
        self._ultracode_budget = 150000
        self._ultracode_agent_cap = 5
        self._ultracode_threshold = 70
        self._rate_limit = None
        self.notices = []
        self._async_tasks = {}
        self._state = "ready"
        self._session_id = "s1"
        self._cancelled = type("E", (), {"clear": lambda s: None})()
        self.pushed = []
        self.sent = []

    def _push_effort_event(self):
        self.pushed.append(self._ultracode)

    _on_prompt_done = None

    def _push_state(self):
        pass

    def _push_js(self, event, data):
        if event == "__acpNotice":
            self.notices.append(data)

    def _request(self, method, params, cb=None):
        self.sent.append(params["prompt"])
        self._state = "ready"
        return 1


def main():
    saved = []
    def fake_update_prefs(mutator):
        prefs = {}
        mutator(prefs)
        saved.append(prefs)

    acp_client.update_prefs = fake_update_prefs
    acp_ultracode.update_prefs = fake_update_prefs
    import pathlib, tempfile
    acp_ultracode.RUN_LOG = pathlib.Path(tempfile.mkdtemp()) / "sub" / "runs.jsonl"
    pre = ultracode.build_preamble(ultracode.DEFAULTS)
    c = Stub(backends.ClaudeCodeBackend())
    c.prompt("hello")
    assert c.sent[-1] == [{"type": "text", "text": "hello"}]
    assert c.set_ultracode(True) and c._ultracode and c.pushed == [True]
    c.prompt("hello")
    assert c.sent[-1][0]["text"] == "ultracode\n\n" + pre + "\n\nhello"
    c.prompt("", images=[{"data": "abc"}])
    assert c.sent[-1][0] == {"type": "text", "text": "ultracode\n\n" + pre} and c.sent[-1][1]["type"] == "image"
    assert c.set_ultracode(False) and not c._ultracode

    # One-shot: the keyword goes out once, then ultracode disarms.
    c.pushed.clear()
    assert c.set_ultracode_limits(mode="once") and c._ultracode_mode == "once"
    assert c.set_ultracode_limits(mode="bogus") and c._ultracode_mode == "once"
    c.set_ultracode(True)
    c.prompt("first")
    assert c.sent[-1][0]["text"] == "ultracode\n\n" + pre + "\n\nfirst" and not c._ultracode
    c.prompt("second")
    assert c.sent[-1] == [{"type": "text", "text": "second"}]

    k = Stub(backends.KiroBackend())
    assert not k.set_ultracode(True) and not k._ultracode
    assert not k.set_ultracode_limits(mode="once")

    # Limits: validated, persisted as the defaults, and worded into the preamble.
    saved.clear()
    assert c.set_ultracode_limits(budget=80000, agent_cap=3)
    assert (c._ultracode_budget, c._ultracode_agent_cap) == (80000, 3)
    assert saved == [{"ultracode": {"mode": "once", "budget": 80000, "agentCap": 3, "threshold": 70}}]
    c.set_ultracode_limits(budget=1, agent_cap=999)
    assert (c._ultracode_budget, c._ultracode_agent_cap) == (20000, 50)
    c.set_ultracode_limits(budget="junk", agent_cap=None)
    assert c._ultracode_budget == 150000
    assert ultracode.normalize(None) == ultracode.DEFAULTS
    assert c.set_ultracode_limits(threshold=5) and c._ultracode_threshold == 10
    c.set_ultracode_limits(threshold=70)

    # Usage guard: refuses to arm, and disarms an armed tab before sending, at or above the threshold.
    import time
    now = time.time()
    hot = [("weekly", 82.0)]
    assert ultracode.guard_message(70, hot) == "weekly usage is 82%, at or above your 70% guard"
    assert ultracode.guard_message(90, hot) is None and ultracode.guard_message(70, []) is None
    windows = [{"label": "weekly", "utilization": 50.0, "resetsAt": now + 100},
               {"label": "five-hour", "utilization": 95.0, "resetsAt": now - 100},
               {"label": "weekly Opus", "utilization": 71.0, "resetsAt": None}]
    assert ultracode.usage_readings(windows, now - 10, None, now) == [("weekly", 50.0), ("weekly Opus", 71.0)]
    assert ultracode.usage_readings(windows, now - 7200, None, now) == []
    assert ultracode.usage_readings([], None, {"status": "rejected", "receivedAt": now}, now) == [("plan", 100.0)]
    assert ultracode.usage_readings([], None, {"utilization": 0.8, "rateLimitType": "five_hour", "receivedAt": now}, now) == [("five hour", 80.0)]
    assert ultracode.usage_readings([], None, {"utilization": 0.8, "receivedAt": now - 7200}, now) == []

    acp_client.read_prefs = lambda: {}
    acp_ultracode.read_prefs = lambda: {}
    c.notices.clear()
    c.set_ultracode_limits(mode="stay")
    c._rate_limit = {"utilization": 0.9, "rateLimitType": "five_hour", "receivedAt": now}
    assert not c.set_ultracode(True) and not c._ultracode and "five hour usage is 90%" in c.notices[-1]["description"]
    c._rate_limit = None
    assert c.set_ultracode(True)
    c._rate_limit = {"utilization": 0.9, "rateLimitType": "five_hour", "receivedAt": now}
    c.prompt("x")
    assert not c._ultracode and c.sent[-1] == [{"type": "text", "text": "x"}] and c.notices[-1]["title"] == "Ultracode turned off"
    c._rate_limit = None
    c.set_ultracode_limits(mode="once")
    text = ultracode.build_preamble({"mode": "once", "budget": 80000, "agentCap": 3})
    assert "at most 3 agents" in text and "+80k" in text

    # Claude sessions carry the workflow settings; other backends carry nothing.
    meta = backends.ClaudeCodeBackend.session_meta["claudeCode"]["options"]["settings"]
    assert meta["permissions"]["ask"] == ["Workflow"] and meta["workflowSizeGuideline"] == "small"
    assert backends.KiroBackend.session_meta is None
    air = backends.ClaudeCodeBackend.client_capabilities_extra["_meta"]["jetbrains"]["air"]
    assert "asyncTasks" in air["capabilities"]

    # Async task updates fold into one record per task.
    c._capture_async_task({"asyncTaskId": "t1", "sessionUpdate": "async_task_spawned", "taskType": "workflow",
                           "name": "w", "canStop": True})
    c._capture_async_task({"asyncTaskId": "t1", "usage": {"totalTokens": 5, "toolUses": 1, "durationMs": 9, "x": 1}})
    c._capture_async_task({"asyncTaskId": "t1", "state": "stopped"})
    task = c._async_tasks["t1"]
    assert task["state"] == "stopped" and task["name"] == "w" and task["usage"] == {"totalTokens": 5, "toolUses": 1, "durationMs": 9}

    # Watchdog: limits are fixed at launch; spend or agent count past them sends one stop request.
    lim = {"budget": 100000, "agentCap": 3}
    assert ultracode.breach_message(lim, 99999, 3) is None and ultracode.breach_message(lim, None, None) is None
    assert ultracode.breach_message(lim, 100000, 1) == "token spend 100k reached the 100k budget"
    assert ultracode.breach_message(lim, 5, 4) == "4 agents passed the cap of 3"
    import json, os, tempfile
    out = os.path.join(tempfile.mkdtemp(), "t.output")
    assert ultracode.read_agent_count(out) is None
    with open(out, "w") as f:
        json.dump({"agentCount": 7}, f)
    assert ultracode.read_agent_count(out) == 7
    c.set_ultracode_limits(budget=100000, agent_cap=3)
    c.notices.clear()
    c.sent_raw = []
    c._request = lambda method, params, cb=None: c.sent_raw.append((method, params))
    c._capture_async_task({"asyncTaskId": "w1", "sessionUpdate": "async_task_spawned", "taskType": "workflow"})
    c.set_ultracode_limits(budget=500000)
    c._capture_async_task({"asyncTaskId": "w1", "usage": {"totalTokens": 99000}})
    assert not c.sent_raw
    c._capture_async_task({"asyncTaskId": "w1", "usage": {"totalTokens": 101000}})
    c._capture_async_task({"asyncTaskId": "w1", "usage": {"totalTokens": 120000}})
    assert c.sent_raw == [("_session/async_task/stop", {"sessionId": "s1", "asyncTaskId": "w1"})]
    assert c.notices[-1]["title"] == "Ultracode workflow stopped" and not c.stop_async_task("w1")
    c._capture_async_task({"asyncTaskId": "w2", "taskType": "workflow", "outputFilePath": out})
    assert c.sent_raw[-1][1]["asyncTaskId"] == "w2" and "7 agents" in c._async_tasks["w2"]["stopRequested"]
    c._capture_async_task({"asyncTaskId": "w3", "taskType": "workflow", "state": "completed", "usage": {"totalTokens": 9e9}})
    assert len(c.sent_raw) == 2
    c.set_ultracode_limits(budget=150000, agent_cap=5)
    del c._request

    # Cost log: one row per finished workflow, written once, with plan usage before and after.
    for earlier in c._async_tasks.values():
        if earlier.get("logTimer"):
            earlier["logTimer"].cancel()
    c._async_tasks.clear()
    log_path = acp_ultracode.RUN_LOG
    log_path.unlink(missing_ok=True)
    c._plan_readings = lambda: [("weekly", 41.0)]
    c._capture_async_task({"asyncTaskId": "l1", "taskType": "workflow", "name": "n"})
    c._capture_async_task({"asyncTaskId": "l1", "usage": {"totalTokens": 5000, "toolUses": 2, "durationMs": 900}})
    assert not log_path.exists()
    c._plan_readings = lambda: [("weekly", 43.0)]
    c._capture_async_task({"asyncTaskId": "l1", "state": "completed", "outputFilePath": out})
    c._capture_async_task({"asyncTaskId": "l1", "state": "completed", "outputFilePath": out})
    rows = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert (row["state"], row["tokens"], row["agents"], row["durationMs"]) == ("completed", 5000, 7, 900)
    assert row["usageBefore"] == [["weekly", 41.0]] and row["usageAfter"] == [["weekly", 43.0]]
    assert row["limits"]["budget"] == 150000 and row["stoppedBy"] is None
    # No output file yet: the first terminal update schedules a fallback write.
    c._capture_async_task({"asyncTaskId": "l2", "taskType": "workflow", "state": "stopped"})
    timer = c._async_tasks["l2"]["logTimer"]
    timer.cancel()
    c._log_async_task(c._async_tasks["l2"])
    assert json.loads(log_path.read_text().splitlines()[-1])["agents"] is None and len(log_path.read_text().splitlines()) == 2
    assert not ultracode.append_run_record(pathlib.Path(out) / "x" / "y.jsonl", {})

    # Workflow card: header facts from task updates, rows and result from the output file, pushed on each update.
    full = {"agentCount": 1, "result": [{"a": 1}], "workflowProgress": [
        {"type": "workflow_phase", "title": "Read"},
        {"type": "workflow_agent", "label": "a.txt", "phaseTitle": "Read", "state": "done", "tokens": 9, "durationMs": 5}]}
    card = ultracode.card_state({"id": "k", "name": "n", "state": "completed", "usage": {"totalTokens": 9}, "canStop": True,
                                 "limits": {"budget": 1, "agentCap": 2}}, full)
    assert card["phases"] == ["Read"] and card["agents"][0] == {"label": "a.txt", "phase": "Read", "state": "done", "tokens": 9, "durationMs": 5}
    assert card["agentCount"] == 1 and '"a": 1' in card["result"] and card["state"] == "completed" and not card["stopping"]
    bare = ultracode.card_state({"id": "k", "lastToolName": "a.txt"}, None)
    assert bare["state"] == "running" and bare["agents"] == [] and bare["result"] is None and bare["lastTool"] == "a.txt"
    live = {"id": "q"}
    for label, total in (("a", 0), ("a", 10), ("b", 25), (None, 40), ("a", 45)):
        ultracode.note_activity(live, label, total)
    assert live["activity"] == [{"label": "a", "tokens": 15}, {"label": "b", "tokens": 30}], live["activity"]
    assert [r["current"] for r in ultracode.card_state(live, None)["activity"]] == [True, False]
    root = pathlib.Path(tempfile.mkdtemp())
    (root / "proj" / "sess" / "tasks").mkdir(parents=True)
    (root / "proj" / "sess" / "tasks" / "tk1.output").write_text("{}")
    assert ultracode.locate_output("tk1", root).endswith("tk1.output") and ultracode.locate_output("nope", root) is None
    cards = []
    c._push_js = lambda ev, d: cards.append((ev, d))
    c._capture_async_task({"asyncTaskId": "cd", "taskType": "workflow", "name": "nm", "canStop": True})
    small = os.path.join(tempfile.mkdtemp(), "cd.output")
    with open(small, "w") as f:
        json.dump(full, f)
    c._async_tasks["cd"]["outputFilePath"] = small
    c._capture_async_task({"asyncTaskId": "cd", "usage": {"totalTokens": 7}})
    ev, payload = cards[-1]
    assert ev == "__acpWorkflowTask" and payload["id"] == "cd" and payload["tokens"] == 7 and payload["agentCount"] == 1
    assert payload["agents"][0]["label"] == "a.txt"
    del c._push_js

    # Plan approval: a Workflow permission request is held until answered; other tools are untouched.
    script = ("export const meta = {\n  name: 'first-lines',\n  description: 'One agent per file',\n"
              "  phases: [{ title: 'Read' }],\n}\nconst r = await parallel(files.map(f => () => agent(`x`)))")
    info = ultracode.summarize_script(script)
    assert info == {"name": "first-lines", "description": "One agent per file", "phases": ["Read"],
                    "agentCalls": 1, "fansOut": True}
    assert not ultracode.summarize_script("agent('a')")["fansOut"]
    sent_msgs, pushed_js = [], []
    c._send = lambda m: sent_msgs.append(m)
    c._push_js = lambda ev, d: pushed_js.append((ev, d))
    c._workflow_plans = {}
    opts = [{"optionId": "allow-once", "kind": "allow_once"}, {"optionId": "reject", "kind": "reject_once"}]
    wf = {"id": 7, "method": "session/request_permission", "params": {
        "toolCall": {"toolCallId": "tc1", "title": "Workflow", "rawInput": {"script": script}}, "options": opts}}
    assert c._hold_workflow_plan(wf, opts) and not sent_msgs
    ev, data = pushed_js[-1]
    assert ev == "__acpWorkflowPlan" and data["name"] == "first-lines" and data["limits"]["agentCap"] == 5
    other = {"id": 8, "params": {"toolCall": {"toolCallId": "tc2", "title": "Bash", "rawInput": {"command": "ls"}}}}
    assert not c._hold_workflow_plan(other, opts)
    assert c.answer_workflow_plan("tc1", False) and not c.answer_workflow_plan("tc1", False)
    assert sent_msgs[-1]["id"] == 7 and sent_msgs[-1]["result"]["outcome"] == {"outcome": "selected", "optionId": "reject"}
    c._hold_workflow_plan(wf, opts)
    assert c.answer_workflow_plan("tc1", True)
    assert sent_msgs[-1]["result"]["outcome"]["optionId"] == "allow-once"
    c._hold_workflow_plan(wf, opts)
    c._cancel_workflow_plans()
    assert sent_msgs[-1]["result"]["outcome"] == {"outcome": "cancelled"} and not c._workflow_plans
    assert pushed_js[-1] == ("__acpWorkflowPlanClosed", {"toolCallId": "tc1"})
    del c._send, c._push_js

    # session/new and session/load requests get the backend's meta; other methods do not.
    class Req(acp_client.ACPClient):
        def __init__(self, backend):
            self._backend = backend
            self._pending = {}
            self.out = []
            self._n = 0

        def _next_id(self):
            self._n += 1
            return self._n

        def _send(self, msg):
            self.out.append(msg)

    r = Req(backends.ClaudeCodeBackend())
    r._request("session/new", {"cwd": "x", "mcpServers": []})
    r._request("session/prompt", {"sessionId": "s"})
    assert r.out[0]["params"]["_meta"] == backends.ClaudeCodeBackend.session_meta
    assert "_meta" not in r.out[1]["params"]
    k2 = Req(backends.KiroBackend())
    k2._request("session/new", {"cwd": "x", "mcpServers": []})
    assert "_meta" not in k2.out[0]["params"]
    print("ok")


main()


