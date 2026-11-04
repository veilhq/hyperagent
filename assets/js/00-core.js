/* ===== Hyperagent: Core ===== */

const $ = (s) => document.querySelector(s);

/* ---- Chip render helper --------------------------------
   Mirrors `render_chip()` from Hypervisor's `site_utils/chips.py` - same
   variant vocabulary, same signature.

   Semantic vocabulary:
   - 'filled'          → live/current (active status, ID chips)
   - 'outlined-accent' → structured/notable (project, "NEW" markers)
   - 'outlined-muted'  → historical/quiet (idle status, tags, badges like "IN USE")

   Usage:
     renderChip('filled', 'ABC-42')
     renderChip('outlined-muted', 'IN USE', 'session-lock')
     renderChip('filled', 'ready', 'topbar-status', {state: 'ready'})
*/
const CHIP_VARIANTS = ['filled', 'outlined-accent', 'outlined-muted'];
function renderChip(variant, text, extraClass, dataAttrs) {
  if (CHIP_VARIANTS.indexOf(variant) === -1) {
    throw new Error("Unknown chip variant '" + variant + "'; expected one of " + CHIP_VARIANTS.join(', '));
  }
  var classes = 'status-chip status-chip-' + variant;
  if (extraClass) classes += ' ' + String(extraClass).trim();
  var attrs = '';
  if (dataAttrs) {
    for (var k in dataAttrs) {
      if (Object.prototype.hasOwnProperty.call(dataAttrs, k)) {
        attrs += ' data-' + k + '="' + dataAttrs[k] + '"';
      }
    }
  }
  return '<span class="' + classes + '"' + attrs + '>' + text + '</span>';
}
window.renderChip = renderChip;  // Explicit promotion for cross-module reads.

/* ---- Toast notifications ----------------------------------------------
   Defined in the shared hyperkit toast module. window.HvToast and
   window.__hypervisorToast are defined there, loaded before this script.
*/

var msgs = $('#messages');
const input = $('#input');
const sendBtn = $('#send-btn');
const cancelBtn = $('#cancel-btn');
const statusEl = $('.topbar-status');
const errorBar = $('.error-bar');
const errorBarMsg = $('.error-bar-msg');
const errorBarActions = $('.error-bar-actions');

/* Structured error bar.
   The bar has two independent slots: a message and an actions area. They are
   written separately because they arrive from separate events - a crash pushes
   __acpStateChange (which offers Reconnect) and __acpError (the message) back
   to back. The previous single-node bar had __acpError overwrite the whole node
   via textContent, destroying the Reconnect link microseconds after it was
   created and leaving closing the tab as the only way to recover. Keeping the
   slots separate makes that class of clobbering impossible. */
window.HaErrorBar = {
  _variants: ['is-warning', 'is-info', 'is-success'],

  // Set the message text with optional semantic variant.
  // variant: 'error' (default) | 'warning' | 'info' | 'success'
  // Uses textContent - never interpolate HTML here.
  setMessage: function (msg, variant) {
    if (!errorBarMsg) return;
    errorBarMsg.textContent = msg == null ? '' : String(msg);
    // Clear previous variant classes (default is error - no class needed)
    for (var i = 0; i < this._variants.length; i++) {
      errorBar.classList.remove(this._variants[i]);
    }
    if (variant && variant !== 'error') {
      errorBar.classList.add('is-' + variant);
    }
    errorBar.classList.add('visible');
  },

  // Replace the action area with a single labelled button.
  setAction: function (label, onClick) {
    if (!errorBarActions) return;
    errorBarActions.textContent = '';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'error-bar-action';
    btn.textContent = label;
    btn.addEventListener('click', onClick);
    errorBarActions.appendChild(btn);
    errorBar.classList.add('visible');
  },

  clearAction: function () {
    if (errorBarActions) errorBarActions.textContent = '';
  },

  hasAction: function () {
    return !!(errorBarActions && errorBarActions.childNodes.length);
  },

  getMessage: function () {
    return errorBarMsg ? errorBarMsg.textContent : '';
  },

  hide: function () {
    errorBar.classList.remove('visible');
    for (var i = 0; i < this._variants.length; i++) {
      errorBar.classList.remove(this._variants[i]);
    }
    if (errorBarMsg) errorBarMsg.textContent = '';
    if (errorBarActions) errorBarActions.textContent = '';
  }
};

/* Keep --ha-cluster-w in sync with the fixed status cluster's real width.
   The error bar shares the top band with the cluster, so it reserves this
   width as padding-right (see .error-bar in 01-layout.css) to stop its
   RECONNECT action from rendering underneath the model picker. The cluster
   resizes whenever the model name, skill strip, or credits chip changes,
   hence the observer rather than a one-shot measurement. */
(function () {
  var cluster = $('.status-cluster');
  if (!cluster) return;
  function sync() {
    var r = cluster.getBoundingClientRect();
    document.documentElement.style.setProperty(
      '--ha-cluster-w', Math.ceil(r.width) + 'px'
    );
    // Publish height too so the error/info bar's band always clears the
    // cluster vertically - the cluster's height moves with its padding, so a
    // measured value keeps the bar correct without a hardcoded max-height.
    document.documentElement.style.setProperty(
      '--ha-cluster-h', Math.ceil(r.height) + 'px'
    );
  }
  sync();
  if (typeof ResizeObserver === 'function') {
    new ResizeObserver(sync).observe(cluster);
  } else {
    window.addEventListener('resize', sync);
  }
})();
const app = $('#app');
const ctxLabel = $('#ctx-label');
const ctxFill = $('#ctx-fill');

var state = 'starting';
var currentMsgEl = null;
var currentMsgText = '';
var toolCards = {};
var currentToolRow = null;
var sessionTitle = '';
var firstPrompt = '';
var _loadingHistory = false;
var commandTurn = null;
var _toolFailTimer = null;

// Context meter update
// `ctxPercentage` is the last-known value, tracked so 08-tabs.js can persist
// it in per-tab render state and restore the meter on tab switch (the DOM
// fill/label alone can't be read back reliably as a percentage).
var ctxPercentage = null;
function updateCtxMeter(pct) {
  if (pct == null) return;
  ctxPercentage = pct;
  var p = Math.round(pct);
  ctxLabel.textContent = 'ctx ' + p + '%';
  ctxFill.style.width = p + '%';
  ctxFill.className = 'ctx-meter-fill' + (p >= 85 ? ' critical' : p >= 65 ? ' warn' : '');
}
// Clear the meter back to its empty state (used when switching to a tab
// that has never had a turn yet, mirroring resetSessionMetrics()).
function clearCtxMeter() {
  ctxPercentage = null;
  ctxLabel.textContent = '';
  ctxFill.style.width = '0%';
  ctxFill.className = 'ctx-meter-fill';
}

// Session credits accumulator
// These four (plus ctxPercentage above) are per-tab metrics - 08-tabs.js
// includes them in _newRenderState()/_saveRenderState()/_loadRenderState()
// and calls updateStatusCenter()/updateCtxMeter() after loading a tab's
// state so the badge bar reflects the active tab's own session, not
// whichever tab most recently finished a turn.
var sessionCredits = 0;
var sessionTokensIn = 0;
var sessionTokensOut = 0;
var sessionTurns = 0;
function updateSessionMetrics(metadata) {
  if (!metadata) return;
  sessionTurns++;
  if (metadata.meteringUsage && metadata.meteringUsage.length) {
    for (var i = 0; i < metadata.meteringUsage.length; i++) sessionCredits += metadata.meteringUsage[i].value;
  } else if (metadata.creditsUsed) {
    sessionCredits += parseFloat(metadata.creditsUsed) || 0;
  }
  if (metadata.inputTokens) sessionTokensIn += metadata.inputTokens;
  if (metadata.outputTokens) sessionTokensOut += metadata.outputTokens;
  updateStatusCenter();
}
function resetSessionMetrics() {
  sessionCredits = 0; sessionTokensIn = 0; sessionTokensOut = 0; sessionTurns = 0;
  updateStatusCenter();
}
function fmtTokens(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return Math.round(n / 1000) + 'k';
  return String(n);
}
function updateStatusCenter() {
  var el = document.getElementById('status-credits');
  if (el) el.textContent = sessionCredits > 0 ? sessionCredits.toFixed(2) + ' cr' : '';
  var tin = document.getElementById('status-tokens-in');
  if (tin) tin.textContent = sessionTokensIn > 0 ? 'in ' + fmtTokens(sessionTokensIn) : '';
  var tout = document.getElementById('status-tokens-out');
  if (tout) tout.textContent = sessionTokensOut > 0 ? 'out ' + fmtTokens(sessionTokensOut) : '';
  var turns = document.getElementById('status-turns');
  if (turns) turns.textContent = sessionTurns > 0 ? sessionTurns + (sessionTurns === 1 ? ' turn' : ' turns') : '';
}

// Sets the HyperKit tooltip text on a chip; empty text removes the tooltip.
function setChipTooltip(el, text) {
  if (text) el.setAttribute('data-tooltip', text);
  else el.removeAttribute('data-tooltip');
}

// Plan credits refresh. `auto` marks the app-start pull: backends whose usage
// report is a slow shell-out (kiro) skip it and keep refreshing on click only.
function refreshPlanCredits(auto) {
  var btn = document.getElementById('plan-credits-refresh');
  var label = document.getElementById('plan-credits-label');
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_plan_usage) return;
  if (btn) btn.classList.add('spinning');
  pywebview.api.get_plan_usage(auto === true).then(function(data) {
    if (btn) btn.classList.remove('spinning');
    if (data && data.skipped) return;
    if (data && Array.isArray(data.windows)) { applyPlanUsagePull(data); return; }
    if (data && data.ok && data.rateLimit) { applyRateLimitReport(data.rateLimit); return; }
    if (!data || !data.ok) {
      if (planUsageHasData()) { renderPlanUsage(); return; }
      if (label) { label.textContent = '?'; setChipTooltip(label, (data && data.error) || ''); }
      return;
    }
    if (label) {
      label.textContent = 'plan ' + data.used + ' / ' + data.total + ' cr';
      label.className = 'ha-cluster-chip plan-credits-label' + (data.used_pct >= 90 ? ' critical' : data.used_pct >= 70 ? ' warn' : '');
      setChipTooltip(label, data.detail || '');
    }
  }).catch(function() {
    if (btn) btn.classList.remove('spinning');
    if (label && !planUsageHasData()) label.textContent = '?';
  });
}
window.refreshPlanCredits = refreshPlanCredits;

// Claude plan usage, merged from two sources: the /usage pull (every window,
// at app start and on refresh) and pushed rate-limit reports (one window each,
// whenever Claude Code's limit state changes). Windows are keyed by
// rate-limit type; utilization is 0-100.
var RATE_LIMIT_LABELS = {
  five_hour: '5h', seven_day: 'week', seven_day_opus: 'week opus',
  seven_day_sonnet: 'week sonnet', seven_day_overage_included: 'week', overage: 'extra'
};
var RATE_LIMIT_NAMES = {
  five_hour: 'five-hour', seven_day: 'weekly', seven_day_opus: 'weekly Opus',
  seven_day_sonnet: 'weekly Sonnet', seven_day_overage_included: 'weekly', overage: 'extra usage'
};
// Model-specific windows: switching to another model keeps work going.
var MODEL_SCOPED_LIMITS = { seven_day_opus: true, seven_day_sonnet: true };
var planUsage = { windows: {}, fetchedAt: null, stale: false, staleReason: '' };
var _rateLimitWarned = {};
var _rateLimitBannerKey = null;

function planUsageHasData() {
  return Object.keys(planUsage.windows).length > 0;
}

function fmtReset(win) {
  if (typeof win.resetsAt === 'number') {
    var when = new Date(win.resetsAt * 1000);
    var soon = when.getTime() - Date.now() < 86400000;
    return soon ? when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
      : when.toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
  }
  return win.resetsText || '';
}

function fmtAge(epochSeconds) {
  var mins = Math.max(0, Math.round((Date.now() / 1000 - epochSeconds) / 60));
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + ' min ago';
  var hours = Math.round(mins / 60);
  return hours < 48 ? hours + ' h ago' : Math.round(hours / 24) + ' d ago';
}

var FIVE_HOUR_SECONDS = 5 * 3600;

function fmtClock(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function fmtRemaining(seconds) {
  var mins = Math.max(1, Math.ceil(seconds / 60));
  var h = Math.floor(mins / 60);
  return h ? h + 'h ' + (mins % 60) + 'm' : mins + 'm';
}

// Five-hour window span: it opens at the first prompt and closes at resetsAt.
function fiveHourSpan(win) {
  if (!win || typeof win.resetsAt !== 'number') return null;
  var left = win.resetsAt - Date.now() / 1000;
  return { start: win.resetsAt - FIVE_HOUR_SECONDS, end: win.resetsAt, left: left, ended: left <= 0 };
}

function renderPlanUsage() {
  var label = document.getElementById('plan-credits-label');
  if (!label) return;
  var keys = Object.keys(planUsage.windows);
  if (!keys.length) return;
  // Lead with the five-hour window; otherwise the fullest one.
  var leadKey = planUsage.windows.five_hour ? 'five_hour' : keys.reduce(function(best, k) {
    return (planUsage.windows[k].utilization || 0) > (planUsage.windows[best].utilization || 0) ? k : best;
  }, keys[0]);
  var lead = planUsage.windows[leadKey];
  var name = RATE_LIMIT_LABELS[leadKey] || lead.label || 'plan';
  var text = lead.status === 'rejected' ? name + ' limit'
    : typeof lead.utilization === 'number' ? name + ' ' + Math.round(lead.utilization) + '%'
    : name + (lead.status === 'allowed_warning' ? ' !' : ' ok');
  var span = leadKey === 'five_hour' ? fiveHourSpan(lead) : null;
  if (span) {
    // Past its end the old percentage no longer applies; the next prompt opens a new window.
    if (span.ended) text = name + ' reset';
    else text += ' - ' + fmtRemaining(span.left) + ' left';
  }
  var critical = false, warn = false;
  var tipLines = ['Claude plan usage'];
  if (span) {
    tipLines.push(span.ended
      ? '5h window ended ' + fmtClock(span.end) + ' - the next prompt starts a new one'
      : '5h window: ' + fmtClock(span.start) + ' to ' + fmtClock(span.end)
        + ' (' + fmtRemaining(span.left) + ' left)');
  }
  keys.forEach(function(k) {
    var win = planUsage.windows[k];
    var pct = typeof win.utilization === 'number' ? win.utilization : null;
    if (k === 'five_hour' && span && span.ended) pct = null;
    if (win.status === 'rejected' || (pct !== null && pct >= 90)) critical = true;
    else if (win.status === 'allowed_warning' || (pct !== null && pct >= 70)) warn = true;
    var line = (win.label || RATE_LIMIT_NAMES[k] || k) + ': '
      + (win.status === 'rejected' ? 'limit reached' : pct !== null ? Math.round(pct) + '%' : 'ok');
    var reset = fmtReset(win);
    if (reset) line += ' - resets ' + reset;
    tipLines.push(line);
  });
  if (planUsage.fetchedAt && planUsage.stale) {
    tipLines.push('Last-known usage from ' + fmtAge(planUsage.fetchedAt)
      + (planUsage.staleReason ? ' (' + planUsage.staleReason + ')' : ''));
    text += ' (' + fmtAge(planUsage.fetchedAt).replace(' ago', '') + ')';
  }
  label.textContent = 'plan ' + text;
  label.className = 'ha-cluster-chip plan-credits-label' + (critical ? ' critical' : warn ? ' warn' : '');
  setChipTooltip(label, tipLines.join('\n'));
}

// Pull result: {windows: [{key, label, utilization, resetsAt, resetsText}], fetchedAt, stale, error}.
function applyPlanUsagePull(data) {
  if (!data.windows.length && planUsageHasData()) {
    planUsage.stale = true;
    planUsage.staleReason = data.error || '';
    renderPlanUsage();
    return;
  }
  var windows = {};
  data.windows.forEach(function(win) {
    var prev = planUsage.windows[win.key];
    // A pushed warning/rejection newer than the pulled data keeps its status.
    var status = prev && prev.status && prev.receivedAt && data.fetchedAt && prev.receivedAt > data.fetchedAt
      ? prev.status : null;
    windows[win.key] = { label: win.label, utilization: win.utilization, resetsAt: win.resetsAt,
                         resetsText: win.resetsText, status: status, receivedAt: data.fetchedAt };
  });
  planUsage.windows = windows;
  planUsage.fetchedAt = data.fetchedAt || null;
  planUsage.stale = !!data.stale;
  planUsage.staleReason = data.stale ? (data.error || '') : '';
  if (!planUsageHasData()) {
    var label = document.getElementById('plan-credits-label');
    if (label) { label.textContent = '?'; setChipTooltip(label, data.error || 'No Claude plan usage reported'); }
    return;
  }
  renderPlanUsage();
}

// Pushed report: {status, rateLimitType, utilization 0-1, resetsAt epoch s, receivedAt}.
function applyRateLimitReport(rl) {
  if (!rl) return;
  var key = rl.rateLimitType || null;
  if (key) {
    var win = planUsage.windows[key] || { label: RATE_LIMIT_NAMES[key] || key };
    if (typeof rl.utilization === 'number') win.utilization = rl.utilization * 100;
    if (typeof rl.resetsAt === 'number') { win.resetsAt = rl.resetsAt; win.resetsText = ''; }
    win.status = rl.status || null;
    win.receivedAt = rl.receivedAt || Date.now() / 1000;
    planUsage.windows[key] = win;
    renderPlanUsage();
  } else if (rl.status === 'rejected') {
    // A rejected prompt with no window detail: the prompt error already explains it.
    var label = document.getElementById('plan-credits-label');
    if (label && !planUsageHasData()) {
      label.textContent = 'plan limit';
      label.className = 'ha-cluster-chip plan-credits-label critical';
    }
  }
  notifyRateLimit(rl, key);
}

// Toast once per window on a warning; a persistent, dismissable banner when a
// window is exhausted, cleared once that window reports allowed again.
function notifyRateLimit(rl, key) {
  if (!key) return;
  var name = RATE_LIMIT_NAMES[key] || key;
  var win = planUsage.windows[key] || {};
  var reset = fmtReset(win);
  if (rl.status === 'allowed_warning') {
    var warnKey = key + ':' + (rl.resetsAt || '');
    if (_rateLimitWarned[warnKey] || !window.HvToast) return;
    _rateLimitWarned[warnKey] = true;
    var pct = typeof win.utilization === 'number' ? ' at ' + Math.round(win.utilization) + '%' : '';
    window.HvToast.show({ variant: 'warn', title: 'Claude ' + name + ' usage' + pct,
                          message: reset ? 'Resets ' + reset + '.' : '' });
  } else if (rl.status === 'rejected') {
    var msg = 'Claude ' + name + ' limit reached' + (reset ? ' - resets ' + reset : '') + '. '
      + (MODEL_SCOPED_LIMITS[key] ? 'Switching to another model lets you keep working.'
                                  : 'Switching models will not help until it resets.');
    _rateLimitBannerKey = key;
    window.HaErrorBar.setMessage(msg, 'warning');
    window.HaErrorBar.setAction('Dismiss', function() {
      _rateLimitBannerKey = null;
      window.HaErrorBar.hide();
    });
  } else if (_rateLimitBannerKey === key) {
    _rateLimitBannerKey = null;
    window.HaErrorBar.hide();
  }
}

window.__acpRateLimit = function(data) { applyRateLimitReport(data); };

// Keep the five-hour countdown current between reports.
setInterval(function() { if (planUsageHasData()) renderPlanUsage(); }, 30000);

// Session permission mode chip. Each tab has its own mode; Python re-pushes
// the active tab's mode on tab switch, and a null id clears the chip.
window.__acpMode = function(data) {
  if (!data) return;
  if (data._tabId && typeof activeTabId !== 'undefined' && activeTabId && data._tabId !== activeTabId) return;
  var el = document.getElementById('session-mode');
  if (!el) return;
  el.textContent = data.id ? String(data.name || data.id) : '';
};

// Apply palette from hypervisor theme
function applyAccent(palette) {
  var hex = typeof palette === 'string' ? palette : palette.accent;
  var root = document.documentElement;
  var rs = root.style;

  // Sync light mode class first
  if (typeof palette === 'object') {
    if (palette.lightMode) {
      root.classList.add('a11y-bw-theme');
    } else {
      root.classList.remove('a11y-bw-theme');
    }
  }

  // In light mode, force blue palette + primary-color semantics
  if (root.classList.contains('a11y-bw-theme')) {
    hex = '#0000ff';
    rs.setProperty('--accent', '#0000ff');
    rs.setProperty('--accent-dim', '#0000cc');
    rs.setProperty('--accent-glow', 'rgba(0,0,255,0.15)');
    rs.setProperty('--accent-border', '#0000ff');
    rs.setProperty('--warm', '#0000ff');
    rs.setProperty('--cool', '#0000ff');
    rs.setProperty('--comp', '#0000ff');
    rs.setProperty('--success', '#007a00');
    rs.setProperty('--warning', '#b35900');
    rs.setProperty('--error', '#cc0000');
    rs.setProperty('--info', '#0000ff');
    rs.setProperty('--surface-active', 'var(--selected-wash)');
  } else {
    var r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
    rs.setProperty('--accent', hex);
    rs.setProperty('--accent-dim', hex + 'cc');
    rs.setProperty('--accent-glow', 'rgba('+r+','+g+','+b+',0.06)');
    rs.setProperty('--accent-border', 'rgba('+r+','+g+','+b+',0.15)');
    if (typeof palette === 'object') {
      rs.setProperty('--warm', palette.warm);
      rs.setProperty('--cool', palette.cool);
      rs.setProperty('--comp', palette.comp);
      // Apply semantic overrides from presets, or reset to defaults
      if (palette.semantics) {
        if (palette.semantics.success) rs.setProperty('--success', palette.semantics.success);
        if (palette.semantics.warning) rs.setProperty('--warning', palette.semantics.warning);
        if (palette.semantics.error) rs.setProperty('--error', palette.semantics.error);
        if (palette.semantics.info) rs.setProperty('--info', palette.semantics.info);
      } else {
        rs.setProperty('--success', '#00ff41');
        rs.setProperty('--warning', '#ffb000');
        rs.setProperty('--error', '#ff3333');
        rs.setProperty('--info', '#00cccc');
      }
      rs.setProperty('--surface-active', 'var(--selected-wash)');
    }
  }
  // Dynamic cursors synced to accent
  var ec = encodeURIComponent(hex);
  rs.setProperty('--cursor-default', "url(\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='" + ec + "' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z'/></svg>\") 2 2, auto");
  rs.setProperty('--cursor-pointer', "url(\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='" + ec + "' stroke='" + ec + "' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z'/></svg>\") 2 2, pointer");
  rs.setProperty('--cursor-text', "url(\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='" + ec + "' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M17 22h-1a4 4 0 0 1-4-4V6a4 4 0 0 1 4-4h1'/><path d='M7 22h1a4 4 0 0 0 4-4V6a4 4 0 0 0-4-4H7'/></svg>\") 10 10, text");
}
window.applyAccent = applyAccent;
if (window.pywebview && window.pywebview.api) {
  pywebview.api.get_accent().then(applyAccent);
} else {
  window.addEventListener('pywebviewready', function() {
    pywebview.api.get_accent().then(applyAccent);
  });
}
