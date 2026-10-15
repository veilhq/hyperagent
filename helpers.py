"""
Hyperagent helpers â€” standalone utility functions and shared state.

Contains: kiro-cli binary lookup, model read/write, auth helpers, skill
metadata cache, title-subprocess inflight tracking, and palette math.
"""

import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (shared by all modules)
# ---------------------------------------------------------------------------

HYPERAGENT_DIR = Path(__file__).parent.resolve()
HYPERSPACE_ROOT = HYPERAGENT_DIR.parent
PORTAL_ROOT = HYPERSPACE_ROOT.parent
HYPERVISOR_DIR = HYPERSPACE_ROOT / ".hypervisor"
SKILLS_DIR = PORTAL_ROOT / ".kiro" / "skills"
PREFS_FILE = HYPERAGENT_DIR / "preferences.json"
ICON_FILE = HYPERAGENT_DIR / "assets" / "hyperagent.ico"
SESSIONS_DIR = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"

# ---------------------------------------------------------------------------
# Preferences access (shared across tab threads)
# ---------------------------------------------------------------------------
#
# Every tab runs its prompt/title work on its own daemon thread but shares this
# one process, so compound read-modify-write edits to preferences.json race:
# two threads each read, mutate their own copy, and write back, and the later
# writer silently drops the earlier writer's change (a lost ownedSessions or
# sessionTitles entry). This lock serializes the whole read-mutate-write cycle
# so concurrent edits compose instead of clobbering.
_PREFS_LOCK = threading.Lock()


def read_prefs():
    """Return the parsed preferences dict, or {} if absent/unreadable."""
    if PREFS_FILE.exists():
        try:
            return json.loads(PREFS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def update_prefs(mutator):
    """Atomically read-modify-write preferences.json under the shared lock.

    mutator receives the current prefs dict and edits it in place (or returns a
    replacement dict). The load, mutation, and save happen inside one lock hold
    so concurrent tab threads cannot clobber each other's edits. Returns the
    final prefs dict that was written.
    """
    with _PREFS_LOCK:
        prefs = read_prefs()
        result = mutator(prefs)
        if result is not None:
            prefs = result
        PREFS_FILE.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
        return prefs

# ---------------------------------------------------------------------------
# Structured logging (shared ecosystem logger)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(HYPERSPACE_ROOT / ".hyperkit" / "python"))
from hyper_logging import setup_logger, set_output_level, TRACE  # noqa: E402

import logging as _logging

_LEVEL_MAP = {
    "TRACE": TRACE,
    "DEBUG": _logging.DEBUG,
    "INFO": _logging.INFO,
    "WARNING": _logging.WARNING,
    "ERROR": _logging.ERROR,
}
_env_level = os.environ.get("HYPERAGENT_LOG_LEVEL", "INFO").upper()
_log_level = _LEVEL_MAP.get(_env_level, _logging.INFO)
logger = setup_logger("hyperagent", level=_log_level)
# Re-assert the level via the helper rather than looping over logger.handlers:
# setting every handler directly would also raise the flight recorder's level and
# stop it capturing the TRACE/DEBUG context it replays when an error fires.
set_output_level(logger, _log_level)


# ---------------------------------------------------------------------------
# Kiro-cli binary lookup
# ---------------------------------------------------------------------------

def _find_kiro():
    """Locate the kiro-cli executable, or return None."""
    found = shutil.which("kiro-cli")
    if found:
        return found
    fallback = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "bin" / "kiro-cli.exe"
    return str(fallback) if fallback.exists() else None


# ---------------------------------------------------------------------------
# Session file cleanup
# ---------------------------------------------------------------------------

def delete_session_files(session_id):
    """Remove a session's files (.json / .jsonl / .lock) from disk.

    Used to reap scratch sessions. Loading an existing session over ACP requires
    protocol state that only session/new establishes, so a scratch session is
    created and discarded on every load â€” and its metadata .json is written
    immediately, which makes it visible in the session list until removed.
    """
    if not session_id:
        return
    try:
        for f in SESSIONS_DIR.glob(f"{session_id}*"):
            if f.is_dir():
                shutil.rmtree(f, ignore_errors=True)
            else:
                f.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("delete_session_files(%s) failed: %s", session_id, e)


# ---------------------------------------------------------------------------
# Model helpers â€” read/write kiro-cli's chat.defaultModel via `kiro-cli settings`
# ---------------------------------------------------------------------------

def _kiro_settings_run(args, timeout=5):
    """Run `kiro-cli settings ...` without spawning a console window. Returns
    (returncode, stdout, stderr) or (None, "", str(error)) on exception."""
    kiro = _find_kiro()
    if not kiro:
        return (None, "", "kiro-cli not found")
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        r = subprocess.run(
            [kiro, "settings", *args],
            capture_output=True, text=True, timeout=timeout,
            creationflags=flags,
        )
        return (r.returncode, r.stdout or "", r.stderr or "")
    except Exception as e:
        return (None, "", str(e))


def read_kiro_default_model():
    """Return kiro-cli's chat.defaultModel value, or None."""
    rc, out, err = _kiro_settings_run(["chat.defaultModel", "--format", "json"])
    if rc != 0 or not out.strip():
        return None
    try:
        v = json.loads(out.strip())
        if isinstance(v, str):
            return v
        return None
    except Exception:
        return out.strip().strip('"') or None


def write_kiro_default_model(model_id):
    """Set chat.defaultModel via kiro-cli settings CLI. Returns True on success."""
    if not model_id or not isinstance(model_id, str):
        return False
    rc, out, err = _kiro_settings_run(["chat.defaultModel", model_id, "--global"])
    if rc == 0:
        logger.info("kiro-cli chat.defaultModel set to %s", model_id)
        return True
    logger.warning("kiro-cli set chat.defaultModel failed rc=%s err=%s", rc, err.strip())
    return False


# Cache of {modelId: rate_multiplier} sourced from `kiro-cli chat --list-models`.
_MODEL_RATES_CACHE = None


def get_model_rates():
    """Return a dict {modelId: rate_multiplier} from `kiro-cli chat --list-models`.
    Cached after first successful fetch. Returns empty dict on failure."""
    global _MODEL_RATES_CACHE
    if _MODEL_RATES_CACHE is not None:
        return _MODEL_RATES_CACHE
    kiro = _find_kiro()
    if not kiro:
        _MODEL_RATES_CACHE = {}
        return _MODEL_RATES_CACHE
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        r = subprocess.run(
            [kiro, "chat", "--list-models", "--format", "json"],
            capture_output=True, text=True, timeout=5,
            creationflags=flags,
        )
        if r.returncode != 0 or not r.stdout.strip():
            _MODEL_RATES_CACHE = {}
            return _MODEL_RATES_CACHE
        data = json.loads(r.stdout)
        rates = {}
        for m in data.get("models", []):
            mid = m.get("model_id") or m.get("modelId")
            rm = m.get("rate_multiplier")
            if mid is not None and rm is not None:
                rates[mid] = rm
        _MODEL_RATES_CACHE = rates
        logger.debug("model rates cached: %d entries", len(rates))
        return rates
    except Exception as e:
        logger.debug("get_model_rates failed: %s", e)
        _MODEL_RATES_CACHE = {}
        return _MODEL_RATES_CACHE


# ---------------------------------------------------------------------------
# CLI version detection
# ---------------------------------------------------------------------------

_KIRO_VERSION_CACHE = None


def get_kiro_version():
    """Return the kiro-cli version string (e.g. '2.16.1'), or None on failure.
    Cached after first successful fetch."""
    global _KIRO_VERSION_CACHE
    if _KIRO_VERSION_CACHE is not None:
        return _KIRO_VERSION_CACHE
    kiro = _find_kiro()
    if not kiro:
        return None
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        r = subprocess.run(
            [kiro, "--version"],
            capture_output=True, text=True, timeout=5,
            creationflags=flags,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        # Output: "kiro-cli-chat 2.16.1" â€” extract the version number
        parts = r.stdout.strip().split()
        version = parts[-1] if parts else r.stdout.strip()
        _KIRO_VERSION_CACHE = version
        logger.debug("kiro-cli version cached: %s", version)
        return version
    except Exception as e:
        logger.debug("get_kiro_version failed: %s", e)
        return None


def invalidate_kiro_version_cache():
    """Clear the cached version so next call re-reads from the binary."""
    global _KIRO_VERSION_CACHE
    _KIRO_VERSION_CACHE = None


# Header line: "Version 2.21.1 (2026-09-03)"
_CHANGELOG_HEADER_RE = re.compile(r"^Version\s+(\S+)\s+\((\d{4}-\d{2}-\d{2})\)\s*$")
# Entry line: "  - Fixed: [V3] some text" â€” type is one of the known categories,
# an optional "[V3]" (or similar) engine tag may lead the text.
_CHANGELOG_ENTRY_RE = re.compile(
    r"^\s*-\s*(Added|Changed|Fixed|Security|Deprecated):\s*(.*)$"
)
_CHANGELOG_TAG_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)$")

_CHANGELOG_CACHE = {}  # version -> parsed dict


def get_kiro_changelog(version):
    """Return parsed changelog for a specific kiro-cli version, or None on failure.

    Shells out to `kiro-cli version --changelog=<version>` and parses the plain
    text output into a structured dict:

        {
            "version": "2.21.1",
            "date": "2026-09-03",
            "entries": [{"type": "Added", "tag": None, "text": "..."},
                        {"type": "Changed", "tag": "V3", "text": "..."}],
        }

    Results are cached per version â€” changelog content is immutable for a
    released version, so a fetch only ever needs to run once per version.
    """
    if not version:
        return None
    if version in _CHANGELOG_CACHE:
        return _CHANGELOG_CACHE[version]
    kiro = _find_kiro()
    if not kiro:
        return None
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        r = subprocess.run(
            [kiro, "version", "--changelog=%s" % version],
            capture_output=True, text=True, timeout=5,
            creationflags=flags,
        )
        if r.returncode != 0 or not r.stdout.strip():
            logger.debug("get_kiro_changelog: no output for %s (rc=%s)", version, r.returncode)
            return None
    except Exception as e:
        logger.debug("get_kiro_changelog failed: %s", e)
        return None

    parsed = _parse_changelog(r.stdout, version)
    if parsed:
        _CHANGELOG_CACHE[version] = parsed
    return parsed


def _parse_changelog(text, requested_version):
    """Parse the first version block from changelog stdout into a dict.

    Reads the first version block encountered. The date comes from the
    "Version X (YYYY-MM-DD)" header; entries are the dash-prefixed category
    lines that follow it.
    """
    blocks = _parse_changelog_blocks(text)
    if not blocks:
        return None
    first = blocks[0]
    if not first.get("version"):
        first["version"] = requested_version
    return first


def _parse_changelog_blocks(text):
    """Parse every version block from changelog stdout into a list of dicts.

    Handles both single-version output (`--changelog=<v>`) and the full
    archive (`--changelog=all`). Each block:

        {"version": "2.21.1", "date": "2026-09-03",
         "entries": [{"type": "Added", "tag": None, "text": "..."}, ...]}

    Blocks are returned in source order (kiro-cli emits newest-first).
    """
    blocks = []
    current = None
    for line in text.splitlines():
        header = _CHANGELOG_HEADER_RE.match(line)
        if header:
            if current and current["entries"]:
                blocks.append(current)
            current = {
                "version": header.group(1),
                "date": header.group(2),
                "entries": [],
            }
            continue
        entry = _CHANGELOG_ENTRY_RE.match(line)
        if entry and current is not None:
            body = entry.group(2).strip()
            tag = None
            tag_match = _CHANGELOG_TAG_RE.match(body)
            if tag_match:
                tag = tag_match.group(1)
                body = tag_match.group(2).strip()
            current["entries"].append({
                "type": entry.group(1),
                "tag": tag,
                "text": body,
            })
    if current and current["entries"]:
        blocks.append(current)
    return blocks


def get_kiro_all_changelogs():
    """Return every changelog block the current kiro-cli binary knows.

    Shells out to `kiro-cli version --changelog=all` and parses all version
    blocks. Returns a list of dicts (newest-first), or [] on failure. Not
    cached â€” this is the capture source, meant to run on update events.
    """
    kiro = _find_kiro()
    if not kiro:
        return []
    flags = 0x08000000 if os.name == "nt" else 0
    try:
        r = subprocess.run(
            [kiro, "version", "--changelog=all"],
            capture_output=True, text=True, timeout=10,
            creationflags=flags,
        )
        if r.returncode != 0 or not r.stdout.strip():
            logger.debug("get_kiro_all_changelogs: no output (rc=%s)", r.returncode)
            return []
    except Exception as e:
        logger.debug("get_kiro_all_changelogs failed: %s", e)
        return []
    return _parse_changelog_blocks(r.stdout)


# Persistent changelog archive â€” lives in the hypervisor data dir so the
# hypervisor viewer utility can read it. Hyperagent owns the write side (it
# detects updates and talks to the CLI); hypervisor only reads.
CHANGELOG_CACHE_DIR = HYPERVISOR_DIR / "data"
CHANGELOG_CACHE_FILE = CHANGELOG_CACHE_DIR / "changelog.json"


def capture_changelog_cache():
    """Merge the current binary's changelog into the persistent archive.

    Append-only: versions already in the archive are never dropped, so history
    accumulates even as old versions age out of newer kiro-cli binaries. New
    versions are added; existing ones are refreshed in place (content for a
    released version is stable, but a re-parse costs nothing).

    Returns the number of versions newly added, or 0 on failure / no change.
    """
    blocks = get_kiro_all_changelogs()
    if not blocks:
        return 0

    existing = load_changelog_cache()
    by_version = {b["version"]: b for b in existing if b.get("version")}
    added = 0
    for block in blocks:
        v = block.get("version")
        if not v:
            continue
        if v not in by_version:
            added += 1
        by_version[v] = block

    # Sort newest-first by version tuple, falling back to string compare.
    def _vkey(item):
        v = item[0]
        try:
            return tuple(int(p) for p in v.split("."))
        except (ValueError, AttributeError):
            return (v,)

    merged = [b for _, b in sorted(by_version.items(), key=_vkey, reverse=True)]

    try:
        CHANGELOG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "captured": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "versions": merged,
        }
        CHANGELOG_CACHE_FILE.write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        logger.info("changelog cache updated: %d version(s) total, %d new",
                    len(merged), added)
    except Exception as e:
        logger.warning("capture_changelog_cache write failed: %s", e)
        return 0
    return added


def load_changelog_cache():
    """Read the persistent changelog archive. Returns a list of version blocks
    (newest-first), or [] if the cache does not exist or is unreadable."""
    if not CHANGELOG_CACHE_FILE.exists():
        return []
    try:
        data = json.loads(CHANGELOG_CACHE_FILE.read_text(encoding="utf-8"))
        return data.get("versions", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.debug("load_changelog_cache failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Title-subprocess crash correlation registry
# ---------------------------------------------------------------------------

_TITLE_INFLIGHT = {}          # pid -> dict(started, session_id, tab_states)
_TITLE_INFLIGHT_LOCK = threading.Lock()
_TITLE_SPAWN_SEQ = 0          # monotonic counter for correlating spawn/exit pairs


def _title_inflight_register(pid, session_id, tab_states):
    """Record a title subprocess as in flight. Returns its correlation seq."""
    global _TITLE_SPAWN_SEQ
    with _TITLE_INFLIGHT_LOCK:
        _TITLE_SPAWN_SEQ += 1
        seq = _TITLE_SPAWN_SEQ
        _TITLE_INFLIGHT[pid] = {
            "seq": seq,
            "started": time.monotonic(),
            "session_id": session_id,
            "tab_states": tab_states,
        }
    return seq


def _title_inflight_release(pid):
    """Remove a title subprocess from the in-flight set. Returns its record."""
    with _TITLE_INFLIGHT_LOCK:
        return _TITLE_INFLIGHT.pop(pid, None)


def _title_inflight_snapshot():
    """Return a list of (pid, age_seconds, seq, session_id, tab_states) for all
    currently in-flight title subprocesses."""
    now = time.monotonic()
    with _TITLE_INFLIGHT_LOCK:
        return [
            (pid, round(now - rec["started"], 3), rec["seq"],
             rec["session_id"], rec["tab_states"])
            for pid, rec in _TITLE_INFLIGHT.items()
        ]


def _kill_inflight_title_subprocesses():
    """Kill all in-flight title subprocesses and clear the registry.
    Returns the number of processes killed."""
    killed = 0
    with _TITLE_INFLIGHT_LOCK:
        for pid in list(_TITLE_INFLIGHT.keys()):
            try:
                os.kill(pid, 9)
                killed += 1
                logger.info("_kill_inflight_title: terminated pid=%d", pid)
            except OSError as e:
                logger.debug("_kill_inflight_title: pid=%d already gone: %s", pid, e)
        _TITLE_INFLIGHT.clear()
    return killed


# ---------------------------------------------------------------------------
# Skill metadata cache
# ---------------------------------------------------------------------------

def _load_skill_metadata():
    """Scan .kiro/skills/*/SKILL.md and extract name + description from frontmatter."""
    skills = {}
    if not SKILLS_DIR.exists():
        return skills
    for skill_dir in SKILLS_DIR.iterdir():
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            text = skill_file.read_text(encoding="utf-8")
            if text.startswith("---"):
                end = text.index("---", 3)
                front = text[3:end]
                name = ""
                desc = ""
                for line in front.strip().splitlines():
                    if line.startswith("name:"):
                        name = line[5:].strip()
                    elif line.startswith("description:"):
                        desc = line[12:].strip()
                if name:
                    skills[name] = {"name": name, "description": desc}
        except Exception:
            continue
    return skills


_SKILL_CACHE = _load_skill_metadata()
_SKILL_MD_PATTERN = re.compile(r"[/\\]\.kiro[/\\]skills[/\\]([^/\\]+)[/\\]SKILL\.md")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

AUTH_OK = True
AUTH_NO = False
AUTH_UNKNOWN = None


def _check_auth():
    """Determine kiro-cli auth state. Tri-state return:
        True  (AUTH_OK)      â€” logged in
        False (AUTH_NO)      â€” definitively not logged in
        None  (AUTH_UNKNOWN) â€” could not determine; the .exe was locked
    """
    delays = (0.25, 0.5, 1.0, 2.0, 4.0)
    locked = False
    kiro = _find_kiro()
    if not kiro:
        logger.error("_check_auth: kiro-cli not found")
        return AUTH_NO
    for attempt, delay in enumerate(delays + (None,)):
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            r = subprocess.run(
                [kiro, "whoami"],
                capture_output=True, text=True, startupinfo=si, timeout=10
            )
            return AUTH_OK if (r.returncode == 0 and "Logged in" in r.stdout) else AUTH_NO
        except OSError as e:
            if getattr(e, "winerror", None) == 32:
                locked = True
                if delay is not None:
                    logger.warning(
                        "_check_auth: exe locked (WinError 32), retry %d/%d in %.2fs",
                        attempt + 1, len(delays), delay,
                    )
                    time.sleep(delay)
                    continue
                logger.warning("_check_auth: exe locked through full backoff (~7.75s) â€” auth state UNKNOWN")
                return AUTH_UNKNOWN
            logger.error("_check_auth error (attempt %d): %s", attempt + 1, e)
            return AUTH_NO
        except Exception as e:
            logger.error("_check_auth error: %s", e)
            return AUTH_NO
    return AUTH_UNKNOWN if locked else AUTH_NO


def _do_login(window=None):
    """Run device-flow login. Pushes URL to frontend if window available.
    Returns True on success."""
    logger.info("_do_login: starting device flow")
    kiro = _find_kiro()
    if not kiro:
        logger.error("_do_login: kiro-cli not found")
        return False
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        proc = subprocess.Popen(
            [kiro, "login", "--license", "pro", "--use-device-flow"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, startupinfo=si,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        url_pushed = False
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            logger.info(f"_do_login output: {line.strip()}")
            if not url_pushed and ("http" in line.lower()):
                urls = re.findall(r'https?://\S+', line)
                if urls and window:
                    url_pushed = True
                    payload = json.dumps({"url": urls[0]}).replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                    try:
                        window.evaluate_js(
                            f"if(window.__acpAuthRequired)window.__acpAuthRequired(JSON.parse(`{payload}`))"
                        )
                    except Exception:
                        pass
        proc.wait(timeout=120)
        success = proc.returncode == 0
        logger.info(f"_do_login: exit={proc.returncode}")
        return success
    except Exception as e:
        logger.error(f"_do_login error: {e}")
        return False


def _do_login_visible():
    """Run 'kiro-cli login' in a visible console so interactive prompts work.
    Returns True on success."""
    logger.info("_do_login_visible: spawning visible console")
    kiro = _find_kiro()
    if not kiro:
        logger.error("_do_login_visible: kiro-cli not found")
        return False
    for attempt in range(5):
        try:
            proc = subprocess.Popen(
                [kiro, "login", "--license", "pro"],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            proc.wait(timeout=120)
            logger.info("_do_login_visible: exit=%d", proc.returncode)
            return proc.returncode == 0
        except OSError as e:
            if getattr(e, "winerror", None) == 32 and attempt < 4:
                logger.warning("_do_login_visible: exe locked (WinError 32), retry %d/4 in 200ms", attempt + 1)
                time.sleep(0.2)
                continue
            logger.error("_do_login_visible error (attempt %d): %s", attempt + 1, e)
            return False
        except Exception as e:
            logger.error("_do_login_visible error: %s", e)
            return False
    logger.error("_do_login_visible: exe locked through all retries, giving up")
