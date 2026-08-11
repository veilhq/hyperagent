/* ===== Hyperagent: Background Activity Strip =====
   Exposes background processes that would otherwise be invisible to the user.

   Motivation: title generation ran as a detached background thread that spawned
   its own kiro-cli process and silently renamed the tab several seconds after a
   turn ended. Nothing in the UI indicated it was running, so a slow or failed
   generation was indistinguishable from "nothing happened".

   Public API (window.HaActivity):
     start(key, label)  -> show a badge for an in-flight process
     done(key)          -> mark success, badge lingers briefly then removes
     fail(key)          -> mark failure, same lifecycle with error styling
     clear(key)         -> remove immediately, no terminal state

   Keys are caller-defined and scoped by the caller (e.g. 'title:<tabId>') so
   concurrent processes of the same kind don't collide. */

var _haActivityBadges = {};   // key -> {el, timer}
var _HA_ACTIVITY_LINGER_MS = 900;

function _haActivityStrip() {
  return document.getElementById('activity-strip');
}

function _haActivityBuildBadge(label) {
  var el = document.createElement('span');
  el.className = 'activity-badge';
  var text = document.createElement('span');
  text.className = 'activity-badge-label';
  text.textContent = label;
  var dither = document.createElement('span');
  dither.className = 'activity-dither';
  dither.appendChild(document.createElement('span'));
  dither.appendChild(document.createElement('span'));
  dither.appendChild(document.createElement('span'));
  el.appendChild(text);
  el.appendChild(dither);
  return el;
}

window.HaActivity = {
  start: function (key, label) {
    var strip = _haActivityStrip();
    if (!strip || !key) return;
    // Restarting an existing key reuses its badge rather than stacking a
    // duplicate (e.g. a regenerate fired while the first run is still live).
    var existing = _haActivityBadges[key];
    if (existing) {
      if (existing.timer) clearTimeout(existing.timer);
      existing.el.classList.remove('is-done', 'is-failed');
      existing.timer = null;
      return;
    }
    var el = _haActivityBuildBadge(label || key);
    strip.appendChild(el);
    _haActivityBadges[key] = { el: el, timer: null };
  },

  _terminate: function (key, cls) {
    var rec = _haActivityBadges[key];
    if (!rec) return;
    if (rec.timer) clearTimeout(rec.timer);
    rec.el.classList.add(cls);
    rec.timer = setTimeout(function () {
      if (rec.el && rec.el.parentNode) rec.el.parentNode.removeChild(rec.el);
      delete _haActivityBadges[key];
    }, _HA_ACTIVITY_LINGER_MS);
  },

  done: function (key) { window.HaActivity._terminate(key, 'is-done'); },
  fail: function (key) { window.HaActivity._terminate(key, 'is-failed'); },

  clear: function (key) {
    var rec = _haActivityBadges[key];
    if (!rec) return;
    if (rec.timer) clearTimeout(rec.timer);
    if (rec.el && rec.el.parentNode) rec.el.parentNode.removeChild(rec.el);
    delete _haActivityBadges[key];
  }
};

/* ---- Title generation activity ----
   Driven by __acpTitleActivity pushed from generate_title() in hyperagent.py.
   Phases: start | done | failed. */

function _haTitleKey(tabId) {
  return 'title:' + (tabId || 'active');
}

// Apply/remove the provisional-title styling on a tab row.
function _haSetTabTitlePending(tabId, pending) {
  if (!tabId || typeof tabs === 'undefined') return;
  var tab = tabs[tabId];
  if (!tab || !tab.el) return;
  tab.el.classList.toggle('title-pending', !!pending);
}

// Brief highlight when a generated title lands, so the rename is perceivable
// instead of a silent text swap. Class is removed after the animation so a
// later regenerate can retrigger it.
function _haFlashTitleSettled(tabId) {
  var targets = [];
  if (tabId && typeof tabs !== 'undefined' && tabs[tabId] && tabs[tabId].el) {
    targets.push(tabs[tabId].el);
  }
  // The topbar title only reflects the active tab.
  var topbar = document.getElementById('session-title');
  if (topbar && (!tabId || typeof activeTabId === 'undefined' || tabId === activeTabId)) {
    targets.push(topbar);
  }
  targets.forEach(function (el) {
    el.classList.remove('title-settled');
    // Force a reflow so removing + re-adding restarts the animation.
    void el.offsetWidth;
    el.classList.add('title-settled');
    setTimeout(function () { el.classList.remove('title-settled'); }, 700);
  });
}

window.__acpTitleActivity = function (data) {
  if (!data) return;
  var tabId = data._tabId;
  var key = _haTitleKey(tabId);
  var phase = data.phase;

  if (phase === 'start') {
    // A tab the user has manually renamed is locked — no pending state, and
    // __acpSessionTitle will reject the incoming title as well.
    if (tabId && typeof tabs !== 'undefined' && tabs[tabId] && tabs[tabId].titleLocked) return;
    window.HaActivity.start(key, data.label || 'title');
    _haSetTabTitlePending(tabId, true);
    return;
  }

  if (phase === 'done' || phase === 'failed') {
    _haSetTabTitlePending(tabId, false);
    if (phase === 'done') {
      window.HaActivity.done(key);
      _haFlashTitleSettled(tabId);
    } else {
      window.HaActivity.fail(key);
      // Surface the failure — a silently-heuristic title is exactly the kind of
      // invisible fallback this module exists to expose.
      if (window.HvToast) {
        window.HvToast.show({
          variant: 'error',
          message: 'Title generation failed — using fallback name'
        });
      }
    }
  }
};


/* ---- Crash recovery activity ----
   Driven by __acpRecovery pushed from ACPClient._auto_recover() in hyperagent.py.
   Phases: attempting | recovered | exhausted | failed.

   Recovery used to be entirely invisible and, because __acpError clobbered the
   Reconnect link, effectively unreachable — the only way back was closing and
   reopening the tab. This surfaces the automatic attempt so the user can tell
   "recovering" apart from "dead", and leaves a manual action when it gives up. */

window.__acpRecovery = function (data) {
  if (!data) return;
  var phase = data.phase;
  var key = 'recover:' + (data._tabId || 'active');

  if (phase === 'attempting') {
    var label = 'recover ' + (data.attempt || 1) + '/' + (data.max || 1);
    window.HaActivity.start(key, label);
    if (window.HaErrorBar) {
      window.HaErrorBar.setMessage(
        'Connection lost (code ' + (data.exitCode != null ? data.exitCode : '?') +
        ') — reconnecting automatically...'
      );
      // Automatic attempt in progress; a manual button here would race it.
      window.HaErrorBar.clearAction();
    }
    return;
  }

  if (phase === 'recovered') {
    window.HaActivity.done(key);
    if (window.HaErrorBar) window.HaErrorBar.hide();
    if (window.HvToast) {
      window.HvToast.show({ variant: 'success', message: 'Reconnected automatically' });
    }
    return;
  }

  if (phase === 'exhausted' || phase === 'failed') {
    window.HaActivity.fail(key);
    // Auto-recovery is done trying — hand control back with an explicit action.
    // __acpStateChange('crashed') also installs one; setAction replaces rather
    // than appends, so the user never sees duplicate buttons.
    if (window.HaErrorBar) {
      window.HaErrorBar.setMessage(
        phase === 'exhausted'
          ? 'Automatic recovery failed after ' + (data.attempts || 0) + ' attempts.'
          : 'Automatic recovery failed.'
      );
      var tabId = data._tabId || null;
      window.HaErrorBar.setAction('Reconnect', function () {
        window.HaErrorBar.setMessage('Reconnecting...');
        window.HaErrorBar.clearAction();
        if (window.pywebview && window.pywebview.api && window.pywebview.api.reconnect) {
          pywebview.api.reconnect(tabId);
        }
      });
    }
    if (window.HvToast) {
      window.HvToast.show({ variant: 'error', message: 'Auto-reconnect failed — use Reconnect' });
    }
  }
};


/* ---- Self-update detection (0xC0000138) ----
   Pushed by ACPClient._handle_update_restart() when kiro-cli exits with
   STATUS_DLL_NOT_FOUND — the signature of the auto-updater replacing the
   binary in-place. Shows a calm "updating" banner instead of the alarming
   crash state, and auto-dismisses once the tab reaches ready.

   Phases: waiting | reconnecting | timeout */

// Global flag: set true when an update-restart is in flight.
// Survives error-bar DOM manipulation and intermediate state transitions.
window._updateRestartInFlight = false;

window.__acpUpdating = function (data) {
  if (!data) return;
  var phase = data.phase;
  var key = 'update:' + (data._tabId || 'active');

  if (phase === 'waiting') {
    window._updateRestartInFlight = true;
    window.HaActivity.start(key, 'updating');
    if (window.HaErrorBar) {
      var msg = 'Kiro CLI is updating';
      if (data.poll && data.max) {
        msg += ' (' + data.poll + '/' + data.max + ')';
      }
      msg += ' — will reconnect automatically';
      window.HaErrorBar.setMessage(msg);
      window.HaErrorBar.clearAction();
    }
    // Add updating class to error bar for calm styling
    if (errorBar) errorBar.classList.add('is-updating');
    return;
  }

  if (phase === 'reconnecting') {
    window.HaActivity.start(key, 'reconnecting');
    if (window.HaErrorBar) {
      window.HaErrorBar.setMessage('Update complete — reconnecting...');
      window.HaErrorBar.clearAction();
    }
    return;
  }

  if (phase === 'timeout') {
    window._updateRestartInFlight = false;
    window.HaActivity.fail(key);
    if (errorBar) errorBar.classList.remove('is-updating');
    if (window.HaErrorBar) {
      window.HaErrorBar.setMessage('Update timed out — kiro-cli may still be installing.');
      var tabId = data._tabId || null;
      window.HaErrorBar.setAction('Reconnect', function () {
        window.HaErrorBar.setMessage('Reconnecting...');
        window.HaErrorBar.clearAction();
        if (window.pywebview && window.pywebview.api && window.pywebview.api.reconnect) {
          pywebview.api.reconnect(tabId);
        }
      });
    }
    if (window.HvToast) {
      window.HvToast.show({ variant: 'error', message: 'Update timeout — try Reconnect' });
    }
  }
};

/* Hook into __acpStateChange to clean up updating UI when ready is reached. */
(function () {
  var _origStateChange = window.__acpStateChange;
  window.__acpStateChange = function (data) {
    // Dismiss updating banner on successful reconnect
    var isUpdate = window._updateRestartInFlight ||
                   (errorBar && errorBar.classList.contains('is-updating'));
    if (data && data.state === 'ready' && isUpdate) {
      window._updateRestartInFlight = false;
      if (errorBar) errorBar.classList.remove('is-updating');
      window.HaActivity.done('update:' + (data._tabId || 'active'));
      // Toast with version if available (set by __acpCliVersion which fires just before this)
      var ver = window._kiroCliVersion || '';
      var msg = ver ? 'Updated to v' + ver : 'Updated and reconnected';
      if (window.HvToast) {
        window.HvToast.show({ variant: 'success', message: msg });
      }
    }
    // Delegate to original handler
    if (_origStateChange) _origStateChange(data);
  };
})();


/* ---- CLI version badge ----
   Pushed by ACPClient._push_state() on every ready transition.
   Populates the #cli-version chip in the status cluster. */

window._kiroCliVersion = '';

window.__acpCliVersion = function (data) {
  if (!data || !data.version) return;
  window._kiroCliVersion = data.version;
  var badge = document.getElementById('cli-version');
  if (badge) {
    badge.textContent = 'v' + data.version;
    badge.title = 'Kiro CLI v' + data.version;
  }
};
