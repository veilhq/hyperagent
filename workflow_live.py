"""Live per-agent readout for a running workflow, read from the transcript files Claude Code writes as the
run goes: subagents/workflows/wf_<id>/journal.jsonl plus one agent-<id>.jsonl per agent."""

import json
import time
from datetime import datetime
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"
SUMMARY_KEYS = ("command", "file_path", "path", "pattern", "description", "query", "url")
SUMMARY_CHARS = 140
PROMPT_CHARS = 600
NARRATION_CHARS = 240
RESULT_CHARS = 600
SCRIPT_CHARS = 60000
LAUNCH_SLACK_S = 2
_cache = {}


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return 0


def _created(path):
    """Creation time on Windows; on other platforms st_ctime is the last metadata change, a later bound."""
    try:
        return path.stat().st_ctime
    except OSError:
        return 0


def locate_run(session_id, name, root=None, claimed=(), since=None):
    """The wf_<id> directory of a session's workflow, skipping runs in claimed: the newest whose script
    file is exactly <name>-wf_<id>.js, else the newest created at or after since (epoch seconds).
    None when no run qualifies yet, so the caller retries on the next update."""
    if not session_id:
        return None
    taken = {str(path) for path in claimed if path}
    runs = []
    for found in (root or PROJECTS).glob("*/%s/subagents/workflows/wf_*" % session_id):
        if found.is_dir() and str(found) not in taken:
            runs.append(found)
    runs.sort(key=_mtime, reverse=True)
    if name:
        for run in runs:
            if (run.parents[2] / "workflows" / "scripts" / ("%s-%s.js" % (name, run.name))).is_file():
                return run
    if since is not None:
        runs = [run for run in runs if _created(run) >= since - LAUNCH_SLACK_S]
    return runs[0] if runs else None


def _lines(path):
    """Parsed JSON lines of a file, cached by size and mtime so an unchanged agent is not re-read."""
    try:
        stat = path.stat()
    except OSError:
        return []
    key = str(path)
    hit = _cache.get(key)
    if hit and hit[0] == (stat.st_size, stat.st_mtime):
        return hit[1]
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    _cache[key] = ((stat.st_size, stat.st_mtime), rows)
    return rows


def _stamp(value):
    """Epoch milliseconds of an ISO timestamp, or None."""
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except (AttributeError, ValueError):
        return None


def _summary(tool_input):
    if not isinstance(tool_input, dict):
        return ""
    for key in SUMMARY_KEYS:
        if tool_input.get(key):
            return " ".join(str(tool_input[key]).split())[:SUMMARY_CHARS]
    return ""


def _text(content):
    """Plain text of a message content value (string or block list)."""
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def _result_text(value):
    return value if isinstance(value, str) else json.dumps(value)


def _clip(text, limit):
    return " ".join((text or "").split())[:limit]


def _prompt(rows):
    """The task text an agent was given: the computed-task message when present, else the first user text."""
    first = None
    for row in rows:
        if row.get("type") != "user":
            continue
        text = _text((row.get("message") or {}).get("content"))
        if not text.strip():
            continue
        first = first or text
        if "computed task" in text[:200]:
            return _clip(text.split("follows:", 1)[-1], PROMPT_CHARS)
    return _clip(first, PROMPT_CHARS)


def _agent(base, started, finished, now_ms):
    """One agent's row from its transcript."""
    rows = _lines(base / ("agent-%s.jsonl" % started["agentId"]))
    tools = {}
    tool_ms = {}
    opened = {}
    out_tokens = turns = ctx = last_out = errors = blocked = thinking = 0
    cache_read = cache_total = 0
    model = last_tool = last_summary = narration = stop = effort = None
    first = last = None
    for row in rows:
        stamp = _stamp(row.get("timestamp"))
        if stamp is not None:
            first = stamp if first is None else first
            last = stamp
        kind = row.get("type")
        if kind == "api-request":
            effort = ((row.get("params") or {}).get("output_config") or {}).get("effort") or effort
            continue
        message = row.get("message") or {}
        if kind == "user":
            for block in message.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                began = opened.pop(block.get("tool_use_id"), None)
                if began and stamp is not None:
                    tool_ms[began[0]] = tool_ms.get(began[0], 0) + stamp - began[1]
                if block.get("is_error"):
                    errors += 1
                    if "Blocked" in _text(block.get("content"))[:120]:
                        blocked += 1
            continue
        if kind != "assistant":
            continue
        model = message.get("model") or model
        stop = message.get("stop_reason") or stop
        usage = message.get("usage") or {}
        turns += 1
        last_out = usage.get("output_tokens", 0)
        out_tokens += last_out
        fresh = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        ctx = fresh + usage.get("cache_read_input_tokens", 0)
        cache_read += usage.get("cache_read_input_tokens", 0)
        cache_total += ctx
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "thinking":
                thinking += 1
            elif block.get("type") == "text" and block.get("text", "").strip():
                narration = _clip(block["text"], NARRATION_CHARS)
            elif block.get("type") == "tool_use":
                name = block.get("name")
                tools[name] = tools.get(name, 0) + 1
                last_tool, last_summary = name, _summary(block.get("input"))
                if stamp is not None and block.get("id"):
                    opened[block["id"]] = (name, stamp)
    done = finished is not None
    end = last if done else now_ms
    return {
        "id": started["agentId"], "label": started.get("label"), "phase": started.get("phase"),
        "state": "done" if done else "running", "model": model, "effort": effort,
        "durationMs": end - first if first is not None and end is not None else None,
        "turns": turns, "toolCalls": sum(tools.values()), "tools": tools, "toolMs": tool_ms,
        "lastTool": last_tool, "lastToolSummary": last_summary,
        "errors": errors, "blocked": blocked, "thinkingTurns": thinking, "stopReason": stop,
        "cacheHit": round(cache_read / cache_total, 3) if cache_total else None,
        "narration": narration, "prompt": _prompt(rows),
        "result": _clip(started.get("resultText"), RESULT_CHARS) if done else None,
        # The run reports an agent's tokens as its latest turn's full context plus that turn's output.
        "tokens": ctx + last_out, "outTokens": out_tokens,
    }


def _script(run_dir):
    """Name and text of the workflow script written for this run, or (None, None)."""
    scripts = Path(run_dir).parents[2] / "workflows" / "scripts"
    for found in scripts.glob("*-%s.js" % Path(run_dir).name):
        try:
            return found.name, found.read_text(encoding="utf-8")[:SCRIPT_CHARS]
        except OSError:
            return found.name, None
    return None, None


def read_run(run_dir, now=None):
    """Agents and phases of a workflow run in launch order, or None when there is no journal yet."""
    if not run_dir:
        return None
    entries = _lines(Path(run_dir) / "journal.jsonl")
    if not entries:
        return None
    now_ms = int((now if now is not None else time.time()) * 1000)
    finished = {}
    for row in entries:
        if row.get("type") == "result" and row.get("agentId"):
            finished[row["agentId"]] = row.get("result")
    agents = []
    for row in entries:
        if row.get("type") == "started" and row.get("agentId"):
            done = row["agentId"] in finished
            agents.append(_agent(Path(run_dir), dict(row, resultText=_result_text(finished.get(row["agentId"]))
                                                     if done else None),
                                 row["agentId"] if done else None, now_ms))
    phases = []
    for agent in agents:
        if agent["phase"] and agent["phase"] not in phases:
            phases.append(agent["phase"])
    name, script = _script(run_dir)
    return {"agents": agents, "phases": phases, "scriptName": name, "script": script}
