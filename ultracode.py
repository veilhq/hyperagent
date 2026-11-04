"""Ultracode run limits: validated settings and the cost-aware preamble sent with an armed prompt."""

import json
import re
import tempfile
from pathlib import Path

MODES =("once", "stay")
DEFAULTS = {"mode": "once", "budget": 150000, "agentCap": 5, "threshold": 70}
BUDGET_RANGE = (20000, 2000000)
AGENT_CAP_RANGE = (1, 50)
THRESHOLD_RANGE = (10, 100)
# Plan-usage readings older than this are not trusted by the guard.
USAGE_MAX_AGE = 3600
# Safety ceiling on the workflow result sent to the page; the full-view overlay shows everything under it.
RESULT_MAX_CHARS = 300000


def _clamp(value, low, high, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def normalize(raw):
    """Return a complete, in-range settings dict from a possibly partial or invalid one."""
    raw = raw if isinstance(raw, dict) else {}
    mode = raw.get("mode")
    return {
        "mode": mode if mode in MODES else DEFAULTS["mode"],
        "budget": _clamp(raw.get("budget"), *BUDGET_RANGE, DEFAULTS["budget"]),
        "agentCap": _clamp(raw.get("agentCap"), *AGENT_CAP_RANGE, DEFAULTS["agentCap"]),
        "threshold": _clamp(raw.get("threshold"), *THRESHOLD_RANGE, DEFAULTS["threshold"]),
    }


def usage_readings(windows, fetched_at, rate_limit, now):
    """[(label, percent)] of plan usage still worth trusting: unexpired windows from a recent
    /usage pull, plus a recent pushed rate-limit report (utilization 0-1; rejected counts as 100)."""
    readings = []
    if fetched_at and now - fetched_at < USAGE_MAX_AGE:
        for window in windows or []:
            resets_at = window.get("resetsAt")
            if isinstance(window.get("utilization"), (int, float)) and (not resets_at or resets_at > now):
                readings.append((window.get("label") or "plan", float(window["utilization"])))
    if rate_limit and now - (rate_limit.get("receivedAt") or 0) < USAGE_MAX_AGE:
        label = (rate_limit.get("rateLimitType") or "plan").replace("_", " ")
        if rate_limit.get("status") == "rejected":
            readings.append((label, 100.0))
        elif isinstance(rate_limit.get("utilization"), (int, float)):
            readings.append((label, rate_limit["utilization"] * 100.0))
    return readings


def guard_message(threshold, readings):
    """Why ultracode should not run ("weekly usage is 82%, above your 70% guard"), or None when clear."""
    over = [(label, pct) for label, pct in readings if pct >= threshold]
    if not over:
        return None
    label, pct = max(over, key=lambda r: r[1])
    return "%s usage is %.0f%%, at or above your %d%% guard" % (label, pct, threshold)


def breach_message(limits, total_tokens, agent_count):
    """Why a running workflow must be stopped ("token spend 160k passed the 150k budget"), or None while within limits."""
    if isinstance(total_tokens, (int, float)) and total_tokens >= limits["budget"]:
        return "token spend %dk reached the %dk budget" % (round(total_tokens / 1000), round(limits["budget"] / 1000))
    if isinstance(agent_count, int) and agent_count > limits["agentCap"]:
        return "%d agents passed the cap of %d" % (agent_count, limits["agentCap"])
    return None


def read_output(path):
    """Parsed workflow output file (agentCount, workflowProgress, result), or None when it is missing,
    unreadable, or not yet written."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read_agent_count(path):
    """agentCount from a workflow output file, or None when it is unavailable."""
    count = (read_output(path) or {}).get("agentCount")
    return count if isinstance(count, int) else None


def locate_output(task_id, root=None):
    """Path of a workflow's output file under the Claude temp directory, or None. The adapter names
    the path only in the completed update, so a running workflow's file is found by its task id."""
    root = root or Path(tempfile.gettempdir()) / "claude"
    for found in root.glob("*/*/tasks/%s.output" % task_id):
        return str(found)
    return None


def note_activity(task, label, total_tokens):
    """Attribute each token increase to the agent label last reported, building a live per-label
    readout for runs whose output file has no agent rows yet."""
    if not isinstance(total_tokens, (int, float)):
        return
    if label:
        task["activityLabel"] = label
    current = task.get("activityLabel")
    delta = total_tokens - task.get("activityTokens", 0)
    task["activityTokens"] = total_tokens
    if not current:
        return
    rows = task.setdefault("activity", [])
    row = next((r for r in rows if r["label"] == current), None)
    if row is None:
        row = {"label": current, "tokens": 0}
        rows.append(row)
    row["tokens"] += max(delta, 0)


def card_state(task, output, live=None):
    """Page payload for one workflow card: header facts from the task updates, phases and agent rows
    from the live transcripts while the run is active, else from the output file."""
    usage = task.get("usage") or {}
    progress = (output or {}).get("workflowProgress") or []
    agents = [{"label": row.get("label"), "phase": row.get("phaseTitle"), "state": row.get("state"),
               "tokens": row.get("tokens"), "durationMs": row.get("durationMs")}
              for row in progress if row.get("type") == "workflow_agent"]
    phases = [row.get("title") for row in progress if row.get("type") == "workflow_phase"]
    if live and live["agents"] and (task.get("state") not in TERMINAL_STATES or not agents):
        agents, phases = live["agents"], live["phases"] or phases
    result = (output or {}).get("result")
    return {
        "id": task.get("id"),
        "name": task.get("name"),
        "description": task.get("description") or task.get("summary"),
        "state": task.get("state") or "running",
        "stopping": bool(task.get("stopRequested")),
        "canStop": bool(task.get("canStop")),
        "lastTool": task.get("lastToolName"),
        "tokens": usage.get("totalTokens"),
        "toolUses": usage.get("toolUses"),
        "durationMs": usage.get("durationMs"),
        "limits": task.get("limits"),
        "phases": phases,
        "script": {"name": live.get("scriptName"), "text": live.get("script")} if live and live.get("script") else None,
        "agents": agents,
        "agentCount": (output or {}).get("agentCount"),
        "activity": [dict(row, current=row["label"] == task.get("activityLabel")) for row in task.get("activity", [])],
        "result": json.dumps(result, indent=1)[:RESULT_MAX_CHARS] if result is not None else None,
    }


def summarize_script(script):
    """Static facts about a workflow script for the approval card: name, description, phase titles,
    the number of agent() call sites, and whether loops or fan-out make the real agent count larger."""
    def first(pattern):
        match = re.search(pattern, script)
        return match.group(1) if match else ""

    quoted = r"""['"`]([^'"`]+)['"`]"""
    return {
        "name": first(r"name:\s*" + quoted),
        "description": first(r"description:\s*" + quoted),
        "phases": re.findall(r"title:\s*" + quoted, script),
        "agentCalls": len(re.findall(r"\bagent\(", script)),
        "fansOut": bool(re.search(r"\b(parallel|pipeline|while|for)\b|\.map\(", script)),
    }


TERMINAL_STATES = ("completed", "stopped", "failed", "cancelled")


def run_record(task, readings_after, ended_at):
    """One cost-log row for a finished workflow: spend, duration, agents, limits, and plan usage before/after."""
    usage = task.get("usage") or {}
    return {
        "taskId": task.get("id"),
        "name": task.get("name"),
        "state": task.get("state"),
        "stoppedBy": task.get("stopRequested"),
        "tokens": usage.get("totalTokens"),
        "toolUses": usage.get("toolUses"),
        "durationMs": usage.get("durationMs"),
        "agents": read_agent_count(task["outputFilePath"]) if task.get("outputFilePath") else None,
        "limits": task.get("limits"),
        "usageBefore": task.get("usageBefore"),
        "usageAfter": [[label, pct] for label, pct in readings_after],
        "startedAt": task.get("startedAt"),
        "endedAt": ended_at,
    }


def append_run_record(path, record):
    """Append one JSON line to the cost log; failures are returned as False, never raised."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        return False
    return True


def budget_directive(budget):
    """Token-target directive the harness reads from a prompt, e.g. 150000 -> "+150k"."""
    return "+%dk" % round(budget / 1000)


def build_preamble(settings):
    """Prompt text that frames the run as cost-limited, replacing ultracode's "cost is no constraint" stance."""
    return (
        "Usage on this account is limited and shared, so cost matters for this run. "
        "Hard limits: at most %d agents in total and a token budget of %s. "
        "Prefer one cheap pass over exhaustive fan-out, use low effort for mechanical agents, "
        "and do not exceed either limit; if the task seems to need more, stop and say so."
        % (settings["agentCap"], budget_directive(settings["budget"]))
    )


def wire_text(keyword, settings, text):
    """Compose the prompt text for an armed turn: keyword, preamble, then the user's text."""
    parts = [keyword, build_preamble(settings)]
    if text:
        parts.append(text)
    return "\n\n".join(parts)
