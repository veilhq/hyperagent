"""Reads conversation-compaction records from a Claude Code session transcript.

The ACP stream carries no compaction event, so the transcript's compact_boundary
records (trigger, tokens before and after, duration) are the only source.
"""
import json
import re
from datetime import datetime
from pathlib import Path


def transcript_path(session_id, cwd):
    """Path of the session transcript Claude Code keeps for a working directory."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(cwd).replace("/", "\\"))
    return Path.home() / ".claude" / "projects" / slug / f"{session_id}.jsonl"


def file_size(path):
    """Current transcript size in bytes; 0 when it does not exist yet."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_boundaries(path, offset):
    """Return (events, new_offset) for compact_boundary records appended after offset.

    Only complete lines are consumed, so a record still being written is picked up next time.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            if size < offset:
                return [], size
            handle.seek(offset)
            chunk = handle.read()
    except OSError:
        return [], offset
    end = chunk.rfind(b"\n") + 1
    events = []
    for line in chunk[:end].splitlines():
        if b"compact_boundary" not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if record.get("type") != "system" or record.get("subtype") != "compact_boundary":
            continue
        meta = record.get("compactMetadata") or {}
        events.append({
            "trigger": meta.get("trigger"),
            "preTokens": meta.get("preTokens"),
            "postTokens": meta.get("postTokens"),
            "durationMs": meta.get("durationMs"),
        })
    return events, offset + end


def _boundary_message(record):
    meta = record.get("compactMetadata") or {}
    return {
        "role": "compaction",
        "trigger": meta.get("trigger"),
        "preTokens": meta.get("preTokens"),
        "postTokens": meta.get("postTokens"),
        "durationMs": meta.get("durationMs"),
    }


def _timestamp(record):
    try:
        return datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError, AttributeError):
        return None


def _tool_result_text(block):
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text")
    return ""


def read_earlier_history(path):
    """History messages written before the transcript's last compact_boundary.

    The ACP session replay follows the parent chain from the newest boundary, so everything
    compacted away is missing from it. Each boundary becomes a compaction marker message.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return []
    records = []
    for line in raw[: raw.rfind(b"\n") + 1].splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and not record.get("isSidechain"):
            records.append(record)
    last = max((i for i, rec in enumerate(records)
                if rec.get("type") == "system" and rec.get("subtype") == "compact_boundary"), default=None)
    if last is None:
        return []
    messages = []
    tools = {}
    for record in records[: last + 1]:
        kind = record.get("type")
        if kind == "system" and record.get("subtype") == "compact_boundary":
            messages.append(_boundary_message(record))
            continue
        if kind not in ("user", "assistant") or record.get("isMeta") or record.get("isCompactSummary"):
            continue
        content = (record.get("message") or {}).get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        stamp = _timestamp(record)
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text" and (block.get("text") or "").strip():
                if kind == "user" and block["text"].lstrip().startswith(("<command-", "<local-command")):
                    continue
                role = "user" if kind == "user" else "agent"
                messages.append({"role": role, "text": block["text"].strip(), "ts": stamp})
            elif block_type == "tool_use":
                tool = {"role": "tool", "name": block.get("name") or "tool", "tool": block.get("name"),
                        "toolUseId": block.get("id", ""), "status": "completed",
                        "input": block.get("input") or None, "output": None}
                tools[tool["toolUseId"]] = tool
                messages.append(tool)
            elif block_type == "tool_result" and block.get("tool_use_id") in tools:
                tool = tools[block["tool_use_id"]]
                tool["output"] = _tool_result_text(block) or None
                if block.get("is_error"):
                    tool["status"] = "failed"
    return messages


def compact_summary_heads(path, length=120):
    """Opening text of each generated compaction summary, which the transcript stores as a user message."""
    heads = []
    try:
        with open(path, "rb") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return heads
    for line in lines:
        if b"isCompactSummary" not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        content = (record.get("message") or {}).get("content")
        if isinstance(content, list):
            content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        if record.get("isCompactSummary") and isinstance(content, str) and content.strip():
            heads.append(content.strip()[:length])
    return heads
