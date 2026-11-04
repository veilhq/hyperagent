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
          if (window.HvToast) window.HvToast.show({ variant: 'error', message: 'rename failed - session unchanged' });
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
      sessionList.innerHTML = '';
      var authMsg = document.createElement('div');
      authMsg.style.cssText = 'padding:0.8rem 1rem;color:var(--comp);font-size: var(--fs-mono);';
      authMsg.textContent = data.auth_hint || 'Not logged in - run kiro-cli login';
      sessionList.appendChild(authMsg);
      return;
    }
    if (!data || !data.sessions.length) {
      sessionList.innerHTML = '<div style="padding:0.8rem 1rem;color:var(--text-dim);font-size: var(--fs-mono);">No sessions</div>';
      return;
    }
    // Get session IDs already open in tabs - skip those from the history list
    var openIds = window._getOpenSessionIds ? window._getOpenSessionIds() : {};
    var rendered = 0;
    data.sessions.forEach(function(s, i) {
      // Skip sessions that are currently open as tabs
      if (openIds[s.id]) return;

      var el = document.createElement('div');
      el.className = 'element-hover-lift session-item stagger-in';
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
        '<button class="session-delete-btn" data-tooltip="Delete" aria-label="Delete">&times;</button>';

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
      sessionList.innerHTML = '<div style="padding:0.8rem 1rem;color:var(--text-dim);font-size: var(--fs-mono);">All sessions open as tabs</div>';
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
  // Hold back stream updates and sends until the load settles. The loading
  // block is the tab's content until the history push replaces it or a load
  // failure replaces it with Retry; the backend sends one or the other for
  // every load, and both clear the hold.
  _loadingHistory = true;
  showTabLoading(msgs, 'Loading session');
  pywebview.api.load_session(id);
}

function openInNewTab(sessionId, title) {
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.open_session_in_tab) return;
  pywebview.api.open_session_in_tab(sessionId).then(function(tabId) {
    if (!tabId) return; // error pushed from backend
    _addTabToUI(tabId, title || 'New Chat', sessionId);
    switchTab(tabId);
    // Unless history already arrived, the new tab shows its loading block.
    var box = tabs[tabId] && tabs[tabId].msgsEl;
    if (box && tabHasNoContent(box)) showTabLoading(box, 'Loading session');
    // Refresh session list to remove the now-open session
    refreshSessions();
  });
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
  // Ctrl+Shift+B: debug - cycle the error bar through its states then hide.
  // Checked before the plain Ctrl+B toggle below; with Shift held e.key is 'B'.
  if (e.ctrlKey && e.shiftKey && (e.key === 'B' || e.key === 'b')) {
    e.preventDefault();
    if (!window.HaErrorBar) return;
    var steps = [
      function () { HaErrorBar.setMessage('Connection lost.', 'error'); HaErrorBar.setAction('Reconnect', function () { HaErrorBar.hide(); }); },
      function () { HaErrorBar.clearAction(); HaErrorBar.setMessage('Watcher reconnecting...', 'warning'); },
      function () { HaErrorBar.setMessage('Rebuilding...', 'info'); },
      function () { HaErrorBar.setMessage('Reconnected automatically', 'success'); },
      function () { HaErrorBar.hide(); }
    ];
    window.__errorBarDebugStep = (window.__errorBarDebugStep || 0) % steps.length;
    steps[window.__errorBarDebugStep]();
    window.__errorBarDebugStep++;
    return;
  }
  // Ctrl+B: toggle sidebar
  if (e.ctrlKey && e.key === 'b') { e.preventDefault(); toggleSidebar(); }
  // Ctrl+N: new tab (was in-place newSession; tabs are the primary UX now)
  if (e.ctrlKey && e.key === 'n') { e.preventDefault(); if (typeof createTab === 'function') createTab(); else newSession(); }
  // f: toggle fullscreen (when not typing, and no modifier - avoid clashing with Ctrl+F search).
  // Suppress whenever focus is in any text-entry context (prompt textarea, tab-rename input,
  // or any contenteditable) so a literal lowercase 'f' can be typed into titles and fields.
  if (e.key === 'f' && !e.ctrlKey && !e.metaKey && !e.altKey) {
    var ae = document.activeElement;
    var typing = ae && (ae.tagName === 'TEXTAREA' || ae.tagName === 'INPUT' || ae.isContentEditable);
    if (!typing) { e.preventDefault(); pywebview.api.toggle_fullscreen(); }
  }
});

// Expose for inline onclick in shell.html
window.toggleSidebar = toggleSidebar;
