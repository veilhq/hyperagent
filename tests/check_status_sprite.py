"""Status sprite checks: the tab-to-status fold, the activity stamps, and the built page in headless Edge.

The page runs against a stub bridge that walks through every stage; the script
records the label, the flash color, and the theme switch. Usage: python tests/check_status_sprite.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generated_html import SPRITE_HTML  # noqa: E402
from sprite_window import reduce_status  # noqa: E402

EDGE = next((p for p in (
    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
) if os.path.exists(p)), None)

FAILURES = []


def expect(name, actual, wanted):
    ok = actual == wanted
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {actual!r}, wanted {wanted!r}"))
    if not ok:
        FAILURES.append(name)


def check_reduce():
    now = 10_000.0
    expect("no tabs ready sleeps", reduce_status(["stopped", "starting"], [None], [], now)["closed"], True)
    expect("any prompting tab is busy", reduce_status(["ready", "prompting"], [None, None], [], now)["busy"], True)
    expect("ready tab is awake", reduce_status(["ready"], [None], [], now)["closed"], False)
    expect("error age from newest error", reduce_status(["ready", "ready"], [now - 90, now - 5], [], now)["error_age"], 5.0)
    expect("old errors drop off", reduce_status(["ready"], [now - 700], [], now)["error_age"], None)
    expect("crashed tab without an error stamp beacons", reduce_status(["crashed"], [None], [], now)["error_age"], 30.0)
    expect("recent turn ends give a rate", reduce_status(["ready"], [None], [now - 40, now - 3], now)["rate"], 2)
    expect("rate is zero once the last turn is stale", reduce_status(["ready"], [None], [now - 40, now - 25], now)["rate"], 0)
    expect("a tab with an open prompt is waiting", reduce_status(["prompting", "ready"], [None, None], [], now, waiting=[True, False])["waiting"], True)
    expect("a crashed tab is not waiting", reduce_status(["crashed"], [None], [], now, waiting=[True])["waiting"], False)
    expect("success age from newest clean finish", reduce_status(["ready", "ready"], [None, None], [], now, success_times=[now - 4, now - 2])["success_age"], 2.0)
    expect("success fades after a few seconds", reduce_status(["ready"], [None], [], now, success_times=[now - 30])["success_age"], None)


def check_stamps():
    from acp_client import ACPClient
    client = ACPClient()
    client._push_js("__acpError", {"error": "noise", "source": "stderr"})
    expect("stderr is not an error", client.last_error_at, None)
    client._push_js("__acpError", {"error": "Prompt failed", "source": "jsonrpc"})
    expect("prompt failure stamps an error", client.last_error_at is not None, True)
    client._push_js("__acpTurnEnd", {})
    client._push_js("__acpTurnEnd", {})
    expect("turn ends are stamped", len(client.turn_end_times), 2)
    expect("a clean turn end is a success", client.last_success_at is not None, True)
    client.last_success_at = None
    client._push_js("__acpTurnEnd", {"_cancelled": True})
    client._push_js("__acpTurnEnd", {"_failed": True})
    client._push_js("__acpTurnEnd", {"stopReason": "max_tokens"})
    expect("cancelled, failed, and cut-off turns are not a success", client.last_success_at, None)
    client._push_js("__acpQuestion", {"toolCallId": "q1"})
    client._push_js("__acpWorkflowPlan", {"toolCallId": "w1"})
    expect("open question and plan are waiting", len(client.waiting_on), 2)
    client._push_js("__acpQuestionClosed", {"toolCallId": "q1"})
    client._push_js("__acpWorkflowPlanClosed", {"toolCallId": "w1"})
    expect("answered prompts stop waiting", len(client.waiting_on), 0)
    client._push_js("__acpAuthRequired", {"url": None})
    expect("login is waiting", len(client.waiting_on), 1)
    client._push_js("__acpTurnEnd", {})
    expect("a turn end clears waiting", len(client.waiting_on), 0)


STUB = """
<script>
window.__status = null;
window.__calls = [];
window.pywebview = { api: {
  get_status: function () { return Promise.resolve(window.__status); },
  get_accent: function () { return Promise.resolve({ accent: '#ff00ff', lightMode: false,
    semantics: { success: '#00ff00', warning: '#ffff00', error: '#ff0000' } }); },
  get_bounds: function () { return Promise.resolve({ x: 0, y: 0, w: 176, h: 176 }); },
  begin_drag: function () { return Promise.resolve(); },
  drag_to: function () { return Promise.resolve(true); },
  end_drag: function () { return Promise.resolve(); },
  set_tile_region: function (r) { window.__calls.push('tile ' + Math.round(r)); return Promise.resolve(); }
} };
</script>
"""

SCRIPT = """
<script>
(function () {
  var calm = { busy: false, closed: false, error_age: null, rate: 0, waiting: false, success_age: null };
  function at(changes) { return Object.assign({}, calm, changes); }
  // Each stage: expected label, status, and the tint its flash should reach.
  var stages = [
    ['sleep', at({ closed: true }), [255, 0, 255]],
    ['idle', at({}), [255, 0, 255]],
    ['live 3/m', at({ rate: 3 }), [255, 0, 255]],
    ['busy', at({ busy: true }), [255, 0, 255]],
    ['done', at({ success_age: 1, rate: 1 }), [0, 255, 0]],
    ['needs input', at({ busy: true, waiting: true }), [255, 255, 0]],
    ['error 4s', at({ busy: true, waiting: true, error_age: 4 }), [255, 0, 0]],
    ['error 90s', at({ error_age: 90 }), [255, 0, 0]]
  ];
  var result = { labels: [], flash: [], lit: [], stray: [] };
  var TINTS = [[255, 0, 255], [0, 255, 0], [255, 255, 0], [255, 0, 0]];
  function sample(rgb) {
    var canvas = document.getElementById('matrix');
    var data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    var lit = 0, found = false, stray = false;
    for (var i = 0; i < data.length; i += 4) {
      if (data[i + 3] === 0) continue;
      lit++;
      TINTS.forEach(function (tint) {
        if (data[i] !== tint[0] || data[i + 1] !== tint[1] || data[i + 2] !== tint[2]) return;
        if (rgb && tint === rgb) found = true;
        else if (!rgb || tint !== rgb) stray = true;
      });
    }
    return { found: found, lit: lit, stray: stray };
  }
  window.dispatchEvent(new Event('pywebviewready'));
  stages.forEach(function (stage, index) {
    setTimeout(function () { window.__status = stage[1]; }, 500 + index * 4000);
    result.flash[index] = false;
    result.stray[index] = false;
    var tint = stage[2] && TINTS.filter(function (each) { return each.join() === stage[2].join(); })[0];
    for (var ms = 100; ms < 3900; ms += 100) {
      setTimeout(function (ms) {
        var seen = sample(tint);
        if (seen.found) result.flash[index] = true;
        // Skip until the 2s status poll has picked up the new stage.
        if (seen.stray && ms > 2600) result.stray[index] = true;
        result.lit[index] = seen.lit > 0;
      }, 500 + index * 4000 + ms, ms);
    }
    setTimeout(function () { result.labels.push(document.getElementById('label').textContent); }, 500 + index * 4000 + 3800);
  });
  setTimeout(function () {
    window.applySpriteTheme({ accent: '#ff00ff', lightMode: true });
    var root = document.documentElement;
    result.lightClass = root.classList.contains('a11y-bw-theme');
    result.lightDropsAccent = root.style.getPropertyValue('--accent') === '';
    result.tileRegion = window.__calls.some(function (call) { return /^tile [1-9]/.test(call); });
    var pre = document.createElement('pre');
    pre.id = 'test-result';
    pre.textContent = JSON.stringify(result);
    document.body.appendChild(pre);
  }, 500 + stages.length * 4000 + 200);
})();
</script>
"""


def check_page():
    if not EDGE:
        print("skip page checks: Microsoft Edge not found")
        return
    html = SPRITE_HTML.replace("<head>", "<head>" + STUB, 1).replace("</body>", SCRIPT + "</body>", 1)
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "status-sprite.html"
        page.write_text(html, encoding="utf-8")
        proc = subprocess.run(
            [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
             "--user-data-dir=" + str(Path(tmp) / "profile"),
             "--virtual-time-budget=40000", "--dump-dom", page.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    match = re.search(r'<pre id="test-result">(.*?)</pre>', proc.stdout, re.S)
    if not match:
        print("FAIL page: no result in dumped DOM")
        print(proc.stderr[-2000:])
        FAILURES.append("page")
        return
    result = json.loads(match.group(1).replace("&quot;", '"').replace("&amp;", "&"))
    expect("stage labels", result["labels"], ["sleep", "idle", "live 3/m", "busy", "done", "needs input", "error 4s", "error 90s"])
    expect("cells are lit in every stage", result["lit"], [True] * 8)
    expect("busy flashes in the live color", result["flash"][3], True)
    expect("success pulses in the success color", result["flash"][4], True)
    expect("waiting pulses in the warning color", result["flash"][5], True)
    expect("errors pulse in the error color", result["flash"][6:], [True, True])
    expect("no stage shows another stage's color", result["stray"], [False] * 8)
    expect("light mode applies the bw theme", result["lightClass"], True)
    expect("light mode drops the inline accent", result["lightDropsAccent"], True)
    expect("tile region follows the corner radius", result["tileRegion"], True)


def main():
    check_reduce()
    check_stamps()
    check_page()
    if FAILURES:
        print("FAIL:", ", ".join(FAILURES))
        sys.exit(1)
    print("status sprite: ok")


if __name__ == "__main__":
    main()
