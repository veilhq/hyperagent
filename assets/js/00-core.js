/* ===== Hyperagent: Core ===== */
(function() {
"use strict";

// Global error catcher — shows JS errors in the error bar
window.onerror = function(msg, url, line, col, err) {
  var bar = document.querySelector('.error-bar');
  if (bar) { bar.textContent = 'JS: ' + msg + ' (line ' + line + ')'; bar.classList.add('visible'); }
  console.error('JS ERROR:', msg, 'line:', line, err);
};

const $ = (s) => document.querySelector(s);
const msgsWrapper = $('#messages');
const input = $('#input');
const sendBtn = $('#send-btn');
const cancelBtn = $('#cancel-btn');
const statusEl = $('.topbar-status');
const errorBar = $('.error-bar');
const app = $('#app');
const ctxLabel = $('#ctx-label');
const ctxFill = $('#ctx-fill');

let state = 'starting';
let currentMsgEl = null;
let currentMsgText = '';
let toolCards = {};
let currentToolRow = null;
let sessionTitle = '';
let firstPrompt = '';
window._loadingHistory = false;

// --- Per-session DOM containers ---
const MSG_CAP = 100;
let msgs = null; // points to active .session-msgs div

function ensureSessionContainer(sessionId) {
  if (!sessionId) sessionId = '__default';
  var el = msgsWrapper.querySelector('.session-msgs[data-session-id="' + sessionId + '"]');
  if (!el) {
    el = document.createElement('div');
    el.className = 'session-msgs';
    el.dataset.sessionId = sessionId;
    msgsWrapper.appendChild(el);
  }
  return el;
}

function switchContainer(sessionId) {
  msgsWrapper.querySelectorAll('.session-msgs.active').forEach(function(c) {
    c.classList.remove('active');
  });
  var target = ensureSessionContainer(sessionId);
  target.classList.add('active');
  msgs = target;
  return target;
}

function trimMessages() {
  if (!msgs) return;
  while (msgs.children.length > MSG_CAP) {
    msgs.removeChild(msgs.firstChild);
  }
}

// Init default container
msgs = ensureSessionContainer('__default');
msgs.classList.add('active');

// Context meter update
function updateCtxMeter(pct) {
  if (pct == null) return;
  var p = Math.round(pct);
  ctxLabel.textContent = p + '%';
  ctxFill.style.width = p + '%';
  ctxFill.className = 'ctx-meter-fill' + (p >= 85 ? ' critical' : p >= 65 ? ' warn' : '');
}
window.updateCtxMeter = updateCtxMeter;

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
  }
  // Dynamic cursors synced to accent
  var ec = encodeURIComponent(hex);
  root.setProperty('--cursor-default', "url(\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='" + ec + "' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z'/></svg>\") 2 2, auto");
  root.setProperty('--cursor-pointer', "url(\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='" + ec + "' stroke='" + ec + "' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z'/></svg>\") 2 2, pointer");
  root.setProperty('--cursor-text', "url(\"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='" + ec + "' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M17 22h-1a4 4 0 0 1-4-4V6a4 4 0 0 1 4-4h1'/><path d='M7 22h1a4 4 0 0 0 4-4V6a4 4 0 0 0-4-4H7'/></svg>\") 10 10, text");
}
window.applyAccent = applyAccent;
if (window.pywebview && window.pywebview.api) {
  pywebview.api.get_accent().then(applyAccent);
} else {
  window.addEventListener('pywebviewready', function() {
    pywebview.api.get_accent().then(applyAccent);
  });
}
