"""Ultracode and background-workflow methods for ACPClient."""

import threading
import time

import ultracode
import workflow_live
from helpers import HYPERSPACE_ROOT, logger, read_prefs, update_prefs

# One JSON line per finished background workflow: tokens, agents, duration, limits, plan usage.
RUN_LOG = HYPERSPACE_ROOT / ".logs" / "workflow-runs.jsonl"


class UltracodeMixin:
    """Ultracode limits, guard, and async workflow task tracking; relies on ACPClient state."""

    def _ultracode_limits(self):
        return {"mode": self._ultracode_mode, "budget": self._ultracode_budget,
                "agentCap": self._ultracode_agent_cap, "threshold": self._ultracode_threshold}

    def set_ultracode_limits(self, mode=None, budget=None, agent_cap=None, threshold=None):
        """Change this tab's ultracode mode, token budget, agent cap, or plan-usage guard, and save them as the defaults for new tabs."""
        if not self._backend.ultracode_keyword:
            return False
        current = self._ultracode_limits()
        for key, value in (("mode", mode), ("budget", budget), ("agentCap", agent_cap), ("threshold", threshold)):
            if value is not None:
                current[key] = value
        limits = ultracode.normalize(current)
        self._ultracode_mode = limits["mode"]
        self._ultracode_budget = limits["budget"]
        self._ultracode_agent_cap = limits["agentCap"]
        self._ultracode_threshold = limits["threshold"]
        logger.info("%sultracode limits: %s", self._tab_ctx(), limits)
        update_prefs(lambda prefs: prefs.__setitem__("ultracode", limits))
        self._push_effort_event()
        return True

    def _capture_async_task(self, update):
        """Record a background workflow's latest state from an async_task_* update."""
        task_id = update.get("asyncTaskId")
        if not task_id:
            return
        task = self._async_tasks.get(task_id)
        if task is None:
            # Limits are fixed at launch so later picker changes do not move a running workflow's ceiling.
            task = self._async_tasks[task_id] = {
                "id": task_id, "limits": self._ultracode_limits(), "startedAt": time.time(),
                "usageBefore": [[label, pct] for label, pct in self._plan_readings()],
            }
        for key in ("taskType", "name", "description", "canStop", "state", "summary", "lastToolName", "outputFilePath"):
            if update.get(key) is not None:
                task[key] = update[key]
        usage = update.get("usage")
        if isinstance(usage, dict):
            task["usage"] = {k: usage.get(k) for k in ("totalTokens", "toolUses", "durationMs")}
            ultracode.note_activity(task, update.get("lastToolName"), usage.get("totalTokens"))
        logger.debug("%sasync task %s: state=%s tokens=%s", self._tab_ctx(), task_id[:8], task.get("state"),
                     (task.get("usage") or {}).get("totalTokens"))
        if not task.get("outputFilePath") and task.get("taskType") in (None, "workflow"):
            task["outputFilePath"] = ultracode.locate_output(task_id)
        self._watch_async_task(task)
        self._push_workflow_card(task)
        if task.get("state") in ultracode.TERMINAL_STATES and task.get("taskType") in (None, "workflow"):
            if task.get("outputFilePath"):
                self._log_async_task(task)
            elif not task.get("logTimer"):
                # The completed update that names the output file can trail the first one by a moment.
                task["logTimer"] = threading.Timer(2.0, self._log_async_task, args=(task,))
                task["logTimer"].daemon = True
                task["logTimer"].start()

    def _push_workflow_card(self, task):
        """Send the page this workflow's current card state."""
        if task.get("taskType") not in (None, "workflow"):
            return
        output = ultracode.read_output(task["outputFilePath"]) if task.get("outputFilePath") else None
        if not task.get("liveDir"):
            claimed = [t.get("liveDir") for t in self._async_tasks.values() if t is not task]
            task["liveDir"] = workflow_live.locate_run(self._session_id, task.get("name"),
                                                       claimed=claimed, since=task.get("startedAt"))
            if task["liveDir"]:
                logger.info("%sworkflow %s live dir: %s", self._tab_ctx(), task["id"][:8], task["liveDir"].name)
        live = workflow_live.read_run(task["liveDir"])
        self._push_js("__acpWorkflowTask", ultracode.card_state(task, output, live))

    def _log_async_task(self, task):
        """Append a finished workflow's cost row to the run log, once."""
        if task.get("logged"):
            return
        task["logged"] = True
        record = ultracode.run_record(task, self._plan_readings(), time.time())
        record["tab"] = getattr(self, "_tab_id", None)
        if ultracode.append_run_record(RUN_LOG, record):
            logger.info("%sworkflow run logged: %s tokens=%s agents=%s", self._tab_ctx(), record["state"],
                        record["tokens"], record["agents"])

    def _watch_async_task(self, task):
        """Stop a running workflow once its spend or agent count passes the limits it launched under."""
        if task.get("stopRequested") or task.get("taskType") not in (None, "workflow"):
            return
        if task.get("state") in ("completed", "stopped", "failed", "cancelled"):
            return
        count = ultracode.read_agent_count(task["outputFilePath"]) if task.get("outputFilePath") else None
        reason = ultracode.breach_message(task["limits"], (task.get("usage") or {}).get("totalTokens"), count)
        if reason:
            self.stop_async_task(task["id"], reason)

    def stop_async_task(self, task_id, reason="user"):
        """Ask the adapter to stop a background workflow; returns False when it is unknown or already stopping."""
        task = self._async_tasks.get(task_id)
        if not task or task.get("stopRequested") or not self._session_id:
            return False
        task["stopRequested"] = reason
        logger.warning("%sstopping workflow %s: %s", self._tab_ctx(), task_id[:8], reason)
        self._request("_session/async_task/stop", {"sessionId": self._session_id, "asyncTaskId": task_id})
        self._push_workflow_card(task)
        if reason != "user":
            self._push_guard_notice("%s; the workflow was stopped" % reason, "Ultracode workflow stopped")
        return True

    def _plan_readings(self):
        """[(label, percent)] of plan usage currently worth trusting."""
        from plan_usage import PREFS_KEY
        cached = read_prefs().get(PREFS_KEY) or {}
        return ultracode.usage_readings(cached.get("windows"), cached.get("fetchedAt"), self._rate_limit, time.time())

    def _ultracode_guard(self):
        """Reason ultracode should not run at the current plan usage, or None when clear."""
        return ultracode.guard_message(self._ultracode_threshold, self._plan_readings())

    def _push_guard_notice(self, reason, title):
        self._push_js("__acpNotice", {"severity": "warning", "title": title, "description": reason})

    def set_ultracode(self, on):
        """Turn ultracode on or off for this tab; applies from the next prompt. Arming is refused above the usage guard."""
        if not self._backend.ultracode_keyword:
            return False
        if on:
            reason = self._ultracode_guard()
            if reason:
                logger.info("%sultracode arm refused: %s", self._tab_ctx(), reason)
                self._ultracode = False
                self._push_guard_notice(reason, "Ultracode not armed")
                self._push_effort_event()
                return False
        self._ultracode = bool(on)
        logger.info("%sultracode: %s", self._tab_ctx(), "on" if self._ultracode else "off")
        self._push_effort_event()
        return True
