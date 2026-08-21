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
  // Insert into the grid's body column (after the title element)
  el.insertBefore(input, titleEl.nextSibling);
  input.focus();
  input.select();

  function commit() {
    var newTitle = input.value.trim();
    if (!newTitle) newTitle = currentTitle;
    titleEl.textContent = newTitle;
    titleEl.classList.remove('renaming');
    if (input.parentNode) input.parentNode.removeChild(input);
    if (newTitle !== currentTitle) {
      var p = pywebview.api.rename_session(sessionId, newTitle);
      if (p && typeof p.catch === 'function') {
        p.catch(function () {
          if (window.HvToast) window.HvToast.show({ variant: 'error', message: 'rename failed — session unchanged' });
        });
      }
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
  var btn = document.getElementById('session-refresh-btn');
  if (btn) {
    btn.classList.remove('spinning');
    // Restart the animation even if triggered twice quickly
    void btn.offsetWidth;
    btn.classList.add('spinning');
  }
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
      el.className = 'hv-hover-lift session-item stagger-in';
      el.setAttribute('data-session-id', s.id);

      // Build stacked row: title on top, meta strip (chip + age) below, delete btn top-right
      var chipHtml = renderChip('outlined-muted', HvUtils.escapeHtml(s.msgs), 'session-item-chip');
      var lockHtml = s.locked ? ' ' + renderChip('outlined-muted', 'IN USE', 'session-lock') : '';
      el.innerHTML =
        '<span class="session-item-title">' + HvUtils.escapeHtml(s.title) + '</span>' +
        '<div class="session-item-meta">' +
          chipHtml + lockHtml +
          '<span class="session-item-age">' + HvUtils.escapeHtml(s.age) + '</span>' +
        '</div>' +
        '<button class="session-delete-btn" title="Delete">&times;</button>';

      // Single click on title: open in a new tab
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

// Backend reaped a scratch session created to satisfy the session-load protocol.
// The list is only rebuilt on request, so re-query rather than leave a stale entry.
window.__acpSessionsChanged = function(data) {
  if (typeof refreshSessions !== 'function') return;
  if (!sidebar || !sidebar.classList.contains('open')) return;
  refreshSessions();
};

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
  splash.innerHTML = '<div class="ha-splash-flag"><svg class="ha-splash-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 35.02 35.02" fill="currentColor"><path d="M13.58,20.85c.19,0,.28-.23.15-.36,0,0-5.08-5.08-6.1-6.11-.11-.11-.27-.14-.42-.09L.34,17.01c-.45.18-.45.82,0,1l7.05,2.79c.11.04.22.06.33.06.98,0,5.85,0,5.85,0Z"/><path d="M20.85,27.42v-6.3c0-.12-.1-.21-.21-.21h-6.26c-.12,0-.21.1-.21.21v6.3s0,.03-.01.05l2.85,7.21c.18.45.82.45,1,0l2.85-7.21s-.01-.03-.01-.05Z"/><path d="M34.68,17.01l-7.87-3.11c-2.6-1.03-4.66-3.08-5.68-5.68L18.01.34c-.18-.45-.82-.45-1,0l-2.85,7.21s.01.03.01.05v6.3c0,.12.1.21.21.21h6.09c.27,0,.52.11.71.3,1.14,1.14,5.26,5.26,6.2,6.2.12.12.29.15.44.09l6.85-2.71c.45-.18.45-.82,0-1Z"/></svg></div>'
    + '<div class="ha-splash-loading">Loading session history</div>'
    + '<div class="ha-splash-pct" id="ha-splash-pct">0%</div>';
  document.body.appendChild(splash);
}

function deleteSession(id, el) {
  var title = el ? (el.querySelector('.session-item-title') || {}).textContent : null;
  pywebview.api.delete_session(id).then(function() {
    if (el) {
      el.classList.add('session-removing');
      setTimeout(function() { if (el.parentNode) el.parentNode.removeChild(el); }, 200);
    }
    if (window.HvToast) {
      window.HvToast.show({
        variant: 'success',
        message: 'session deleted' + (title ? ': ' + title : '')
      });
    }
  }).catch(function() {
    if (window.HvToast) window.HvToast.show({ variant: 'error', message: 'session delete failed' });
  });
}

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
  // Ctrl+B: toggle sidebar
  if (e.ctrlKey && e.key === 'b') { e.preventDefault(); toggleSidebar(); }
  // Ctrl+N: new tab (was in-place newSession; tabs are the primary UX now)
  if (e.ctrlKey && e.key === 'n') { e.preventDefault(); if (typeof createTab === 'function') createTab(); else newSession(); }
  // f: toggle fullscreen (when not in input, and no modifier — avoid clashing with Ctrl+F search)
  if (e.key === 'f' && !e.ctrlKey && !e.metaKey && !e.altKey && document.activeElement && document.activeElement.tagName !== 'TEXTAREA') { e.preventDefault(); pywebview.api.toggle_fullscreen(); }
});

// Expose for inline onclick in shell.html
window.toggleSidebar = toggleSidebar;
