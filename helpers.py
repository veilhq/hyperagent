"""
Hyperagent helpers — standalone utility functions and shared state.

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
    created and discarded on every load — and its metadata .json is written
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
# Model helpers — read/write kiro-cli's chat.defaultModel via `kiro-cli settings`
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
        # Output: "kiro-cli-chat 2.16.1" — extract the version number
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
        True  (AUTH_OK)      — logged in
        False (AUTH_NO)      — definitively not logged in
        None  (AUTH_UNKNOWN) — could not determine; the .exe was locked
    """
    delays = (0.25, 0.5, 1.0, 2.0, 4.0)
    locked = False
    for attempt, delay in enumerate(delays + (None,)):
        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            r = subprocess.run(
                ["kiro-cli", "whoami"],
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
                logger.warning("_check_auth: exe locked through full backoff (~7.75s) — auth state UNKNOWN")
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
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        proc = subprocess.Popen(
            ["kiro-cli", "login", "--license", "pro", "--use-device-flow"],
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
    for attempt in range(5):
        try:
            proc = subprocess.Popen(
                ["kiro-cli", "login", "--license", "pro"],
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
    return False


# ---------------------------------------------------------------------------
# Palette math (OKLCH color space — perceptually uniform)
# ---------------------------------------------------------------------------

def build_palette_oklch(hex_color, mode):
    """Derive warm/cool/comp using OKLCH color space (perceptually uniform)."""

    # --- Conversion utilities ---
    def multiply_matrix3(m, v):
        return [
            m[0]*v[0] + m[1]*v[1] + m[2]*v[2],
            m[3]*v[0] + m[4]*v[1] + m[5]*v[2],
            m[6]*v[0] + m[7]*v[1] + m[8]*v[2],
        ]

    def srgb_to_linear(c):
        if abs(c) <= 0.04045:
            return c / 12.92
        return (-1 if c < 0 else 1) * (((abs(c) + 0.055) / 1.055) ** 2.4)

    def linear_to_srgb(c):
        if abs(c) > 0.0031308:
            return (-1 if c < 0 else 1) * (1.055 * (abs(c) ** (1 / 2.4)) - 0.055)
        return 12.92 * c

    M_SRGB_TO_XYZ = [
        0.41239079926595934, 0.357584339383878,   0.1804807884018343,
        0.21263900587151027, 0.715168678767756,   0.07219231536073371,
        0.01933081871559182, 0.11919477979462598, 0.9505321522496607,
    ]
    M_XYZ_TO_SRGB = [
         3.2409699419045226,  -1.537383177570094,   -0.4986107602930034,
        -0.9692436362808796,   1.8759675015077202,   0.04155505740717559,
         0.05563007969699366, -0.20397695888897652,  1.0569715142428786,
    ]
    M_XYZ_TO_LMS = [
        0.8190224379967030, 0.3619062600528904, -0.1288737815209879,
        0.0329836539323885, 0.9292868615863434,  0.0361446663506424,
        0.0481771893596242, 0.2642395317527308,  0.6335478284694309,
    ]
    M_LMS_TO_OKLAB = [
        0.2104542683093140,  0.7936177747023054, -0.0040720430116193,
        1.9779985324311684, -2.4285922420485799,  0.4505937096174110,
        0.0259040424655478,  0.7827717124575296, -0.8086757549230774,
    ]
    M_OKLAB_TO_LMS = [
        1,  0.3963377773761749,  0.2158037573099136,
        1, -0.1055613458156586, -0.0638541728258133,
        1, -0.0894841775298119, -1.2914855480194092,
    ]
    M_LMS_TO_XYZ = [
         1.2268798758459243, -0.5578149944602171,  0.2813910456659647,
        -0.0405757452148008,  1.1122868032803170, -0.0717110580655164,
        -0.0763729366746601, -0.4214933324022432,  1.5869240198367816,
    ]

    def hex_to_oklch(hex_str):
        r = int(hex_str[1:3], 16) / 255
        g = int(hex_str[3:5], 16) / 255
        b = int(hex_str[5:7], 16) / 255
        lin = [srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b)]
        xyz = multiply_matrix3(M_SRGB_TO_XYZ, lin)
        lms = multiply_matrix3(M_XYZ_TO_LMS, xyz)
        lms_cbrt = [math.copysign(abs(x) ** (1/3), x) if x != 0 else 0 for x in lms]
        lab = multiply_matrix3(M_LMS_TO_OKLAB, lms_cbrt)
        L = lab[0]
        a, b_val = lab[1], lab[2]
        C = math.sqrt(a*a + b_val*b_val)
        H = 0 if (abs(a) < 0.0002 and abs(b_val) < 0.0002) else (math.degrees(math.atan2(b_val, a)) % 360)
        return (L, C, H)

    def oklch_to_srgb(l, c, h):
        h_rad = math.radians(h)
        a = c * math.cos(h_rad)
        b_val = c * math.sin(h_rad)
        lms_cbrt = multiply_matrix3(M_OKLAB_TO_LMS, [l, a, b_val])
        lms = [x*x*x for x in lms_cbrt]
        xyz = multiply_matrix3(M_LMS_TO_XYZ, lms)
        lin_rgb = multiply_matrix3(M_XYZ_TO_SRGB, xyz)
        return [linear_to_srgb(lin_rgb[0]), linear_to_srgb(lin_rgb[1]), linear_to_srgb(lin_rgb[2])]

    def in_gamut(rgb):
        return all(-0.001 <= ch <= 1.001 for ch in rgb)

    def oklch_to_hex(l, c, h):
        rgb = oklch_to_srgb(l, c, h)
        if not in_gamut(rgb):
            lo, hi = 0.0, c
            for _ in range(20):
                mid = (lo + hi) / 2
                rgb = oklch_to_srgb(l, mid, h)
                if in_gamut(rgb):
                    lo = mid
                else:
                    hi = mid
            rgb = oklch_to_srgb(l, lo, h)
        rgb = [max(0, min(1, ch)) for ch in rgb]
        return "#{:02x}{:02x}{:02x}".format(
            round(rgb[0] * 255), round(rgb[1] * 255), round(rgb[2] * 255))

    # --- Palette derivation ---
    L, C, H = hex_to_oklch(hex_color)
    L = max(L, 0.55)

    if mode == "triadic":
        warm = oklch_to_hex(min(L * 0.9, 0.8), C, (H + 120) % 360)
        cool = oklch_to_hex(L * 0.8, C * 0.95, (H + 240) % 360)
        comp = oklch_to_hex(L * 0.7, C * 0.85, (H + 180) % 360)
    elif mode == "analogous":
        warm = oklch_to_hex(min(L * 0.95, 0.8), C, (H + 30) % 360)
        cool = oklch_to_hex(L * 0.85, C * 0.95, (H + 60) % 360)
        comp = oklch_to_hex(L * 0.75, C * 0.9, (H + 330) % 360)
    elif mode == "square":
        warm = oklch_to_hex(min(L * 0.9, 0.8), C, (H + 90) % 360)
        cool = oklch_to_hex(L * 0.8, C * 0.95, (H + 180) % 360)
        comp = oklch_to_hex(L * 0.7, C * 0.85, (H + 270) % 360)
    elif mode == "complement":
        warm = oklch_to_hex(min(L * 0.9, 0.8), C, (H + 180) % 360)
        cool = oklch_to_hex(L * 0.7, C * 0.85, (H + 180) % 360)
        comp = oklch_to_hex(L * 0.6, C * 0.6, H)
    else:  # split
        warm = oklch_to_hex(min(L * 0.9, 0.8), C, (H + 150) % 360)
        cool = oklch_to_hex(L * 0.8, C * 0.95, (H + 210) % 360)
        comp = oklch_to_hex(L * 0.7, C * 0.85, (H + 180) % 360)

    return {"accent": hex_color, "warm": warm, "cool": cool, "comp": comp}


def build_palette_hsl(hex_color, mode):
    """Derive warm/cool/comp from accent + palette mode (HSL-based, mirrors hypervisor theme.js)."""
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    r1, g1, b1 = r / 255, g / 255, b / 255
    mx, mn = max(r1, g1, b1), min(r1, g1, b1)
    l = (mx + mn) / 2
    if mx == mn:
        h = s = 0.0
    else:
        d = mx - mn
        s = d / (2 - mx - mn) if l > 0.5 else d / (mx + mn)
        if mx == r1:
            h = ((g1 - b1) / d + (6 if g1 < b1 else 0)) / 6
        elif mx == g1:
            h = ((b1 - r1) / d + 2) / 6
        else:
            h = ((r1 - g1) / d + 4) / 6
    h *= 360

    def hsl_to_hex(hh, ss, ll):
        hh = ((hh % 360) + 360) % 360
        c = (1 - abs(2 * ll - 1)) * ss
        x = c * (1 - abs((hh / 60) % 2 - 1))
        m = ll - c / 2
        if hh < 60:     rr, gg, bb = c, x, 0
        elif hh < 120:  rr, gg, bb = x, c, 0
        elif hh < 180:  rr, gg, bb = 0, c, x
        elif hh < 240:  rr, gg, bb = 0, x, c
        elif hh < 300:  rr, gg, bb = x, 0, c
        else:           rr, gg, bb = c, 0, x
        return "#{:02x}{:02x}{:02x}".format(
            round((rr + m) * 255), round((gg + m) * 255), round((bb + m) * 255))

    if mode == "triadic":
        warm = hsl_to_hex(h + 120, min(s * 1.1, 1), min(l * 1.15, 0.75))
        cool = hsl_to_hex(h + 240, min(s * 0.9, 1), min(l * 0.95, 0.65))
        comp = hsl_to_hex(h + 180, s * 0.7, min(l * 0.85, 0.55))
    elif mode == "analogous":
        warm = hsl_to_hex(h + 30, min(s * 1.05, 1), min(l * 1.1, 0.75))
        cool = hsl_to_hex(h + 60, min(s * 0.9, 1), min(l * 0.95, 0.65))
        comp = hsl_to_hex(h - 30, s * 0.85, min(l * 0.9, 0.6))
    elif mode == "square":
        warm = hsl_to_hex(h + 90, min(s * 1.1, 1), min(l * 1.1, 0.75))
        cool = hsl_to_hex(h + 180, min(s * 0.9, 1), min(l * 0.95, 0.65))
        comp = hsl_to_hex(h + 270, s * 0.8, min(l * 0.85, 0.55))
    elif mode == "complement":
        warm = hsl_to_hex(h + 180, min(s * 1.1, 1), min(l * 1.2, 0.75))
        cool = hsl_to_hex(h + 180, min(s * 0.7, 1), min(l * 0.7, 0.5))
        comp = hsl_to_hex(h, s * 0.5, min(l * 0.6, 0.4))
    else:  # split
        warm = hsl_to_hex(h + 150, min(s * 1.1, 1), min(l * 1.15, 0.75))
        cool = hsl_to_hex(h + 210, min(s * 0.9, 1), min(l * 0.95, 0.65))
        comp = hsl_to_hex(h + 180, s * 0.7, min(l * 0.85, 0.55))

    return {"accent": hex_color, "warm": warm, "cool": cool, "comp": comp}
