/* ===== Hyperagent: Sessions Sidebar ===== */

var sidebar = document.getElementById('sidebar');
var sessionList = document.getElementById('session-list');
var ctxMenu = null; // context menu element
var ctxSessionId = null; // session targeted by context menu

function toggleSidebar() {
  var open = sidebar.classList.toggle('open');
  if (open) refreshSessions();
}

var _refreshTimer = null;
var _lastRefresh = 0;
var REFRESH_DEBOUNCE = 500;

function refreshSessions() {
  var now = Date.now();
  if (now - _lastRefresh < REFRESH_DEBOUNCE) {
    clearTimeout(_refreshTimer);
    _refreshTimer = setTimeout(refreshSessions, REFRESH_DEBOUNCE);
    return;
  }
  _lastRefresh = now;
  _doRefreshSessions();
}

function _doRefreshSessions() {
  pywebview.api.list_sessions().then(function(data) {
    sessionList.innerHTML = '';
    if (data && data.auth_required) {
      sessionList.innerHTML = '<div style="padding:0.8rem 1rem;color:var(--comp);font-size:0.65rem;">Not logged in — run kiro-cli login</div>';
      return;
    }
    if (!data || !data.sessions.length) {
      sessionList.innerHTML = '<div style="padding:0.8rem 1rem;color:var(--text-dim);font-size:0.65rem;">No sessions</div>';
      return;
    }
    data.sessions.forEach(function(s, i) {
      var el = document.createElement('div');
      var isPinned = s.pinned || false;
      var cls = 'session-item stagger-in';
      if (s.id === data.active) cls += ' active';
      if (isPinned) cls += ' pinned';
      el.className = cls;
      el.dataset.sessionId = s.id;

      var lockBadge = s.locked && s.id !== data.active ? '<span class="session-lock">IN USE</span>' : '';
      var indicator = '';
      if (s.processing) indicator = '<span class="session-dot pulsing"></span>';
      else if (s.completed) indicator = '<span class="session-dot done">!</span>';

      el.innerHTML = '<div class="session-item-row">'
        + '<div class="session-item-title">' + escapeHtml(s.title) + '</div>'
        + indicator
        + '<button class="session-more-btn" title="Actions">&#x2026;</button>'
        + '</div>'
        + '<div class="session-item-meta">' + escapeHtml(s.age) + ' · ' + escapeHtml(s.msgs) + lockBadge + '</div>';

      el.querySelector('.session-item-title').onclick = function() { loadSession(s.id); };
      el.querySelector('.session-more-btn').onclick = function(e) { e.stopPropagation(); showCtxMenu(e, s); };
      el.addEventListener('contextmenu', function(e) { e.preventDefault(); showCtxMenu(e, s); });

      sessionList.appendChild(el);
      setTimeout(function() { el.classList.remove('stagger-in'); el.classList.add('stagger-visible'); }, 30 + i * 40);
    });
  });
}

// --- Context Menu ---

function showCtxMenu(e, session) {
  hideCtxMenu();
  ctxSessionId = session.id;
  var isPinned = session.pinned || false;
  var isActive = document.querySelector('.session-item.active');
  isActive = isActive && isActive.dataset.sessionId === session.id;

  ctxMenu = document.createElement('div');
  ctxMenu.className = 'session-ctx-menu';

  var items = [
    { label: isPinned ? 'Unpin' : 'Pin', action: function() { togglePin(session.id, isPinned); } },
    { label: 'Rename', action: function() { renameSession(session.id); } },
    { label: 'Delete', action: function() { deleteSession(session.id); }, disabled: isPinned || isActive }
  ];

  items.forEach(function(item) {
    var btn = document.createElement('button');
    btn.className = 'session-ctx-item' + (item.disabled ? ' disabled' : '');
    btn.textContent = item.label;
    if (!item.disabled) btn.onclick = function() { hideCtxMenu(); item.action(); };
    ctxMenu.appendChild(btn);
  });

  document.body.appendChild(ctxMenu);

  // Position near the click
  var x = e.clientX, y = e.clientY;
  ctxMenu.style.left = x + 'px';
  ctxMenu.style.top = y + 'px';

  // Adjust if overflows viewport
  requestAnimationFrame(function() {
    var rect = ctxMenu.getBoundingClientRect();
    if (rect.right > window.innerWidth) ctxMenu.style.left = (x - rect.width) + 'px';
    if (rect.bottom > window.innerHeight) ctxMenu.style.top = (y - rect.height) + 'px';
  });

  // Close on outside click
  setTimeout(function() {
    document.addEventListener('click', hideCtxMenuOnClick);
  }, 0);
}

function hideCtxMenu() {
  if (ctxMenu && ctxMenu.parentNode) ctxMenu.parentNode.removeChild(ctxMenu);
  ctxMenu = null;
  ctxSessionId = null;
  document.removeEventListener('click', hideCtxMenuOnClick);
}

function hideCtxMenuOnClick(e) {
  if (ctxMenu && !ctxMenu.contains(e.target)) hideCtxMenu();
}

// --- Pin/Unpin ---

function togglePin(sessionId, currentlyPinned) {
  var method = currentlyPinned ? 'unpin_session' : 'pin_session';
  pywebview.api[method](sessionId).then(function() { refreshSessions(); });
}

// --- Rename ---

function renameSession(sessionId) {
  var el = sessionList.querySelector('[data-session-id="' + sessionId + '"] .session-item-title');
  if (!el) return;
  var current = el.textContent;
  var inp = document.createElement('input');
  inp.className = 'session-rename-input';
  inp.value = current;
  inp.maxLength = 50;
  el.textContent = '';
  el.appendChild(inp);
  inp.focus();
  inp.select();

  function commit() {
    var val = inp.value.trim();
    if (val && val !== current) {
      pywebview.api.rename_session(sessionId, val).then(function() { refreshSessions(); });
    } else {
      el.textContent = current;
    }
  }
  inp.addEventListener('blur', commit);
  inp.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); inp.blur(); }
    if (e.key === 'Escape') { inp.value = current; inp.blur(); }
  });
}

// --- Load / Delete ---

function loadSession(id) {
  window._loadingHistory = true;
  window._loadingHistoryTimeout = setTimeout(function() { window._loadingHistory = false; }, 10000);
  if (window._setActiveSession) window._setActiveSession(id);
  if (window._updateSessionIndicator) window._updateSessionIndicator(id, 'clear');
  pywebview.api.load_session(id);
  sessionList.querySelectorAll('.session-item').forEach(function(el) {
    el.classList.toggle('active', el.dataset.sessionId === id);
  });
}

function deleteSession(id) {
  pywebview.api.delete_session(id).then(function(ok) {
    if (ok) refreshSessions();
  });
}

// --- Sidebar indicator updates (called from handlers) ---

window._updateSessionIndicator = function(sessionId, type) {
  // type: 'processing' | 'done' | 'clear'
  var el = sessionList.querySelector('[data-session-id="' + sessionId + '"]');
  if (!el) return;
  var existing = el.querySelector('.session-dot');
  if (existing) existing.remove();
  if (type === 'clear') return;
  var dot = document.createElement('span');
  if (type === 'processing') {
    dot.className = 'session-dot pulsing';
  } else if (type === 'done') {
    dot.className = 'session-dot done';
    dot.textContent = '!';
  }
  el.querySelector('.session-item-row').insertBefore(dot, el.querySelector('.session-more-btn'));
};

function escapeHtml(str) {
  return (str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
  if (e.ctrlKey && e.key === 'b') { e.preventDefault(); toggleSidebar(); }
  if (e.key === 'f' && document.activeElement.tagName !== 'TEXTAREA') { e.preventDefault(); pywebview.api.toggle_fullscreen(); }
});
