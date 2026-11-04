"""
Claude plan usage pulled from the agent's local /usage command.

/usage runs on a throwaway session in a headless ACPClient whose session lives
outside the workspace, so it never reaches a tab or the workspace sidebar; the
session is deleted afterwards. The last successful result is kept in
preferences.json so a failed pull or a restart can still show last-known usage
with its age.
"""

import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from helpers import AUTH_NO, logger, read_prefs, update_prefs

PREFS_KEY = "claudePlanUsage"
# The plan-limits endpoint behind /usage is rate limited; reuse a recent pull.
MIN_PULL_INTERVAL = 60
START_TIMEOUT = 60
SESSION_TIMEOUT = 45
PROMPT_TIMEOUT = 60

# One window line of the adapter's structured /usage output, e.g.
#   **5-hour limit** \u2014 **42%** \u00b7 Resets Oct 26, 7:00 PM CDT
_LIMIT_LINE = re.compile(
    r"^\*\*(?P<label>.+?)\*\*\s+\u2014\s+\*\*(?P<pct>\d+(?:\.\d+)?)%\*\*"
    r"(?:\s+\u00b7\s+Resets\s+(?P<reset>.+?))?\s*$",
    re.MULTILINE,
)
_MARKDOWN_ESCAPE = re.compile(r"\\(.)")

_pull_lock = threading.Lock()


def _window_key(label):
    """Map an adapter window label to the rate-limit type used by pushed reports."""
    low = label.lower()
    if low.startswith("5-hour"):
        return "five_hour", "five-hour"
    if "all models" in low:
        return "seven_day", "weekly"
    if "opus" in low:
        return "seven_day_opus", "weekly Opus"
    if "sonnet" in low:
        return "seven_day_sonnet", "weekly Sonnet"
    model = label.split("\u00b7", 1)[-1].strip() or label
    return "weekly_" + re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_"), "weekly " + model


def _parse_reset(text, now=None):
    """Epoch seconds for an adapter reset string ("Oct 26, 7:00 PM CDT"), or None.

    The string has no year and a zone abbreviation; it is read as local time
    (the adapter formats in this machine's zone) in the next year when the date
    would otherwise be more than a day in the past.
    """
    clean = re.sub(r"[\u202f\u00a0]", " ", text).strip()
    clean = re.sub(r"\s+[A-Z]{2,5}(?:[+-]\d{1,2}(?::?\d{2})?)?$", "", clean)
    now = now or datetime.now()
    for fmt in ("%b %d, %I:%M %p", "%b %d, %H:%M"):
        try:
            parsed = datetime.strptime(clean, fmt).replace(year=now.year)
        except ValueError:
            continue
        if (now - parsed).total_seconds() > 86400:
            parsed = parsed.replace(year=now.year + 1)
        return parsed.timestamp()
    return None


def parse_usage_markdown(text):
    """Window list from the adapter's structured /usage Markdown.

    Returns [] when no window line matches (the adapter fell back to Claude
    Code's raw output, or the login has no plan limits).
    """
    windows = []
    for match in _LIMIT_LINE.finditer(text or ""):
        label = _MARKDOWN_ESCAPE.sub(r"\1", match.group("label")).strip()
        key, name = _window_key(label)
        reset_text = _MARKDOWN_ESCAPE.sub(r"\1", match.group("reset") or "").strip()
        windows.append({
            "key": key,
            "label": name,
            "utilization": float(match.group("pct")),
            "resetsAt": _parse_reset(reset_text) if reset_text else None,
            "resetsText": reset_text,
        })
    return windows


def _probe_cwd():
    """Session directory for the probe, outside the workspace so its session is never listed."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Hyperagent" / "usage-probe"
    base.mkdir(parents=True, exist_ok=True)
    return str(base).replace("\\", "/")


def _clear_probe_transcripts():
    """Remove the probe's transcripts; Claude Code rewrites a stub after session/delete."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", _probe_cwd().replace("/", "\\"))
    project_dir = Path.home() / ".claude" / "projects" / slug
    for transcript in project_dir.glob("*.jsonl"):
        try:
            transcript.unlink()
        except OSError as e:
            logger.debug("plan_usage: could not remove %s: %s", transcript.name, e)


def _run_usage_command(backend):
    """Run /usage on a headless client. Returns (markdown, error)."""
    from acp_client import ACPClient

    if backend.check_auth() is AUTH_NO:
        return None, "%s is not signed in" % backend.display_name
    client = ACPClient()
    client._backend = backend
    client._tab_id = "usage"
    chunks = []

    def sink(update):
        if update.get("sessionUpdate") == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type", "text") == "text":
                chunks.append(content.get("text") or "")

    client._update_sink = sink
    session_id = None
    started = time.monotonic()
    try:
        client.start_process()
        client.connect()
        while client._state == "starting" and time.monotonic() - started < START_TIMEOUT:
            time.sleep(0.1)
        if client._state != "ready":
            return None, "%s did not start (state=%s)" % (backend.display_name, client._state)
        result = client.request_sync("session/new", {"cwd": _probe_cwd(), "mcpServers": []},
                                     timeout=SESSION_TIMEOUT)
        session_id = result.get("sessionId") if isinstance(result, dict) else None
        if not session_id:
            return None, "could not open a usage session: %s" % (result or "timeout")
        result = client.request_sync("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "/usage"}],
        }, timeout=PROMPT_TIMEOUT)
        if result is None:
            return None, "/usage timed out"
        if isinstance(result, dict) and "error" in result:
            return None, "/usage failed: %s" % result["error"]
        return "".join(chunks), None
    finally:
        if session_id:
            deleted = client.request_sync("session/delete", {"sessionId": session_id}, timeout=10)
            if not isinstance(deleted, dict) or "error" in deleted:
                logger.warning("plan_usage: could not delete usage session %s: %s", session_id[:8], deleted)
        client.stop()
        _clear_probe_transcripts()
        logger.info("plan_usage: probe finished in %.1fs", time.monotonic() - started)


def _cached():
    cached = read_prefs().get(PREFS_KEY)
    if isinstance(cached, dict) and isinstance(cached.get("windows"), list) and cached["windows"]:
        return cached
    return None


def get_plan_usage(backend):
    """{ok, windows, fetchedAt, stale, error} for the plan-usage chip.

    Pulls at most once per MIN_PULL_INTERVAL; concurrent callers share one pull.
    On failure the last successful pull is returned with stale=True.
    """
    with _pull_lock:
        cached = _cached()
        if cached and time.time() - (cached.get("fetchedAt") or 0) < MIN_PULL_INTERVAL:
            return {"ok": True, "windows": cached["windows"], "fetchedAt": cached["fetchedAt"],
                    "stale": False, "error": None}
        try:
            text, error = _run_usage_command(backend)
        except Exception as e:
            logger.exception("plan_usage: probe failed")
            text, error = None, str(e)
        windows = parse_usage_markdown(text) if text else []
        if text and not windows:
            limited = re.search(r"rate.?limited until ([^\n.]+)", text, re.IGNORECASE)
            error = ("usage check rate limited until %s" % limited.group(1).strip() if limited
                     else "no plan limits in /usage output")
            logger.warning("plan_usage: unrecognized /usage output: %r", text[:400])
        if windows:
            fetched_at = time.time()
            update_prefs(lambda prefs: prefs.__setitem__(PREFS_KEY, {"windows": windows, "fetchedAt": fetched_at}))
            logger.info("plan_usage: %s", ", ".join("%s %.0f%%" % (w["key"], w["utilization"]) for w in windows))
            return {"ok": True, "windows": windows, "fetchedAt": fetched_at, "stale": False, "error": None}
        logger.warning("plan_usage: pull failed: %s", error)
        if cached:
            return {"ok": True, "windows": cached["windows"], "fetchedAt": cached["fetchedAt"],
                    "stale": True, "error": error}
        return {"ok": False, "windows": [], "fetchedAt": None, "stale": False, "error": error}
