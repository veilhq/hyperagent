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
    data.sessions.forEach(function(s, i) {
      var el = document.createElement('div');
      el.className = 'session-item stagger-in' + (s.id === data.active ? ' active' : '');
      el.setAttribute('data-session-id', s.id);
      var lockBadge = s.locked && s.id !== data.active ? '<span class="session-lock">IN USE</span>' : '';
      el.innerHTML = '<div class="session-item-row">'
        + '<div class="session-item-title">' + escapeHtml(s.title) + '</div>'
        + '<button class="session-delete-btn" title="Delete">&times;</button>'
        + '</div>'
        + '<div class="session-item-meta">' + escapeHtml(s.age) + ' · ' + escapeHtml(s.msgs) + lockBadge + '</div>';
      el.querySelector('.session-item-title').onclick = function() { loadSession(s.id); };
      el.querySelector('.session-delete-btn').onclick = function(e) { e.stopPropagation(); deleteSession(s.id, el); };
      // Right-click context menu
      el.addEventListener('contextmenu', function(e) {
        e.preventDefault();
        e.stopPropagation();
        ctxMenu.show(e.clientX, e.clientY, s.id, el);
      });
      sessionList.appendChild(el);
      setTimeout(function() { el.classList.remove('stagger-in'); el.classList.add('stagger-visible'); }, 30 + i * 40);
    });
  });
}

function loadSession(id) {
  // Block updates until history render completes
  _loadingHistory = true;
  // Safety: reset flag if __acpSessionLoaded never fires (e.g. push_js error)
  _loadingHistoryTimeout = setTimeout(function() { _loadingHistory = false; }, 10000);
  pywebview.api.load_session(id);
  // Update active highlight
  sessionList.querySelectorAll('.session-item').forEach(function(el) {
    el.classList.toggle('active', false);
  });
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
