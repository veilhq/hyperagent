"""Per-tab loading lifecycle, run against the built app in headless Edge.

Writes the built HTML plus a stub pywebview bridge and a scripted event
sequence to a temp file, dumps the DOM after the timers run, and checks the
results the script recorded. Usage: python tests/check_tab_loading.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generated_html import HTML  # noqa: E402

EDGE = next((p for p in (
    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
) if os.path.exists(p)), None)

STUB = """
<script>
// Headless virtual time stops servicing requestAnimationFrame; frame it on a timer.
window.requestAnimationFrame = function(cb) { return setTimeout(function() { cb(performance.now()); }, 16); };
window.cancelAnimationFrame = function(id) { clearTimeout(id); };
window.__calls = [];
window.__errors = [];
window.addEventListener('error', function(e) { window.__errors.push(String(e.message) + ' @' + e.lineno); });
window.pywebview = { api: new Proxy({}, { get: function(_, name) {
  return function() {
    window.__calls.push([name].concat([].slice.call(arguments)));
    if (name === 'open_session_in_tab') return Promise.resolve('t' + String(arguments[0]).slice(1));
    return Promise.resolve(null);
  };
}})};
</script>
"""

SCRIPT = """
<script>
(function() {
  var out = {};
  function box(id) { return tabs[id].msgsEl; }
  function has(id, sel) { return !!box(id).querySelector(sel); }
  function msgCount(id) { return box(id).querySelectorAll('.msg').length; }
  function history(n) {
    var m = [];
    for (var i = 0; i < n; i++) m.push({ role: i % 2 ? 'agent' : 'user', text: 'message ' + i });
    return m;
  }
  setTimeout(function() {
    __acpStateChange({ state: 'starting', _tabId: 't1' });
    __acpStateChange({ state: 'ready', _tabId: 't1' });
    out.bootSplashGone = !document.querySelector('#ha-splash:not(.hidden)');
    openInNewTab('s2', 'Two');
  }, 300);
  setTimeout(function() {
    __acpStateChange({ state: 'starting', _tabId: 't2' });
    out.t2Loading = has('t2', '.tab-loading');
    out.t1NoLoading = !has('t1', '.tab-loading');
    out.noFullScreenOverlay = !document.querySelector('.ha-splash:not(.hidden)');
    __acpStartSlow({ agent: 'Claude Code', _tabId: 't2' });
    out.slowText = box('t2').querySelector('.tab-loading-text').textContent;
    switchTab('t1');
    out.t1Active = activeTabId === 't1' && box('t1').style.display !== 'none' && box('t2').style.display === 'none';
    var t2Block = box('t2').querySelector('.tab-loading');
    __acpSessionLoaded({ sessionId: 's2', messages: history(12), _tabId: 't2' });
    out.t2BlockKeptAcrossLoad = box('t2').querySelector('.tab-loading') === t2Block;
    __acpStateChange({ state: 'ready', _tabId: 't2' });
    out.t2FadingOnReady = t2Block.classList.contains('tab-loading-exit') && t2Block.parentNode === box('t2');
    out.t2HistoryHiddenDuringFade = box('t2').classList.contains('tab-connecting');
    out.t1Untouched = msgCount('t1') === 0;
    openInNewTab('s3', 'Three');
  }, 600);
  setTimeout(function() {
    out.t2RenderedInBackground = msgCount('t2') === 12 && !has('t2', '.tab-loading') && !box('t2').classList.contains('tab-connecting');
    __acpStateChange({ state: 'starting', _tabId: 't3' });
    __acpSessionLoadFailed({ sessionId: 's3', error: 'no response after 90s', _tabId: 't3' });
    __acpStateChange({ state: 'ready', _tabId: 't3' });
    out.t3FailedPanel = has('t3', '.tab-load-failed') && !has('t3', '.tab-loading');
    out.t3ErrorText = box('t3').querySelector('.tab-load-error').textContent;
    out.sendEnabledAfterFailure = !sendBtn.disabled;
    box('t3').querySelector('[data-testid="retry-session-load-button"]').click();
    out.retryCalled = __calls.some(function(c) { return c[0] === 'load_session' && c[1] === 's3'; });
    out.t3LoadingAfterRetry = has('t3', '.tab-loading');
    // Render into the active tab, then switch away before it finishes.
    __acpSessionLoaded({ sessionId: 's3', messages: history(40), _tabId: 't3' });
    switchTab('t1');
  }, 900);
  setTimeout(function() {
    out.t3MessageCount = msgCount('t3');
    out.t3AllMessages = msgCount('t3') === 40;
    out.t1StillClean = msgCount('t1') === 0;
    out.t3SendHoldReleased = tabs.t3.renderState._loadingHistory === false;
    // kiro order: history from disk arrives while the agent is still starting.
    openInNewTab('s4', 'Four');
  }, 2500);
  setTimeout(function() {
    __acpStateChange({ state: 'starting', _tabId: 't4' });
    __acpSessionLoaded({ sessionId: 's4', messages: history(3), _tabId: 't4' });
  }, 2800);
  setTimeout(function() {
    var visible = [].filter.call(box('t4').children, function(c) { return getComputedStyle(c).display !== 'none'; });
    out.t4HistoryRenderedHidden = msgCount('t4') === 3 && visible.length === 1 && visible[0].classList.contains('tab-loading');
    out.t4LoadingText = box('t4').querySelector('.tab-loading-text').textContent === 'Connecting to session';
    __acpStateChange({ state: 'ready', _tabId: 't4' });
    out.t4FadeStarted = has('t4', '.tab-loading.tab-loading-exit');
    out.t4FadeOpacityAnimates = parseFloat(getComputedStyle(box('t4').querySelector('.tab-loading')).transitionDuration) > 0;
  }, 3300);
  setTimeout(function() {
    var shown = [].filter.call(box('t4').children, function(c) { return getComputedStyle(c).display !== 'none'; });
    out.t4RevealedAfterFade = !has('t4', '.tab-loading') && !box('t4').classList.contains('tab-connecting') && shown.length === 3;
    out.errors = window.__errors.length === 0 ? true : window.__errors;
    var pre = document.createElement('pre');
    pre.id = 'test-result';
    pre.textContent = JSON.stringify(out);
    document.body.appendChild(pre);
  }, 3800);
})();
</script>
"""


def main():
    if not EDGE:
        print("SKIP: Microsoft Edge not found")
        return
    html = HTML.replace("<head>", "<head>" + STUB, 1).replace("</body>", SCRIPT + "</body>", 1)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "tab-loading.html"
        page.write_text(html, encoding="utf-8")
        proc = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
             "--user-data-dir=" + str(Path(tmp) / "profile"),
             "--virtual-time-budget=6000", "--dump-dom", page.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
        )
    match = re.search(r'<pre id="test-result">(.*?)</pre>', proc.stdout, re.S)
    if not match:
        print("FAIL: no result in dumped DOM")
        print(proc.stderr[-2000:])
        sys.exit(1)
    result = json.loads(match.group(1).replace("&quot;", '"').replace("&amp;", "&"))
    failed = [k for k, v in result.items() if v is False]
    print(json.dumps(result, indent=1))
    if result.get("slowText") != "Still starting Claude Code...":
        failed.append("slowText")
    if result.get("t3ErrorText") != "no response after 90s":
        failed.append("t3ErrorText")
    if failed:
        print("FAIL:", ", ".join(failed))
        sys.exit(1)
    print("tab loading lifecycle: ok")


if __name__ == "__main__":
    main()
