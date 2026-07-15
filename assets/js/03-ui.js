/* ===== Hyperagent: UI Interactions ===== */

function send() {
  var text = input.value.trim();
  if (!text) return;
  // If currently prompting, cancel first then send (interrupt)
  if (state === 'prompting') {
    input.value = '';
    input.style.height = 'auto';
    pywebview.api.cancel('interrupt');
    // Queue the new prompt — poll until state becomes ready
    var attempts = 0;
    var pollReady = setInterval(function() {
      attempts++;
      if (state === 'ready') {
        clearInterval(pollReady);
        appendUser(text);
        if (!sessionTitle) firstPrompt = text;
        pywebview.api.send_prompt(text);
      } else if (attempts > 40) {
        // Safety: give up after ~2s
        clearInterval(pollReady);
      }
    }, 50);
    return;
  }
  if (state !== 'ready') return;
  if (_loadingHistory) return;
  if (!sessionTitle) firstPrompt = text;
  appendUser(text);
  input.value = '';
  input.style.height = 'auto';
  pywebview.api.send_prompt(text);
}

function cancel() {
  pywebview.api.cancel();
}

function newSession() {
  if (state !== 'ready') return;
  pywebview.api.new_session();
}

// Keyboard shortcuts
input.addEventListener('keydown', function(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    if (searchActive) { closeSearch(); return; }
    if (shortcutsVisible) { toggleShortcuts(); return; }
    if (state === 'prompting') cancel();
  }
  if (e.key === '/' && document.activeElement !== input) { e.preventDefault(); input.focus(); }
  if (e.key === '?' && document.activeElement !== input) { e.preventDefault(); toggleShortcuts(); }
  // Ctrl+F: in-session search
  if (e.ctrlKey && e.key === 'f') { e.preventDefault(); openSearch(); }
});

// Auto-resize textarea
input.addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 180) + 'px';
});

// Message copy (delegated)
msgs.addEventListener('click', function(e) {
  if (!e.target.classList.contains('msg-copy')) return;
  var msg = e.target.closest('.msg-agent');
  if (!msg) return;
  var text = msg._rawText || msg.textContent.replace(/^Copy/, '').trim();
  var btn = e.target;
  function onSuccess() {
    btn.textContent = 'Copied';
    btn.classList.add('copied');
    setTimeout(function() { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1200);
  }
  if (window.pywebview && pywebview.api && pywebview.api.copy_to_clipboard) {
    pywebview.api.copy_to_clipboard(text).then(function(ok) {
      if (ok) onSuccess();
    });
  } else if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(onSuccess).catch(function() { onSuccess(); });
  }
});

// ---- Keyboard shortcut overlay ----
var shortcutsVisible = false;
var shortcutsEl = null;

function toggleShortcuts() {
  if (!shortcutsEl) {
    shortcutsEl = document.createElement('div');
    shortcutsEl.className = 'shortcuts-overlay';
    shortcutsEl.innerHTML = '<div class="shortcuts-panel">'
      + '<div class="shortcuts-title">Keyboard Shortcuts</div>'
      + '<div class="shortcuts-grid">'
      + sc('/', 'Focus input')
      + sc('?', 'Toggle shortcuts')
      + sc('Ctrl+B', 'Toggle sidebar')
      + sc('Ctrl+N', 'New session')
      + sc('Ctrl+F', 'Search messages')
      + sc('Esc', 'Cancel / close')
      + sc('Enter', 'Send message')
      + sc('Shift+Enter', 'New line')
      + sc('F', 'Toggle fullscreen')
      + '</div></div>';
    shortcutsEl.addEventListener('click', function(e) {
      if (e.target === shortcutsEl) toggleShortcuts();
    });
    document.body.appendChild(shortcutsEl);
  }
  shortcutsVisible = !shortcutsVisible;
  shortcutsEl.classList.toggle('visible', shortcutsVisible);
}

function sc(key, desc) {
  return '<div class="sc-row"><kbd>' + key + '</kbd><span>' + desc + '</span></div>';
}

// ---- In-session search ----
var searchActive = false;
var searchEl = null;
var searchMatches = [];
var searchIdx = -1;

function openSearch() {
  if (!searchEl) {
    searchEl = document.createElement('div');
    searchEl.className = 'search-bar';
    searchEl.innerHTML = '<input class="search-input" placeholder="Search messages..." />'
      + '<span class="search-count" id="search-count"></span>'
      + '<button class="search-close">&times;</button>';
    var topRef = document.querySelector('.status-cluster');
    topRef.parentNode.insertBefore(searchEl, topRef);
    var si = searchEl.querySelector('.search-input');
    si.addEventListener('input', function() { doSearch(si.value); });
    si.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') { e.preventDefault(); jumpNext(); }
      if (e.key === 'Escape') closeSearch();
    });
    searchEl.querySelector('.search-close').addEventListener('click', closeSearch);
  }
  searchEl.classList.add('visible');
  searchActive = true;
  searchEl.querySelector('.search-input').focus();
}

function closeSearch() {
  if (searchEl) searchEl.classList.remove('visible');
  searchActive = false;
  clearHighlights();
}

function doSearch(term) {
  clearHighlights();
  searchMatches = [];
  searchIdx = -1;
  if (!term.trim()) { document.getElementById('search-count').textContent = ''; return; }
  var msgEls = msgs.querySelectorAll('.msg');
  var lower = term.toLowerCase();
  msgEls.forEach(function(el) {
    if (el.textContent.toLowerCase().indexOf(lower) > -1) {
      el.classList.add('search-hit');
      searchMatches.push(el);
    }
  });
  document.getElementById('search-count').textContent = searchMatches.length + ' found';
  if (searchMatches.length) jumpNext();
}

function jumpNext() {
  if (!searchMatches.length) return;
  searchIdx = (searchIdx + 1) % searchMatches.length;
  searchMatches[searchIdx].scrollIntoView({ block: 'center', behavior: 'smooth' });
}

function clearHighlights() {
  msgs.querySelectorAll('.search-hit').forEach(function(el) { el.classList.remove('search-hit'); });
}

// ---- Welcome state ----
var welcomeGreetings = [
  'How can I help?',
  'Hello, Operator.',
  'Ready when you are.',
  'What are we building?',
  'Standing by.',
  'All systems go.',
  'What\'s on your mind?',
  'Let\'s get to work.',
  'Good to see you, V.',
  'What\'s the job?'
];

var welcomePrompts = [
  'What\'s in flight right now?',
  'Let\'s review a PR.',
  'Run a health check.'
];

function showWelcome() {
  var w = document.createElement('div');
  w.className = 'welcome';
  w.innerHTML = '<svg class="welcome-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108.28 108.28" fill="currentColor"><path d="M107.94,71.76l-35.71-35.71-.04-.04h-34.8c-.63,0-1.14-.51-1.14-1.14V1.14c0-.63-.51-1.14-1.14-1.14H1.14C.51,0,0,.51,0,1.14v34.58c0,.3.12.59.33.8l33.56,33.56c.72.72.21,1.94-.8,1.94H1.14c-.63,0-1.14.51-1.14,1.14v33.98c0,.63.51,1.14,1.14,1.14h33.98c.63,0,1.14-.51,1.14-1.14v-33.73c0-.63.51-1.14,1.14-1.14h33.48c.63,0,1.14.51,1.14,1.14v33.73c0,.63.51,1.14,1.14,1.14h33.98c.63,0,1.14-.51,1.14-1.14v-34.58c0-.3-.12-.59-.33-.8Z"/><path d="M72.67,18.01l7.88,3.11c2.6,1.03,4.66,3.08,5.68,5.68l3.11,7.87c.18.45.82.45,1,0l3.11-7.87c1.03-2.6,3.08-4.66,5.68-5.68l7.88-3.11c.45-.18.45-.82,0-1l-7.88-3.11c-2.6-1.03-4.66-3.08-5.68-5.68l-3.11-7.87c-.18-.45-.82-.45-1,0l-3.11,7.87c-1.03,2.6-3.08,4.66-5.68,5.68l-7.88,3.11c-.45.18-.45.82,0,1Z"/></svg>'
    + '<span class="welcome-text">' + welcomeGreetings[Math.floor(Math.random() * welcomeGreetings.length)] + '</span>'
    + '<div class="welcome-prompts"></div>';
  var chips = w.querySelector('.welcome-prompts');
  welcomePrompts.forEach(function(p) {
    var chip = document.createElement('button');
    chip.className = 'welcome-chip';
    chip.textContent = p;
    chip.onclick = function() { input.value = p; send(); };
    chips.appendChild(chip);
  });
  msgs.appendChild(w);
  // Defer noise start — 06-welcome.js may not have loaded yet at initial parse
  setTimeout(function() { startWelcomeNoise(); }, 0);
}

// Show welcome on load if empty
showWelcome();

// Show steering files included in session
function showSteering() {
  if (!window.pywebview || !window.pywebview.api) {
    window.addEventListener('pywebviewready', showSteering);
    return;
  }
  pywebview.api.get_steering().then(function(files) {
    if (!files || !files.length) return;
    var auto = files.filter(function(f) { return f.inclusion === 'auto'; });
    if (!auto.length) return;
    var el = document.createElement('div');
    el.className = 'steering-card';
    el.innerHTML = '<span class="steering-label">steering</span>'
      + '<span class="steering-files">' + auto.map(function(f) { return f.name; }).join(' · ') + '</span>';
    msgs.appendChild(el);
  });
}
showSteering();

// Wire buttons
sendBtn.addEventListener('click', send);
cancelBtn.addEventListener('click', cancel);

// Expose globals needed by inline onclick handlers and pywebview bridge
window.send = send;
window.cancel = cancel;
window.newSession = newSession;

// --- Cursor companion box ---
(function() {
  var box = document.createElement('div');
  box.className = 'cursor-box';
  document.body.appendChild(box);
  var CLICKABLE = 'a, button, [role="button"], .session-item, .session-item-title, .session-delete-btn, .search-close, .input-icon-btn, #send-btn, #cancel-btn, .msg-copy, .welcome-chip, .tool-card';
  var hovering = false;
  document.addEventListener('mousemove', function(e) {
    box.style.left = (e.clientX + 14) + 'px';
    box.style.top = (e.clientY - 4) + 'px';
    var over = document.elementFromPoint(e.clientX, e.clientY);
    var hit = over && over.closest(CLICKABLE);
    if (hit && !hovering) { hovering = true; box.classList.add('visible'); }
    else if (!hit && hovering) { hovering = false; box.classList.remove('visible', 'blink'); }
  });
  document.addEventListener('mousedown', function(e) {
    var over = document.elementFromPoint(e.clientX, e.clientY);
    if (!over || !over.closest(CLICKABLE)) return;
    box.classList.remove('blink');
    void box.offsetWidth;
    box.classList.add('blink');
    setTimeout(function() { box.classList.remove('blink'); }, 350);
  });
})();
