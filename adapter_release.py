"""Claude Code adapter releases: find a newer one, test it, then switch to it.

The adapter (claude-agent-acp) bundles the Claude Code runtime Hyperagent runs.
Each version installs into its own directory, so a candidate can be installed
and tested without touching the version in use. The version in use is the
`adapterVersion` preference, defaulting to DEFAULT_ADAPTER_VERSION.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from helpers import logger, read_prefs, update_prefs, version_key

ADAPTER_NAME = "@agentclientprotocol/claude-agent-acp"
DEFAULT_ADAPTER_VERSION = "0.94.0"
SDK_NAME = "@anthropic-ai/claude-agent-sdk"
PREF_ACTIVE = "adapterVersion"
PREF_TEST = "adapterTest"

_LATEST_TTL = 3600.0
_INSTALL_TIMEOUT = 600
_SMOKE_TIMEOUT = 420
_SMOKE_SCRIPT = Path(__file__).resolve().parent / "adapter_smoke.py"

_lock = threading.Lock()
_latest = {"fetched": 0.0, "version": None}
_runtime_cache = {}
_install_started = False


def _quiet_flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def active_version():
    """Adapter version Hyperagent launches."""
    return read_prefs().get(PREF_ACTIVE) or DEFAULT_ADAPTER_VERSION


def package_spec(version):
    return "%s@%s" % (ADAPTER_NAME, version)


def adapter_dir(version):
    """App-owned install location for one adapter version."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Hyperagent"
    return base / ("claude-agent-acp-" + version)


def prune_old_adapters():
    """Delete installs older than the one in use, keeping the newest one back.

    Versions newer than the active one (a tested candidate) are kept.
    """
    base = adapter_dir("x").parent
    prefix = adapter_dir("").name
    installed = {d.name[len(prefix):]: d for d in base.glob(prefix + "*") if d.is_dir()}
    active = active_version()
    older = sorted((v for v in installed if version_key(v) < version_key(active)), key=version_key)
    for version in older[:-1]:
        logger.info("adapter: removing old install %s", version)
        shutil.rmtree(installed[version], ignore_errors=True)
        _runtime_cache.pop(version, None)


def adapter_entry(version):
    return (adapter_dir(version) / "node_modules" / "@agentclientprotocol"
            / "claude-agent-acp" / "dist" / "index.js")


def _npm_view(spec, field):
    """Value of one `npm view` field, or None."""
    npm = shutil.which("npm")
    if not npm:
        return None
    try:
        proc = subprocess.run([npm, "view", spec, field], capture_output=True, text=True,
                              timeout=30, creationflags=_quiet_flags())
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug("adapter: npm view %s failed: %s", spec, e)
        return None
    value = (proc.stdout or "").strip()
    return value if proc.returncode == 0 and value else None


def latest_adapter_version(force=False):
    """Newest published adapter (the `latest` tag), cached for an hour."""
    with _lock:
        if not force and _latest["version"] and time.time() - _latest["fetched"] < _LATEST_TTL:
            return _latest["version"]
    version = _npm_view(ADAPTER_NAME, "version")
    with _lock:
        if version:
            _latest.update(fetched=time.time(), version=version)
        return _latest["version"]


def runtime_version(version):
    """Claude Code version bundled by an adapter version, or None.

    Reads the installed SDK package when present, else asks the registry for
    the SDK version the adapter depends on.
    """
    if version in _runtime_cache:
        return _runtime_cache[version]
    runtime = None
    sdk_json = adapter_dir(version) / "node_modules" / "@anthropic-ai" / "claude-agent-sdk" / "package.json"
    try:
        runtime = json.loads(sdk_json.read_text(encoding="utf-8")).get("claudeCodeVersion")
    except (OSError, ValueError):
        sdk_version = _npm_view(package_spec(version), "dependencies." + SDK_NAME)
        if sdk_version:
            runtime = _npm_view("%s@%s" % (SDK_NAME, sdk_version), "claudeCodeVersion")
    if runtime:
        _runtime_cache[version] = runtime
    return runtime


def install_adapter(version):
    """Install one adapter version into its directory. Returns (ok, error)."""
    npm = shutil.which("npm")
    if not npm:
        return False, "npm not found on PATH"
    target = adapter_dir(version)
    logger.info("adapter: installing %s into %s", package_spec(version), target)
    started = time.time()
    try:
        target.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [npm, "install", "--prefix", str(target), "--no-audit", "--no-fund",
             "--loglevel=error", package_spec(version)],
            capture_output=True, text=True, timeout=_INSTALL_TIMEOUT, creationflags=_quiet_flags())
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.error("adapter: install failed: %s", e)
        return False, str(e)
    if proc.returncode != 0 or not adapter_entry(version).exists():
        tail = (proc.stderr or proc.stdout or "")[-500:]
        logger.error("adapter: install failed (exit=%d): %s", proc.returncode, tail)
        return False, tail or "install failed"
    logger.info("adapter: installed in %.1fs", time.time() - started)
    return True, None


def install_active_in_background():
    """Install the active version once per process so later launches skip npx."""
    global _install_started
    with _lock:
        if _install_started:
            return
        _install_started = True
    threading.Thread(target=install_adapter, args=(active_version(),), daemon=True).start()


def candidate_version():
    """The newest adapter when it is newer than the one in use, else None."""
    latest = latest_adapter_version()
    if latest and version_key(latest) > version_key(active_version()):
        return latest
    return None


def _stored_test(candidate):
    """The saved test result for `candidate`, or None."""
    saved = read_prefs().get(PREF_TEST)
    return saved if isinstance(saved, dict) and saved.get("version") == candidate else None


def release_state():
    """Where the update flow stands: versions, runtimes, and the test result."""
    active = active_version()
    candidate = candidate_version()
    saved = _stored_test(candidate) if candidate else None
    return {
        "active": active,
        "runtime": runtime_version(active),
        "candidate": candidate,
        "candidateRuntime": runtime_version(candidate) if candidate else None,
        "test": saved,
    }


def test_release():
    """Install the candidate into its own directory and run the smoke test.

    Blocking (minutes). Saves the result so a pass survives a restart.
    """
    candidate = candidate_version()
    if not candidate:
        return {"ok": False, "error": "No newer release to test."}
    ok, error = install_adapter(candidate)
    if not ok:
        return _save_test(candidate, False, [{"name": "install", "ok": False, "detail": error}])
    try:
        proc = subprocess.run(
            [sys.executable, str(_SMOKE_SCRIPT), str(adapter_entry(candidate))],
            capture_output=True, text=True, timeout=_SMOKE_TIMEOUT, creationflags=_quiet_flags())
        report = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as e:
        logger.error("adapter: smoke test did not complete: %s", e)
        return _save_test(candidate, False, [{"name": "smoke test", "ok": False, "detail": str(e)}])
    return _save_test(candidate, bool(report.get("ok")), report.get("checks") or [])


def _save_test(candidate, passed, checks):
    result = {"version": candidate, "ok": passed, "checks": checks,
              "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    update_prefs(lambda prefs: prefs.__setitem__(PREF_TEST, result))
    logger.info("adapter: test of %s %s", candidate, "passed" if passed else "failed")
    return result


def install_release():
    """Switch Hyperagent to the tested candidate. Takes effect on Reconnect."""
    candidate = candidate_version()
    saved = _stored_test(candidate) if candidate else None
    if not saved or not saved.get("ok"):
        return {"ok": False, "error": "The release has not passed testing."}
    if not adapter_entry(candidate).exists():
        return {"ok": False, "error": "The tested install is missing; test again."}
    update_prefs(lambda prefs: prefs.__setitem__(PREF_ACTIVE, candidate))
    logger.info("adapter: active version is now %s", candidate)
    prune_old_adapters()
    return {"ok": True, "version": candidate, "runtime": runtime_version(candidate)}
