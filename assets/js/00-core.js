/* ===== Hyperagent: Core ===== */
(function() {
"use strict";

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
window.renderChip = renderChip;  // Expose for cross-module use inside IIFE

/* ---- Toast notifications (WI-115 variant-aware primitive) ----------------
   Shared cross-app IIFE — behavior-identical to Hypervisor's
   `core/00-core.js` (indentation differs because Hyperagent's file is not
   wrapped in an outer IIFE). See work/to-do/hyper-ecosystem-toast-rework.md.

     HvToast.show("plain message")                    → info variant, 3s
     HvToast.show({ variant, title, message, icon,
                    duration, action, dedupeKey })    → full options

     variant:   'success' | 'info' | 'warn' | 'error'   (default: 'info')
     duration:  ms number | 'sticky'                    (variant defaults apply)
     action:    { label: string, onClick: () => void }  (adds inline button)
     dedupeKey: string — replaces prior toast with same key
*/
(function initToasts() {
  if (window.HvToast) return; // idempotent

  var container = document.createElement('div');
  container.className = 'hv-toast-container';
  container.setAttribute('aria-live', 'polite');
  container.setAttribute('aria-atomic', 'false');
  function attach() { document.body.appendChild(container); }
  if (document.body) attach();
  else document.addEventListener('DOMContentLoaded', attach);

  var VARIANTS = {
    success: { icon: 'check-circle',   duration: 3000,     assertive: false },
    info:    { icon: 'info',           duration: 3000,     assertive: false },
    warn:    { icon: 'alert-triangle', duration: 5000,     assertive: true  },
    error:   { icon: 'circle-x',       duration: 'sticky', assertive: true  }
  };
  var MAX_VISIBLE = 5;
  var EXIT_MS = 300;
  var visible = [];
  var dedupeMap = {};

  function normalize(input) {
    if (typeof input === 'string') return { variant: 'info', message: input };
    if (!input || typeof input !== 'object') return { variant: 'info', message: String(input) };
    return input;
  }

  function dismiss(toast) {
    if (!toast || toast.__dismissed) return;
    toast.__dismissed = true;
    if (toast.__timer) clearTimeout(toast.__timer);
    toast.classList.remove('hv-toast-visible');
    toast.classList.add('hv-toast-exit');
    var idx = visible.indexOf(toast);
    if (idx >= 0) visible.splice(idx, 1);
    if (toast.__dedupeKey && dedupeMap[toast.__dedupeKey] === toast) {
      delete dedupeMap[toast.__dedupeKey];
    }
    setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, EXIT_MS);
  }

  function show(input) {
    var opts = normalize(input);
    var variant = VARIANTS[opts.variant] ? opts.variant : 'info';
    var defaults = VARIANTS[variant];
    var icon = opts.icon || defaults.icon;
    var duration = opts.duration != null ? opts.duration : defaults.duration;
    var sticky = duration === 'sticky';
    var message = opts.message == null ? '' : String(opts.message);
    var title = opts.title ? String(opts.title) : '';
    var action = opts.action && opts.action.label && typeof opts.action.onClick === 'function' ? opts.action : null;
    var dedupeKey = opts.dedupeKey || null;

    if (dedupeKey && dedupeMap[dedupeKey]) dismiss(dedupeMap[dedupeKey]);

    var toast = document.createElement('div');
    toast.className = 'hv-toast hv-toast-' + variant;
    if (defaults.assertive) toast.setAttribute('role', 'alert');
    toast.__dedupeKey = dedupeKey;

    var iconEl = document.createElement('i');
    iconEl.className = 'hv-toast-icon';
    iconEl.setAttribute('data-lucide', icon);
    toast.appendChild(iconEl);

    var body = document.createElement('div');
    body.className = 'hv-toast-body';
    if (title) {
      var titleEl = document.createElement('div');
      titleEl.className = 'hv-toast-title';
      titleEl.textContent = title;
      body.appendChild(titleEl);
    }
    var msgEl = document.createElement('div');
    msgEl.className = 'hv-toast-message';
    msgEl.textContent = message;
    body.appendChild(msgEl);
    if (action) {
      var actionBtn = document.createElement('button');
      actionBtn.className = 'hv-button hv-button-ghost hv-toast-action';
      actionBtn.type = 'button';
      actionBtn.textContent = action.label;
      actionBtn.addEventListener('click', function () {
        try { action.onClick(); } catch (e) {}
        dismiss(toast);
      });
      body.appendChild(actionBtn);
    }
    toast.appendChild(body);

    if (sticky || action) {
      var closeBtn = document.createElement('button');
      closeBtn.className = 'hv-toast-close';
      closeBtn.type = 'button';
      closeBtn.setAttribute('aria-label', 'Dismiss notification');
      closeBtn.textContent = '\u00d7';
      closeBtn.addEventListener('click', function () { dismiss(toast); });
      toast.appendChild(closeBtn);
    }

    container.appendChild(toast);
    visible.push(toast);
    if (dedupeKey) dedupeMap[dedupeKey] = toast;

    while (visible.length > MAX_VISIBLE) dismiss(visible[0]);

    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      try { window.lucide.createIcons(); } catch (e) {}
    }

    requestAnimationFrame(function () { toast.classList.add('hv-toast-visible'); });

    if (!sticky) {
      toast.__timer = setTimeout(function () { dismiss(toast); }, duration);
    }

    return { dismiss: function () { dismiss(toast); } };
  }

  try {
    var pending = sessionStorage.getItem('__hv_notify');
    if (pending) {
      sessionStorage.removeItem('__hv_notify');
      show(pending);
    }
  } catch (e) {}

  window.HvToast = { show: show, dismiss: dismiss };
  window.__hypervisorToast = show;  // legacy alias — accepts string or options
})();

var msgs = $('#messages');
const input = $('#input');
const sendBtn = $('#send-btn');
const cancelBtn = $('#cancel-btn');
const statusEl = $('.topbar-status');
const errorBar = $('.error-bar');
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
function updateCtxMeter(pct) {
  if (pct == null) return;
  var p = Math.round(pct);
  ctxLabel.textContent = p + '%';
  ctxFill.style.width = p + '%';
  ctxFill.className = 'ctx-meter-fill' + (p >= 85 ? ' critical' : p >= 65 ? ' warn' : '');
}

// Session credits accumulator
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
