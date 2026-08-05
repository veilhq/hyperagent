/* ===== Hyperagent: Core ===== */

const $ = (s) => document.querySelector(s);

/* ---- Chip render helper (WI-113 Phase 3) --------------------------------
   Mirrors `render_chip()` from Hypervisor's `site_utils/chips.py` — same
   variant vocabulary, same signature.

   Semantic vocabulary (WI-111 Phase 4):
   - 'filled'          → live/current (active status, ID chips)
   - 'outlined-accent' → structured/notable (project, "NEW" markers)
   - 'outlined-muted'  → historical/quiet (idle status, tags, badges like "IN USE")

   Usage:
     renderChip('filled', 'WI-42')
     renderChip('outlined-muted', 'IN USE', 'session-lock')
     renderChip('filled', 'ready', 'topbar-status', {state: 'ready'})
*/
const CHIP_VARIANTS = ['filled', 'outlined-accent', 'outlined-muted'];
function renderChip(variant, text, extraClass, dataAttrs) {
  if (CHIP_VARIANTS.indexOf(variant) === -1) {
    throw new Error("Unknown chip variant '" + variant + "'; expected one of " + CHIP_VARIANTS.join(', '));
  }
  var classes = 'hv-chip hv-chip-' + variant;
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
   Relocated to .hyperkit/js/toast.js (WI-142 Phase 1). window.HvToast and
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
   written separately because they arrive from separate events — a crash pushes
   __acpStateChange (which offers Reconnect) and __acpError (the message) back
   to back. The previous single-node bar had __acpError overwrite the whole node
   via textContent, destroying the Reconnect link microseconds after it was
   created and leaving closing the tab as the only way to recover. Keeping the
   slots separate makes that class of clobbering impossible. */
window.HaErrorBar = {
  // Set the message text. Uses textContent — never interpolate HTML here.
  setMessage: function (msg) {
    if (!errorBarMsg) return;
    errorBarMsg.textContent = msg == null ? '' : String(msg);
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
    document.documentElement.style.setProperty(
      '--ha-cluster-w', Math.ceil(cluster.getBoundingClientRect().width) + 'px'
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
var _loadingHistoryTimeout = null;
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
  ctxLabel.textContent = p + '%';
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
// These four (plus ctxPercentage above) are per-tab metrics — 08-tabs.js
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
function updateStatusCenter() {
  var el = document.getElementById('status-credits');
  if (el) el.textContent = sessionCredits > 0 ? sessionCredits.toFixed(2) + ' cr' : '';
  var tel = document.getElementById('status-tokens');
  if (tel) tel.textContent = (sessionTokensIn + sessionTokensOut) > 0 ? Math.round((sessionTokensIn + sessionTokensOut) / 1000) + 'k tok' : '';
  var turns = document.getElementById('status-turns');
  if (turns) turns.textContent = sessionTurns > 0 ? sessionTurns + ' turns' : '';
}

// Plan credits refresh
function refreshPlanCredits() {
  var btn = document.getElementById('plan-credits-refresh');
  var label = document.getElementById('plan-credits-label');
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_plan_usage) return;
  if (btn) btn.classList.add('spinning');
  pywebview.api.get_plan_usage().then(function(data) {
    if (btn) btn.classList.remove('spinning');
    if (!data || !data.ok) { if (label) label.textContent = '?'; return; }
    if (label) {
      label.textContent = data.used + ' / ' + data.total + ' cr';
      label.className = 'ha-cluster-chip plan-credits-label' + (data.used_pct >= 90 ? ' critical' : data.used_pct >= 70 ? ' warn' : '');
      label.title = data.detail || '';
    }
  }).catch(function() {
    if (btn) btn.classList.remove('spinning');
    if (label) label.textContent = '?';
  });
}
window.refreshPlanCredits = refreshPlanCredits;

// Apply palette from hypervisor theme
function applyAccent(palette) {
  var hex = typeof palette === 'string' ? palette : palette.accent;
  var r = parseInt(hex.slice(1,3),16), g = parseInt(hex.slice(3,5),16), b = parseInt(hex.slice(5,7),16);
  var root = document.documentElement.style;
  root.setProperty('--accent', hex);
  root.setProperty('--accent-dim', hex + 'cc');
  root.setProperty('--accent-glow', 'rgba('+r+','+g+','+b+',0.06)');
  root.setProperty('--accent-border', 'rgba('+r+','+g+','+b+',0.15)');
  if (typeof palette === 'object') {
    root.setProperty('--warm', palette.warm);
    root.setProperty('--cool', palette.cool);
    root.setProperty('--comp', palette.comp);
    // Apply semantic overrides from presets, or reset to defaults
    if (palette.semantics) {
      if (palette.semantics.success) root.setProperty('--success', palette.semantics.success);
      if (palette.semantics.warning) root.setProperty('--warning', palette.semantics.warning);
      if (palette.semantics.error) root.setProperty('--error', palette.semantics.error);
      if (palette.semantics.info) root.setProperty('--info', palette.semantics.info);
    } else {
      root.setProperty('--success', '#00ff41');
      root.setProperty('--warning', '#ffb000');
      root.setProperty('--error', '#ff3333');
      root.setProperty('--info', '#00cccc');
    }
    root.setProperty('--highlight', 'var(--accent)');
    root.setProperty('--surface-active', 'var(--accent-glow)');
  }
  // Dynamic cursors synced to accent
  var ec = encodeURIComponent(hex);
  root.setProperty('--cursor-default', "url(\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='" + ec + "' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z'/></svg>\") 2 2, auto");
  root.setProperty('--cursor-pointer', "url(\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='" + ec + "' stroke='" + ec + "' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z'/></svg>\") 2 2, pointer");
  root.setProperty('--cursor-text', "url(\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='" + ec + "' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M17 22h-1a4 4 0 0 1-4-4V6a4 4 0 0 1 4-4h1'/><path d='M7 22h1a4 4 0 0 0 4-4V6a4 4 0 0 0-4-4H7'/></svg>\") 10 10, text");
}
window.applyAccent = applyAccent;
if (window.pywebview && window.pywebview.api) {
  pywebview.api.get_accent().then(applyAccent);
} else {
  window.addEventListener('pywebviewready', function() {
    pywebview.api.get_accent().then(applyAccent);
  });
}
