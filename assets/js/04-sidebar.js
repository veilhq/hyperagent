/* ===== Hyperagent: Sessions Sidebar ===== */

var sidebar = document.getElementById('sidebar');
var sessionList = document.getElementById('session-list');

// --- Context menu ---
var ctxMenu = (function() {
  var menu = document.createElement('div');
  menu.className = 'session-ctx-menu';
  menu.innerHTML =
    '<div class="session-ctx-menu-item" data-action="rename">Rename</div>' +
    '<div class="session-ctx-menu-sep"></div>' +
    '<div class="session-ctx-menu-item danger" data-action="delete">Delete</div>';
  document.body.appendChild(menu);

  var _target = null; // { id, el }

  menu.addEventListener('click', function(e) {
    var item = e.target.closest('.session-ctx-menu-item');
    if (!item) return;
    var action = item.getAttribute('data-action');
    if (action === 'rename' && _target) startRename(_target.id, _target.el);
    if (action === 'delete' && _target) deleteSession(_target.id, _target.el);
    hide();
  });

  function show(x, y, sessionId, el) {
    _target = { id: sessionId, el: el };
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    menu.classList.add('visible');
    // Clamp to viewport
    var rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 4) + 'px';
    if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 4) + 'px';
  }

  function hide() {
    menu.classList.remove('visible');
    _target = null;
  }

  // Dismiss on any click outside
  document.addEventListener('click', function() { hide(); });
  document.addEventListener('contextmenu', function(e) {
    if (!e.target.closest('.session-item')) hide();
  });

  return { show: show, hide: hide };
})();

// --- Rename logic ---
function startRename(sessionId, el) {
  var titleEl = el.querySelector('.session-item-title');
  if (!titleEl) return;
  var currentTitle = titleEl.textContent;
  titleEl.classList.add('renaming');

  var input = document.createElement('input');
  input.type = 'text';
  input.className = 'session-rename-input';
  input.value = currentTitle;
  var row = el.querySelector('.session-item-row');
  row.insertBefore(input, titleEl.nextSibling);
  input.focus();
  input.select();

  function commit() {
    var newTitle = input.value.trim();
    if (!newTitle) newTitle = currentTitle;
    titleEl.textContent = newTitle;
    titleEl.classList.remove('renaming');
    if (input.parentNode) input.parentNode.removeChild(input);
    if (newTitle !== currentTitle) {
      pywebview.api.rename_session(sessionId, newTitle);
    }
  }

  function cancel() {
    titleEl.classList.remove('renaming');
    if (input.parentNode) input.parentNode.removeChild(input);
  }

  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') { e.preventDefault(); cancel(); }
  });
  input.addEventListener('blur', function() { commit(); });
}

function toggleSidebar() {
  var open = sidebar.classList.toggle('open');
  if (open) refreshSessions();
}

function refreshSessions() {
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
    // Get session IDs already open in tabs — skip those from the history list
    var openIds = window._getOpenSessionIds ? window._getOpenSessionIds() : {};
    var rendered = 0;
    data.sessions.forEach(function(s, i) {
      // Skip sessions that are currently open as tabs
      if (openIds[s.id]) return;
      var el = document.createElement('div');
      el.className = 'session-item stagger-in';
      el.setAttribute('data-session-id', s.id);
      var lockBadge = s.locked ? '<span class="session-lock">IN USE</span>' : '';
      el.innerHTML = '<div class="session-item-row">'
        + '<div class="session-item-title">' + escapeHtml(s.title) + '</div>'
        + '<button class="session-delete-btn" title="Delete">&times;</button>'
        + '</div>'
        + '<div class="session-item-meta">' + escapeHtml(s.age) + ' · ' + escapeHtml(s.msgs) + lockBadge + '</div>';
      // Single click: open in a new tab
      el.querySelector('.session-item-title').onclick = function() {
        if (s.locked) return;
        openInNewTab(s.id, s.title);
      };
      el.querySelector('.session-delete-btn').onclick = function(e) { e.stopPropagation(); deleteSession(s.id, el); };
      // Right-click context menu
      el.addEventListener('contextmenu', function(e) {
        e.preventDefault();
        e.stopPropagation();
        ctxMenu.show(e.clientX, e.clientY, s.id, el);
      });
      sessionList.appendChild(el);
      setTimeout(function() { el.classList.remove('stagger-in'); el.classList.add('stagger-visible'); }, 30 + rendered * 40);
      rendered++;
    });
    if (rendered === 0) {
      sessionList.innerHTML = '<div style="padding:0.8rem 1rem;color:var(--text-dim);font-size:0.65rem;">All sessions open as tabs</div>';
    }
  });
}

function loadSession(id) {
  // Block updates until history render completes
  _loadingHistory = true;
  // Capture the tab that initiated the load so the timeout resets the right state
  var loadTabId = activeTabId;
  _loadingHistoryTimeout = setTimeout(function() {
    if (activeTabId === loadTabId) {
      _loadingHistory = false;
    } else if (loadTabId && tabs[loadTabId] && tabs[loadTabId].renderState) {
      tabs[loadTabId].renderState._loadingHistory = false;
    }
  }, 10000);
  pywebview.api.load_session(id);
}

function openInNewTab(sessionId, title) {
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.open_session_in_tab) return;
  pywebview.api.open_session_in_tab(sessionId).then(function(tabId) {
    if (!tabId) return; // error pushed from backend
    _addTabToUI(tabId, title || 'New Chat', sessionId);
    switchTab(tabId);
    // Show loading splash immediately (don't wait for backend state push)
    _showLoadingSplash();
    // Refresh session list to remove the now-open session
    refreshSessions();
  });
}

function _showLoadingSplash() {
  if (document.getElementById('ha-splash')) return;
  var splash = document.createElement('div');
  splash.id = 'ha-splash';
  splash.className = 'ha-splash';
  splash.innerHTML = '<div class="ha-splash-flag"><svg class="ha-splash-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108.28 108.28" fill="currentColor"><path d="M107.94,71.76l-35.71-35.71-.04-.04h-34.8c-.63,0-1.14-.51-1.14-1.14V1.14c0-.63-.51-1.14-1.14-1.14H1.14C.51,0,0,.51,0,1.14v34.58c0,.3.12.59.33.8l33.56,33.56c.72.72.21,1.94-.8,1.94H1.14c-.63,0-1.14.51-1.14,1.14v33.98c0,.63.51,1.14,1.14,1.14h33.98c.63,0,1.14-.51,1.14-1.14v-33.73c0-.63.51-1.14,1.14-1.14h33.48c.63,0,1.14.51,1.14,1.14v33.73c0,.63.51,1.14,1.14,1.14h33.98c.63,0,1.14-.51,1.14-1.14v-34.58c0-.3-.12-.59-.33-.8Z"/><path d="M72.67,18.01l7.88,3.11c2.6,1.03,4.66,3.08,5.68,5.68l3.11,7.87c.18.45.82.45,1,0l3.11-7.87c1.03-2.6,3.08-4.66,5.68-5.68l7.88-3.11c.45-.18.45-.82,0-1l-7.88-3.11c-2.6-1.03-4.66-3.08-5.68-5.68l-3.11-7.87c-.18-.45-.82-.45-1,0l-3.11,7.87c-1.03,2.6-3.08,4.66-5.68,5.68l-7.88,3.11c-.45.18-.45.82,0,1Z"/></svg></div>'
    + '<div class="ha-splash-loading">Loading session history</div>'
    + '<div class="ha-splash-pct" id="ha-splash-pct">0%</div>';
  document.body.appendChild(splash);
}

function deleteSession(id, el) {
  pywebview.api.delete_session(id).then(function() {
    if (el && el.parentNode) el.parentNode.removeChild(el);
  });
}

function escapeHtml(str) {
  return (str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
  // Ctrl+B: toggle sidebar
  if (e.ctrlKey && e.key === 'b') { e.preventDefault(); toggleSidebar(); }
  // Ctrl+N: new session
  if (e.ctrlKey && e.key === 'n') { e.preventDefault(); newSession(); }
  // f: toggle fullscreen (when not in input)
  if (e.key === 'f' && document.activeElement && document.activeElement.tagName !== 'TEXTAREA') { e.preventDefault(); pywebview.api.toggle_fullscreen(); }
});

// Expose for inline onclick in shell.html
window.toggleSidebar = toggleSidebar;
