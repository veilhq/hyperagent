"""Checks for workflow_live: locating a run directory and reading per-agent rows from transcripts."""

import json
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import workflow_live  # noqa: E402


def write_lines(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def assistant(stamp, out, ctx, *tool_uses):
    content = [{"type": "tool_use", "name": n, "input": i} for n, i in tool_uses]
    return {"type": "assistant", "timestamp": stamp, "message": {
        "model": "claude-sonnet-5-5", "content": content,
        "usage": {"output_tokens": out, "input_tokens": 1, "cache_creation_input_tokens": ctx, "cache_read_input_tokens": 0}}}


def _future():
    """A launch time later than any directory created so far, so the creation-time fallback finds nothing."""
    return time.time() + 60


def main():
    root = pathlib.Path(tempfile.mkdtemp())
    session = root / "proj" / "sess"
    run = session / "subagents" / "workflows" / "wf_abc"
    run.mkdir(parents=True)
    (session / "workflows" / "scripts").mkdir(parents=True)
    (session / "workflows" / "scripts" / "my-flow-wf_abc.js").write_text("//", encoding="utf-8")
    assert workflow_live.locate_run("sess", "my-flow", root) == run
    assert workflow_live.locate_run("nope", "my-flow", root) is None
    assert workflow_live.read_run(run) is None

    # A second run in the same session: before its script lands, the first (claimed) run is never picked,
    # and a name that is a prefix of an older script's name does not match it.
    second = session / "subagents" / "workflows" / "wf_def"
    assert workflow_live.locate_run("sess", "my", root, claimed=[run]) is None
    assert workflow_live.locate_run("sess", "my-flow", root, claimed=[run], since=_future()) is None
    second.mkdir()
    assert workflow_live.locate_run("sess", "my", root, claimed=[run]) == second
    (session / "workflows" / "scripts" / "my-flow-wf_def.js").write_text("//", encoding="utf-8")
    assert workflow_live.locate_run("sess", "my-flow", root, claimed=[run], since=_future()) == second
    assert workflow_live.locate_run("sess", "my-flow", root, claimed=[second]) == run
    (session / "workflows" / "scripts" / "my-flow-wf_def.js").unlink()
    second.rmdir()

    write_lines(run / "journal.jsonl", [
        {"type": "launched"},
        {"type": "started", "agentId": "a1", "label": "one", "phase": "Build"},
        {"type": "started", "agentId": "a2", "label": "two", "phase": "Test"},
        {"type": "result", "agentId": "a1", "result": {"summary": "all good"}},
    ])
    first = assistant("2026-11-02T21:00:05.000Z", 10, 100, ("Bash", {"command": "ls  -la"}), ("Read", {"file_path": "x.py"}))
    first["message"]["content"][0]["id"] = "t1"
    first["message"]["content"][1]["id"] = "t2"
    first["message"]["content"].append({"type": "thinking", "thinking": "hm"})
    first["message"]["content"].append({"type": "text", "text": "Checking  the files."})
    results = {"type": "user", "timestamp": "2026-11-02T21:00:07.000Z", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
        {"type": "tool_result", "tool_use_id": "t2", "is_error": True,
         "content": "<tool_use_error>Blocked: sleep 9</tool_use_error>"}]}}
    write_lines(run / "agent-a1.jsonl", [
        {"type": "user", "timestamp": "2026-11-02T21:00:00.000Z", "message": {"content": "[Workflow harness - computed task] x follows:\n  Do the thing"}},
        {"type": "api-request", "params": {"output_config": {"effort": "low"}}},
        first, results,
        assistant("2026-11-02T21:00:09.000Z", 5, 120, ("Bash", {"command": "pwd"})),
    ])
    write_lines(run / "agent-a2.jsonl", [
        {"type": "user", "timestamp": "2026-11-02T21:00:01.000Z", "message": {}},
    ])
    live = workflow_live.read_run(run, now=1793653210)
    assert live["phases"] == ["Build", "Test"]
    one, two = live["agents"]
    assert one["state"] == "done" and one["label"] == "one" and one["turns"] == 2 and one["toolCalls"] == 3
    assert one["tools"] == {"Bash": 2, "Read": 1} and one["lastTool"] == "Bash" and one["lastToolSummary"] == "pwd"
    assert one["tokens"] == 126 and one["outTokens"] == 15 and one["durationMs"] == 9000, one
    assert one["effort"] == "low" and one["errors"] == 1 and one["blocked"] == 1 and one["thinkingTurns"] == 1
    assert one["toolMs"] == {"Bash": 2000, "Read": 2000} and one["narration"] == "Checking the files."
    assert one["prompt"] == "Do the thing" and "all good" in one["result"] and one["cacheHit"] == 0.0
    assert live["script"] == "//" and live["scriptName"] == "my-flow-wf_abc.js"
    assert two["state"] == "running" and two["turns"] == 0 and two["lastTool"] is None
    print("ok")


main()
