"""Checks compaction_watch against a synthetic Claude Code transcript."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import compaction_watch  # noqa: E402


def boundary(trigger, pre, post, ms):
    return json.dumps({
        "type": "system", "subtype": "compact_boundary",
        "compactMetadata": {"trigger": trigger, "preTokens": pre, "postTokens": post, "durationMs": ms},
    }) + "\n"


def main():
    slug_path = compaction_watch.transcript_path("abc", "C:\\Users\\Name\\Repo\\PORTAL")
    assert slug_path.parent.name == "C--Users-Name-Repo-PORTAL", slug_path
    assert slug_path.name == "abc.jsonl"

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "session.jsonl"
        assert compaction_watch.file_size(path) == 0
        assert compaction_watch.read_boundaries(path, 0) == ([], 0)

        path.write_text('{"type":"user","message":"mentions compact_boundary in text"}\n'
                        + boundary("manual", 30187, 2096, 7420), encoding="utf-8")
        events, offset = compaction_watch.read_boundaries(path, 0)
        assert events == [{"trigger": "manual", "preTokens": 30187, "postTokens": 2096, "durationMs": 7420}], events
        assert offset == compaction_watch.file_size(path)

        # Nothing new, then an unfinished line is left for the next read.
        assert compaction_watch.read_boundaries(path, offset) == ([], offset)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(boundary("auto", 160000, 9000, 12000).rstrip("\n"))
        events, partial_offset = compaction_watch.read_boundaries(path, offset)
        assert events == [] and partial_offset == offset
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n")
        events, offset = compaction_watch.read_boundaries(path, offset)
        assert [e["trigger"] for e in events] == ["auto"], events

        # A truncated transcript resets to its new end without replaying.
        path.write_text("", encoding="utf-8")
        assert compaction_watch.read_boundaries(path, offset) == ([], 0)

        # History before the last boundary survives; later turns are left to the replay.
        def row(**fields):
            return json.dumps(fields) + "\n"

        path.write_text(
            row(type="user", message={"content": "first question"}, timestamp="2026-01-01T00:00:00Z")
            + row(type="assistant", message={"content": [
                {"type": "text", "text": "first answer"},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a"}}]})
            + row(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "file body"}]})
            + row(type="user", isMeta=True, message={"content": "hidden"})
            + row(type="user", message={"content": "<command-name>/compact</command-name>"})
            + boundary("manual", 1000, 100, 5)
            + row(type="user", isCompactSummary=True, message={"content": "summary"})
            + row(type="user", message={"content": "after the boundary"}),
            encoding="utf-8")
        earlier = compaction_watch.read_earlier_history(path)
        assert [m["role"] for m in earlier] == ["user", "agent", "tool", "compaction"], earlier
        assert earlier[0]["text"] == "first question" and earlier[0]["ts"] is not None
        assert earlier[2]["output"] == "file body" and earlier[2]["input"] == {"file_path": "a"}
        assert earlier[3]["postTokens"] == 100
        assert compaction_watch.compact_summary_heads(path) == ["summary"]
        path.write_text(row(type="user", message={"content": "no boundary"}), encoding="utf-8")
        assert compaction_watch.compact_summary_heads(path) == []
        assert compaction_watch.read_earlier_history(path) == []
    print("compaction watch: ok")


if __name__ == "__main__":
    main()
