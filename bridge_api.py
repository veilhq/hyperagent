"""
HyperagentAPI — PyWebView JS bridge.

Exposes all methods callable from the frontend via `window.pywebview.api.*`.
Handles tab management, session CRUD, model switching, title generation,
theme/palette, clipboard, and semantic search.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from helpers import (
    HYPERSPACE_ROOT,
    HYPERVISOR_DIR,
    PORTAL_ROOT,
    PREFS_FILE,
    SKILLS_DIR,
    logger,
    _check_auth,
    _find_kiro,
    _title_inflight_register,
    _title_inflight_release,
    _SKILL_CACHE,
    _SKILL_MD_PATTERN,
    AUTH_NO,
    get_model_rates,
    read_kiro_default_model,
    write_kiro_default_model,
    build_palette_oklch,
    build_palette_hsl,
)
from acp_pool import ACPClientPool


class HyperagentAPI:
    def __init__(self, pool):
        self._pool = pool

    @property
    def _acp(self):
        """Backward compat: return active client."""
        return self._pool.active_client

    def send_prompt(self, text, tab_id=None):
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if client and text and text.strip():
            threading.Thread(target=client.prompt, args=(text.strip(),), daemon=True).start()

    def cancel(self, reason=None, tab_id=None):
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if client:
            client.cancel(reason=reason)

    def new_session(self, tab_id=None):
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if client:
            threading.Thread(target=client.new_session, daemon=True).start()

    def create_tab(self):
        """Create a new tab, spawn its process, connect it."""
        tab_id = self._pool.create_tab()
        if not tab_id:
            return None
        def _start():
            self._pool.start_tab(tab_id)
            self._pool.connect_tab(tab_id)
            self._pool.save_tab_state()
        threading.Thread(target=_start, daemon=True).start()
        return tab_id

    def close_tab(self, tab_id):
        """Close a tab and its process."""
        new_active = self._pool.close_tab(tab_id)
        self._pool.save_tab_state()
        return new_active

    def switch_tab(self, tab_id):
        """Switch to a different tab."""
        result = self._pool.switch_tab(tab_id)
        if result:
            self._pool.save_tab_state()
        return result

    def get_tabs(self):
        """Return list of tabs with their states."""
        states = self._pool.get_tab_states()
        return {
            "tabs": [{"id": tid, "state": st} for tid, st in states.items()],
            "active": self._pool._active_tab
        }

    def open_session_in_tab(self, session_id):
        """Create a new tab and load an existing session into it."""
        logger.info("open_session_in_tab: session=%s", session_id[:8])
        for tab_id, client in self._pool._clients.items():
            if client._session_id == session_id:
                logger.warning("open_session_in_tab: session %s already in tab=%s", session_id[:8], tab_id[:6])
                self._acp._push_js("__acpError", {
                    "error": "Session already open in another tab"
                })
                return None
        tab_id = self._pool.create_tab()
        if not tab_id:
            return None
        def _start():
            self._pool.start_tab(tab_id)
            client = self._pool.get_client(tab_id)
            if client:
                client._suppress_init_ready = True
            self._pool.connect_tab(tab_id)
            if client:
                client._state = "starting"
                client._push_state()
                history = self.get_session_history(session_id)
                client._push_js("__acpSessionLoaded", {"sessionId": session_id, "messages": history})

                def on_load_result(result):
                    if isinstance(result, dict) and "error" in result:
                        err = result["error"]
                        err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                        logger.error("%sopen_session_in_tab load failed: %s", client._tab_ctx(), result)
                        client._push_js("__acpError", {"error": f"Failed to load session: {err_msg}"})
                    else:
                        client._set_session_id(session_id)
                        client._capture_and_push_models(result)
                        client._apply_preferred_model()
                    client._state = "ready"
                    client._push_state()

                def on_new_done(result):
                    throwaway_id = result.get("sessionId") if isinstance(result, dict) else None
                    if throwaway_id:
                        client._owned_sessions.add(throwaway_id)
                        def on_load_and_clean(res):
                            on_load_result(res)
                            if throwaway_id:
                                self._delete_session_files(throwaway_id)
                        client._request("session/load", {
                            "sessionId": session_id,
                            "cwd": str(PORTAL_ROOT).replace("\\", "/"),
                            "mcpServers": []
                        }, on_load_and_clean)
                    else:
                        client._request("session/load", {
                            "sessionId": session_id,
                            "cwd": str(PORTAL_ROOT).replace("\\", "/"),
                            "mcpServers": []
                        }, on_load_result)

                cwd = str(PORTAL_ROOT).replace("\\", "/")
                client._request("session/new", {"cwd": cwd, "mcpServers": []}, on_new_done)
            self._pool.save_tab_state()
        threading.Thread(target=_start, daemon=True).start()
        return tab_id


    def _heuristic_title(self, user_message):
        """Fallback title: verb + noun from the first sentence."""
        text = user_message.strip()
        for sep in ['\n', '. ', '? ', '! ']:
            if sep in text:
                text = text[:text.index(sep)]
                break
        for prefix in ['can you ', 'could you ', 'please ', 'i want to ', 'i need to ', "let's ", 'help me ', 'i would like to ', 'would you ']:
            if text.lower().startswith(prefix):
                text = text[len(prefix):]
                break
        _SKIP = {
            "a", "an", "the",
            "i", "we", "you",
            "just", "also", "then", "and", "but", "so",
            "to", "in", "on", "at", "for", "with", "by", "of", "from", "into",
        }
        tokens = [t for t in re.findall(r"[A-Za-z0-9']+", text) if t]
        while tokens and tokens[0].lower() in _SKIP:
            tokens = tokens[1:]
        words = tokens[:2] if tokens else []
        if words:
            title = ' '.join(w[:1].upper() + w[1:].lower() for w in words)
        else:
            title = user_message.strip()[:30]
        return title or user_message[:30]

    def _tab_state_snapshot(self):
        """DIAGNOSTIC: compact snapshot of every tab's ACP state."""
        try:
            return {
                str(tid)[:6]: getattr(c, "_state", "?")
                for tid, c in list(self._pool._clients.items())
            }
        except Exception as e:
            logger.debug("_tab_state_snapshot failed: %s", e)
            return {}

    def _ai_title(self, user_message, session_id=None):
        """Ask kiro-cli for a descriptive 2-5 word title. Returns None on failure."""
        kiro = _find_kiro()
        if not kiro:
            logger.warning("_ai_title: kiro-cli not found, using heuristic (session=%s)", session_id)
            return None

        snippet = user_message.strip()
        if len(snippet) > 500:
            snippet = snippet[:500] + '...'

        prompt = (
            "Summarize the following user request as a two-word title in "
            "'Verb Noun' form (imperative verb + object noun). "
            "Examples: 'Fix Migration', 'Review PR', 'Refactor Sidebar', "
            "'Debug Deploy', 'Add Endpoint'. "
            "Rules: exactly 2 words, title case, no punctuation, no quotes, "
            "no trailing period, no articles (a/an/the). "
            "Reply with ONLY the title text — no preamble, no explanation.\n\n"
            "Request: " + snippet
        )

        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE

            tab_states = self._tab_state_snapshot()

            proc = subprocess.Popen(
                [kiro, "chat", "--no-interactive", prompt],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace",
                startupinfo=si,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            seq = _title_inflight_register(proc.pid, session_id, tab_states)
            logger.info(
                "_ai_title SPAWN seq=%d pid=%d session=%s tabs=%s",
                seq, proc.pid, session_id, tab_states,
            )
            _t0 = time.monotonic()
            try:
                stdout, stderr = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                logger.warning("_ai_title TIMEOUT seq=%d pid=%d — killing", seq, proc.pid)
                try:
                    proc.kill()
                    proc.communicate(timeout=5)
                except Exception as _ke:
                    logger.error("_ai_title: kill failed pid=%d: %s", proc.pid, _ke)
                raise
            finally:
                _rec = _title_inflight_release(proc.pid)
                logger.info(
                    "_ai_title EXIT  seq=%s pid=%d rc=%s dur=%.2fs",
                    _rec["seq"] if _rec else "?", proc.pid,
                    proc.returncode, time.monotonic() - _t0,
                )
            output = (stdout or "") + "\n" + (stderr or "")
            clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
            clean = re.sub(r'\x1b\[\?[0-9;]*[a-zA-Z]', '', clean)

            _METER_MARKERS = (
                'credits:', 'tokens:', 'time:', 'cost:', 'usage:', 'plan:',
                'thinking', 'trust-all-tools', 'session id', 'model:',
            )
            _PREFIX_REJECT = ('▸', '›', '[', '#', '$', '•', '·', '|')

            def _strip_response_marker(line):
                s = line.lstrip()
                if s.startswith('> '):
                    return s[2:].strip()
                if s.startswith('>') and len(s) > 1 and s[1] != '>':
                    return s[1:].strip()
                return line

            def _looks_like_title(line):
                s = line.strip()
                if not s:
                    return False
                if s.startswith(_PREFIX_REJECT):
                    return False
                low = s.lower()
                if any(m in low for m in _METER_MARKERS):
                    return False
                if low.startswith(('info ', 'debug ', 'warn ', 'error ', '[info', '[debug', '[warn', '[error')):
                    return False
                return True

            lines = [ln for ln in clean.splitlines() if ln.strip()]

            candidate = None
            for ln in reversed(lines):
                stripped = ln.lstrip()
                if stripped.startswith('> ') or (stripped.startswith('>') and len(stripped) > 1 and stripped[1] != '>'):
                    inner = _strip_response_marker(ln)
                    if _looks_like_title(inner):
                        candidate = inner
                        break

            if not candidate:
                for ln in reversed(lines):
                    if _looks_like_title(ln):
                        candidate = ln.strip()
                        break

            if not candidate:
                logger.warning("_ai_title: no candidate line found in output (session=%s): %r", session_id, clean[:400])
                return None

            title = candidate.strip('`"\'*_ ').strip()
            title = title.rstrip('.!?,;:')
            words = title.split()
            if not words:
                return None
            if len(words) > 2:
                words = words[:2]
            _ARTICLES = {"a", "an", "the"}
            if len(words) == 2 and words[0].lower() in _ARTICLES:
                words = words[1:]
            title = ' '.join(w[:1].upper() + w[1:] for w in words if w)
            if len(title) > 40:
                title = title[:40].rstrip()
            low = title.lower()
            if any(bad in low for bad in ("error", "sorry", "cannot", "unable", "logged out")):
                logger.warning("_ai_title: rejected suspicious response (session=%s): %r", session_id, title)
                return None
            logger.info("_ai_title: generated %r for session %s", title, session_id)
            return title or None
        except subprocess.TimeoutExpired:
            logger.warning("_ai_title: kiro-cli timeout (session=%s)", session_id)
            return None
        except Exception as e:
            logger.error("_ai_title error (session=%s): %s", session_id, e)
            return None

    def generate_title(self, user_message, tab_id=None):
        """Generate a short session title from the user's first message."""
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if client is None:
            logger.warning("generate_title: no client for tab_id=%s", tab_id)
            return

        def _run():
            _t0 = time.monotonic()
            client._push_js("__acpTitleActivity", {
                "phase": "start",
                "label": "title",
            })
            try:
                ai = self._ai_title(user_message, session_id=client._session_id)
                title = ai or self._heuristic_title(user_message)
                source = "ai" if ai else "heuristic"

                client._push_js("__acpSessionTitle", {"title": title, "source": source})
                client._push_js("__acpTitleActivity", {
                    "phase": "done",
                    "label": "title",
                    "title": title,
                    "source": source,
                    "durationMs": int((time.monotonic() - _t0) * 1000),
                })
                client._tab_title = title
                if client._session_id:
                    prefs = client._load_prefs()
                    titles = prefs.get("sessionTitles", {})
                    titles[client._session_id] = title
                    prefs["sessionTitles"] = titles
                    client._save_prefs(prefs)
                self._pool.save_tab_state()
            except Exception as e:
                logger.error("generate_title error: %s", e)
                fallback = self._heuristic_title(user_message)
                client._push_js("__acpSessionTitle", {"title": fallback, "source": "heuristic"})
                client._push_js("__acpTitleActivity", {
                    "phase": "failed",
                    "label": "title",
                    "title": fallback,
                    "source": "heuristic",
                    "reason": str(e)[:200],
                    "durationMs": int((time.monotonic() - _t0) * 1000),
                })
        threading.Thread(target=_run, daemon=True).start()


    def reconnect(self, tab_id=None):
        """Reconnect a specific tab (or active tab if no tab_id given)."""
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if client:
            threading.Thread(target=client.start, daemon=True).start()

    def get_state(self, tab_id=None):
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        return client.state if client else "stopped"

    def toggle_fullscreen(self):
        if self._acp._window:
            self._acp._window.toggle_fullscreen()

    def launch_hypereye(self):
        """Launch Hypereye as a detached subprocess via its shortcut.

        Launching via the .lnk ensures Windows groups the window with the
        pinned taskbar shortcut (same AppUserModelID) and shows the correct icon.
        Falls back to direct pythonw invocation if the shortcut doesn't exist.
        """
        import os
        shortcut = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Hypereye.lnk"
        script = HYPERSPACE_ROOT / ".hypereye" / "hypereye.py"

        try:
            if shortcut.exists():
                os.startfile(str(shortcut))
                logger.info("launch_hypereye: started via shortcut")
            elif script.exists():
                subprocess.Popen(
                    ["pythonw", str(script)],
                    cwd=str(script.parent),
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                )
                logger.info("launch_hypereye: started via pythonw (no shortcut)")
            else:
                return {"ok": False, "error": "hypereye.py not found"}
            return {"ok": True}
        except Exception as e:
            logger.error("launch_hypereye failed: %s", e)
            return {"ok": False, "error": str(e)}

    # --- Model switcher API ---

    def set_model(self, model_id, tab_id=None):
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if not client:
            return False
        return client.set_model(model_id)

    def set_default_model(self, model_id):
        """Write model_id to kiro-cli's chat.defaultModel setting (global)."""
        if not write_kiro_default_model(model_id):
            return False
        for tab_id, client in list(self._pool._clients.items()):
            try:
                client._push_models_event()
            except Exception as e:
                logger.debug("push_models_event on tab %s failed: %s", tab_id, e)
        return True

    def get_models(self, tab_id=None):
        """Return the current model state for a tab."""
        client = self._pool.get_client(tab_id) if tab_id else self._pool.active_client
        if not client:
            return None
        rates = get_model_rates()
        enriched = []
        for m in (client._available_models or []):
            mid = m.get("modelId") if isinstance(m, dict) else None
            if mid and mid in rates:
                m2 = dict(m); m2["rateMultiplier"] = rates[mid]
                enriched.append(m2)
            else:
                enriched.append(m)
        return {
            "currentModelId": client._current_model_id,
            "availableModels": enriched,
            "defaultModelId": read_kiro_default_model(),
        }

    def debug_log(self, message):
        """Route a JS-side trace into the hyperagent log."""
        try:
            logger.info("JS: %s", message)
        except Exception:
            pass
        return True

    def copy_to_clipboard(self, text):
        """Write text to the system clipboard via Windows clip.exe."""
        logger.debug("copy_to_clipboard: invoked (%d chars)", len(text) if text else 0)
        try:
            process = subprocess.Popen(
                ["clip"],
                stdin=subprocess.PIPE,
            )
            process.communicate(input=text.encode("utf-16le"))
            if process.returncode != 0:
                logger.warning("clipboard write failed: clip.exe exited %d", process.returncode)
                return False
            logger.debug("clipboard write: %d chars", len(text))
            return True
        except Exception as e:
            logger.warning("clipboard write failed: %s", e)
            return False

    def get_plan_usage(self):
        """Run kiro-cli /usage command and parse plan credits percentage."""
        kiro = _find_kiro()
        if not kiro:
            return {"ok": False, "error": "kiro-cli not found"}

        try:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = subprocess.SW_HIDE
            result = subprocess.run(
                [kiro, "chat", "--no-interactive", "/usage"],
                capture_output=True, text=True, timeout=20,
                startupinfo=si,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            output = (result.stdout or "") + (result.stderr or "")
            clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
            clean = re.sub(r'\x1b\[\?[0-9;]*[a-zA-Z]', '', clean)

            logger.debug(f"plan_usage raw: {repr(clean[:300])}")

            credits_match = re.search(r'\((\d+(?:\.\d+)?)\s+of\s+(\d+(?:\.\d+)?)\s+covered', clean)
            if credits_match:
                used = float(credits_match.group(1))
                total = float(credits_match.group(2))
                used_pct = int((used / total) * 100) if total > 0 else 0
                reset_match = re.search(r'resets?\s+on\s+([\d-]+)', clean)
                reset_str = reset_match.group(1) if reset_match else ""
                at_cap = (used >= total) and ("covered in plan" in clean)
                detail = f"{used:.1f} / {total:.0f} credits"
                if reset_str:
                    detail += f" | resets {reset_str}"
                if at_cap:
                    detail += " | at or over plan limit"
                used_str = f"{used:.1f}" if used != int(used) else str(int(used))
                if at_cap:
                    used_str += "+"
                total_str = str(int(total))
                return {"ok": True, "used_pct": used_pct, "used": used_str, "total": total_str, "detail": detail, "at_cap": at_cap}

            pct_match = re.search(r'(\d+)%', clean)
            if pct_match:
                used_pct = int(pct_match.group(1))
                detail = clean.strip().replace('\n', ' | ')[:200]
                return {"ok": True, "used_pct": used_pct, "detail": detail}

            return {"ok": False, "error": "Could not parse usage output"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "Timed out"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # --- Gradient map presets (mirrors theme.js GRADIENT_MAPS) ---
    GRADIENT_MAPS = {
        "frost2": {"accent": "#d2ebfe", "warm": "#c0caff", "cool": "#ceb0e4", "comp": "#ff0059",
                    "semantics": {"success": "#1dff7d", "warning": "#fdca18", "error": "#fb110b", "info": "#1be1fd"}},
        "cyberdeck": {"accent": "#00ff9f", "warm": "#ffe600", "cool": "#00e5ff", "comp": "#ff003c",
                       "semantics": {"success": "#1efea0", "warning": "#fee51b", "error": "#fc113e", "info": "#1de5fe"}},
        "thermal": {"accent": "#ffc250", "warm": "#fb5a46", "cool": "#5480c7", "comp": "#d10054",
                     "semantics": {"success": "#21ff7b", "warning": "#fdca18", "error": "#fd1369", "info": "#086ffd"}},
        "tundra": {"accent": "#d2ebfe", "warm": "#c0caff", "cool": "#ceb0e4", "comp": "#c8ff5c",
                    "semantics": {"success": "#bffe1c", "warning": "#fdb015", "error": "#fd154c", "info": "#1be1fd"}},
        "cryo": {"accent": "#d2ebfe", "warm": "#c8d8ff", "cool": "#c0b8e8", "comp": "#a855f7",
                  "semantics": {"success": "#1efea1", "warning": "#fdb015", "error": "#fd154c", "info": "#a01efd"}},
        "nordic": {"accent": "#b8ccd8", "warm": "#a8b8c8", "cool": "#c0d0dc", "comp": "#ffb000",
                    "semantics": {"success": "#1dfd91", "warning": "#fdb015", "error": "#fc5c0d", "info": "#0f9afc"}},
        "frostbite": {"accent": "#c2e8ff", "warm": "#a0d0f0", "cool": "#8ac0e8", "comp": "#00c0ff",
                       "semantics": {"success": "#1efea1", "warning": "#fdb015", "error": "#fd154c", "info": "#15bffc"}},
        "hazmat": {"accent": "#c8ff00", "warm": "#ffea00", "cool": "#00ff88", "comp": "#ff00cc",
                    "semantics": {"success": "#c8fe1c", "warning": "#ffea1c", "error": "#fe13cb", "info": "#15c1fd"}},
        "laser": {"accent": "#00ff41", "warm": "#ff0044", "cool": "#0044ff", "comp": "#8b00ff",
                   "semantics": {"success": "#1dfd46", "warning": "#ffea1c", "error": "#fc1145", "info": "#1f5efc"}},
        "prism": {"accent": "#ff2020", "warm": "#ffea00", "cool": "#00e0ff", "comp": "#ff00e5",
                   "semantics": {"success": "#1dfd49", "warning": "#ffea1c", "error": "#fd151a", "info": "#1adffd"}},
        "emergency": {"accent": "#ff5500", "warm": "#ffd500", "cool": "#00ff44", "comp": "#ff003c",
                       "semantics": {"success": "#1dfd49", "warning": "#fdd419", "error": "#fc113e", "info": "#15c1fd"}},
        "ignite": {"accent": "#4a4a4a", "warm": "#ff6600", "cool": "#ffea00", "comp": "#ff003c",
                    "semantics": {"success": "#1dfd49", "warning": "#ffea1c", "error": "#fc113e", "info": "#15c1fd"}},
        "bloom": {"accent": "#4a4a4a", "warm": "#ff00d4", "cool": "#a855f7", "comp": "#ff77e9",
                   "semantics": {"success": "#1dfe8a", "warning": "#ffea1c", "error": "#fe18d3", "info": "#a01efd"}},
        "verdigris": {"accent": "#4a4a4a", "warm": "#00ff88", "cool": "#00e0ff", "comp": "#c8ff00",
                       "semantics": {"success": "#1dfe8a", "warning": "#fdca18", "error": "#fd154c", "info": "#1adffd"}},
        "spectra": {"accent": "#4a4a4a", "warm": "#ff2020", "cool": "#00e0ff", "comp": "#c8ff00",
                     "semantics": {"success": "#c8fe1c", "warning": "#ffea1c", "error": "#fd151a", "info": "#1adffd"}},
        "coldsnap": {"accent": "#4a4a4a", "warm": "#7cffb0", "cool": "#7a8cff", "comp": "#c0a0ff",
                      "semantics": {"success": "#1dfd95", "warning": "#ffea1c", "error": "#fd154c", "info": "#5155fd"}},
    }


    def semantic_search(self, query, top_k=5):
        """Search hyperspace by meaning — direct call to the RAG engine."""
        try:
            import sys
            hypervisor_dir = str(HYPERSPACE_ROOT / ".hypervisor")
            if hypervisor_dir not in sys.path:
                sys.path.insert(0, hypervisor_dir)
            from hv_mcp.rag import get_rag
            rag = get_rag()
            rag.reindex_changed()
            return rag.search(query=query, top_k=top_k)
        except Exception as e:
            logger.error("semantic_search bridge failed: %s", e)
            return []

    def get_accent(self):
        """Read theme from hypervisor's preferences.json and return full palette."""
        prefs_file = HYPERVISOR_DIR / "preferences.json"
        try:
            data = json.loads(prefs_file.read_text(encoding="utf-8"))
            theme_mode = data.get("hypervisor-theme-mode", "custom")
            gradient_map = data.get("hypervisor-gradient-map", "")

            if theme_mode == "preset" and gradient_map:
                preset = self.GRADIENT_MAPS.get(gradient_map)
                if not preset:
                    preset = data.get("userGradientMaps", {}).get(gradient_map)
                if preset:
                    return {
                        "accent": preset["accent"],
                        "warm": preset["warm"],
                        "cool": preset["cool"],
                        "comp": preset["comp"],
                        "semantics": preset.get("semantics"),
                        "mode": "preset",
                        "gradientMap": gradient_map,
                    }

            accent = data.get("hypervisor-accent", "#00ff41")
            mode = data.get("hypervisor-palette-mode", "split")
        except Exception:
            accent, mode = "#00ff41", "split"
        palette = build_palette_oklch(accent, mode)
        palette["mode"] = "custom"
        return palette

    def get_steering(self):
        """Scan .kiro/steering/ and return list of files with their inclusion mode."""
        steering_dir = PORTAL_ROOT / ".kiro" / "steering"
        if not steering_dir.exists():
            return []
        files = []
        for f in sorted(steering_dir.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8")[:500]
                inclusion = "manual"
                if text.startswith("---"):
                    end = text.index("---", 3)
                    front = text[3:end]
                    for line in front.strip().splitlines():
                        if line.startswith("inclusion:"):
                            inclusion = line.split(":", 1)[1].strip()
                files.append({"name": f.stem, "inclusion": inclusion})
            except Exception:
                continue
        return files

    def _is_session_locked(self, session_id):
        """Check if a session lock file is held by a running process (Windows)."""
        try:
            lock_file = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli" / f"{session_id}.lock"
            if not lock_file.exists():
                return False
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            if pid == 0:
                return False
            if pid in self._get_own_kiro_pids():
                return False
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False

    def _get_own_kiro_pids(self):
        """Get PIDs of all kiro-cli processes owned by any tab in our pool."""
        pids = set()
        try:
            sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
            sessions_to_check = set()
            for client in self._pool._clients.values():
                sessions_to_check.update(client._owned_sessions)
                if client._session_id:
                    sessions_to_check.add(client._session_id)
            for sid in sessions_to_check:
                lock_file = sessions_dir / f"{sid}.lock"
                if lock_file.exists():
                    data = json.loads(lock_file.read_text(encoding="utf-8"))
                    pid = int(data.get("pid", 0))
                    if pid:
                        pids.add(pid)
        except Exception:
            pass
        return pids

    def list_sessions(self):
        """List sessions by reading metadata directly from the filesystem."""
        if _check_auth() is AUTH_NO:
            return {"sessions": [], "active": None, "auth_required": True}
        try:
            sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
            if not sessions_dir.exists():
                return {"sessions": [], "active": self._acp._session_id}
            project_cwd = str(PORTAL_ROOT).replace("\\", "/")
            sessions = []
            now = datetime.now(timezone.utc)
            for meta_file in sessions_dir.glob("*.json"):
                try:
                    data = json.loads(meta_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if data.get("cwd", "").rstrip("/") != project_cwd.rstrip("/"):
                    continue
                sid = meta_file.stem
                title = data.get("title", "(no title)") or "(no title)"
                if len(title) > 40:
                    title = title[:40].rstrip() + "..."
                updated = data.get("updated_at") or data.get("created_at", "")
                age = self._relative_age(updated, now)
                jsonl_file = sessions_dir / f"{sid}.jsonl"
                msg_count = 0
                if jsonl_file.exists():
                    try:
                        with open(jsonl_file, "r", encoding="utf-8") as f:
                            msg_count = sum(1 for _ in f)
                    except OSError:
                        pass
                sessions.append({
                    "id": sid, "age": age,
                    "title": title, "msgs": f"{msg_count} msgs",
                    "locked": self._is_session_locked(sid),
                    "_updated": updated,
                })
            sessions.sort(key=lambda s: s.get("_updated", ""), reverse=True)
            for s in sessions:
                del s["_updated"]
            saved_titles = self._acp._load_prefs().get("sessionTitles", {})
            for s in sessions:
                if s["id"] in saved_titles:
                    s["title"] = saved_titles[s["id"]]
            return {"sessions": sessions, "active": self._acp._session_id}
        except Exception as e:
            logger.error(f"list_sessions error: {e}")
            return {"sessions": [], "active": None}

    @staticmethod
    def _relative_age(iso_str, now):
        """Convert an ISO timestamp to a human-readable relative age."""
        try:
            iso_str = re.sub(r'(\.\d{6})\d+', r'\1', iso_str)
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            delta = now - dt
            secs = int(delta.total_seconds())
            if secs < 60:
                return f"{secs} seconds ago"
            mins = secs // 60
            if mins < 60:
                return f"{mins} minute{'s' if mins != 1 else ''} ago"
            hours = mins // 60
            if hours < 24:
                return f"{hours} hour{'s' if hours != 1 else ''} ago"
            days = hours // 24
            return f"{days} day{'s' if days != 1 else ''} ago"
        except Exception:
            return ""

    def rename_session(self, session_id, new_title):
        """Rename a session by updating the custom title in preferences."""
        if not new_title:
            return False
        new_title = new_title.strip()[:60]
        try:
            if not session_id:
                client = self._pool.active_client
                session_id = client._session_id if client else None
            if not session_id:
                return False
            prefs = self._pool._load_prefs()
            titles = prefs.get("sessionTitles", {})
            titles[session_id] = new_title
            prefs["sessionTitles"] = titles
            self._pool._save_prefs(prefs)
            for tab_id, client in self._pool._clients.items():
                if client._session_id == session_id:
                    client._tab_title = new_title
                    client._push_js("__acpSessionTitle", {"title": new_title})
            self._pool.save_tab_state()
            return True
        except Exception as e:
            logger.error(f"rename_session error: {e}")
            return False

    def load_session(self, session_id):
        """Load an existing session by ID."""
        logger.info("load_session: session=%s (into active tab=%s)",
                    session_id[:8], str(self._pool._active_tab)[:6] if self._pool._active_tab else "-")
        for tab_id, client in self._pool._clients.items():
            if tab_id != self._pool._active_tab and client._session_id == session_id:
                logger.warning("load_session: session %s already in tab=%s", session_id[:8], tab_id[:6])
                self._acp._push_js("__acpError", {
                    "error": "Session already open in another tab"
                })
                return
        threading.Thread(
            target=self._load_session_async, args=(session_id,), daemon=True
        ).start()

    def delete_session(self, session_id):
        """Delete a session by removing its files directly."""
        if session_id == self._acp._session_id:
            return False
        try:
            sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
            for f in sessions_dir.glob(f"{session_id}*"):
                if f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    def _delete_session_files(self, session_id):
        """Remove session files from disk (used to clean up throwaway sessions)."""
        try:
            sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
            for f in sessions_dir.glob(f"{session_id}*"):
                if f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink(missing_ok=True)
        except Exception:
            pass


    def get_session_history(self, session_id):
        """Read messages from a session's JSONL file."""
        sessions_dir = Path(os.environ.get("USERPROFILE", "")) / ".kiro" / "sessions" / "cli"
        jsonl_file = sessions_dir / f"{session_id}.jsonl"
        if not jsonl_file.exists():
            return []
        try:
            messages = []
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    kind = entry.get("kind")
                    data = entry.get("data", {})
                    content = data.get("content", [])
                    ts_meta = entry.get("meta") or data.get("meta") or {}
                    ts = ts_meta.get("timestamp") if isinstance(ts_meta, dict) else None
                    if kind == "Prompt":
                        for c in content:
                            if c.get("kind") == "text" and c.get("data"):
                                messages.append({"role": "user", "text": c["data"], "ts": ts})
                                break
                    elif kind == "AssistantMessage":
                        text_parts = []
                        tools = []
                        for c in content:
                            if c.get("kind") == "text" and c.get("data"):
                                text_parts.append(c["data"])
                            elif c.get("kind") == "toolUse":
                                td = c.get("data", {})
                                tool_name = td.get("name", "unknown")
                                tool_input = td.get("input", {})
                                skill_match = None
                                if tool_name == "read":
                                    ops = tool_input.get("operations", [])
                                    for op in ops:
                                        p = op.get("path", "")
                                        if p:
                                            skill_match = _SKILL_MD_PATTERN.search(p)
                                            if skill_match:
                                                break
                                    if not skill_match:
                                        p = tool_input.get("path", "")
                                        if p:
                                            skill_match = _SKILL_MD_PATTERN.search(p)
                                if skill_match:
                                    skill_key = skill_match.group(1)
                                    meta = _SKILL_CACHE.get(skill_key, {"name": skill_key, "description": ""})
                                    tools.append({
                                        "role": "skill",
                                        "name": meta.get("name", skill_key),
                                        "description": meta.get("description", ""),
                                    })
                                else:
                                    tools.append({
                                        "role": "tool",
                                        "name": tool_name,
                                        "toolUseId": td.get("toolUseId", ""),
                                    })
                        combined_text = "".join(text_parts).strip()
                        if combined_text:
                            messages.append({"role": "agent", "text": combined_text, "ts": ts})
                        for t in tools:
                            messages.append(t)
                    elif kind == "ToolResults":
                        pass
            return messages
        except Exception as e:
            logger.error(f"get_session_history error: {e}")
            return []

    def _load_session_async(self, session_id):
        self._acp._state = "starting"
        self._acp._push_state()

        history = self.get_session_history(session_id)
        self._acp._push_js("__acpSessionLoaded", {"sessionId": session_id, "messages": history})

        throwaway_to_clean = [None]

        def on_load_result(result):
            if isinstance(result, dict) and "error" in result:
                err = result["error"]
                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                logger.error(f"sidebar load failed: {result}")
                self._acp._push_js("__acpError", {"error": f"Failed to load session: {err_msg}", "source": "jsonrpc"})
            else:
                self._acp._session_id = session_id
                self._acp._save_session_id(session_id)
                if throwaway_to_clean[0]:
                    self._delete_session_files(throwaway_to_clean[0])
            self._acp._state = "ready"
            self._acp._push_state()

        def on_new_done(result):
            throwaway_id = result.get("sessionId") if isinstance(result, dict) else None
            if throwaway_id:
                self._acp._owned_sessions.add(throwaway_id)
                throwaway_to_clean[0] = throwaway_id
            self._acp._request("session/load", {
                "sessionId": session_id,
                "cwd": str(PORTAL_ROOT).replace("\\", "/"),
                "mcpServers": []
            }, on_load_result)

        cwd = str(PORTAL_ROOT).replace("\\", "/")
        self._acp._request("session/new", {"cwd": cwd, "mcpServers": []}, on_new_done)
