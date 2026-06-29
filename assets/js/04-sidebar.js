/* ===== Hyperagent: Sessions Sidebar ===== */

var sidebar = document.getElementById('sidebar');
var sessionList = document.getElementById('session-list');

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
      var lockBadge = s.locked && s.id !== data.active ? '<span class="session-lock">IN USE</span>' : '';
      el.innerHTML = '<div class="session-item-row">'
        + '<div class="session-item-title">' + escapeHtml(s.title) + '</div>'
        + '<button class="session-delete-btn" title="Delete">&times;</button>'
        + '</div>'
        + '<div class="session-item-meta">' + escapeHtml(s.age) + ' · ' + escapeHtml(s.msgs) + lockBadge + '</div>';
      el.querySelector('.session-item-title').onclick = function() { loadSession(s.id); };
      el.querySelector('.session-delete-btn').onclick = function(e) { e.stopPropagation(); deleteSession(s.id, el); };
      sessionList.appendChild(el);
      setTimeout(function() { el.classList.remove('stagger-in'); el.classList.add('stagger-visible'); }, 30 + i * 40);
    });
  });
}

function loadSession(id) {
  // Block updates until history render completes
  window._loadingHistory = true;
  // Safety: reset flag if __acpSessionLoaded never fires (e.g. push_js error)
  window._loadingHistoryTimeout = setTimeout(function() { window._loadingHistory = false; }, 10000);
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
  // f: toggle fullscreen (when not in input)
  if (e.key === 'f' && document.activeElement.tagName !== 'TEXTAREA') { e.preventDefault(); pywebview.api.toggle_fullscreen(); }
});
