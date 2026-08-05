/* ===== Hyperagent: UI Interactions ===== */

function send() {
  var text = input.value.trim();
  if (!text) return;

  // /hs prefix: run semantic search locally without agent round-trip
  if (text.toLowerCase().startsWith('/hs ')) {
    var hsQuery = text.slice(4).trim();
    if (!hsQuery) return;
    input.value = '';
    input.style.height = 'auto';
    _handleHsSearch(hsQuery);
    return;
  }

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

// Message copy (delegated) — attached to document so it catches clicks in
// any per-tab messages container (08-tabs.js creates a new #messages-{tabId}
// div per tab; only the first tab reuses the original #messages).
document.addEventListener('click', function(e) {
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

// Code block copy (delegated)
document.addEventListener('click', function(e) {
  if (!e.target.classList.contains('code-copy')) return;
  e.stopPropagation();
  var btn = e.target;
  var text = '';
  var encoded = btn.getAttribute('data-code') || '';
  if (encoded) {
    try { text = decodeURIComponent(escape(atob(encoded))); } catch (_e) { text = ''; }
  }
  if (!text) {
    // Fallback: read from the sibling <pre><code>
    var pre = btn.parentNode && btn.parentNode.querySelector('pre code');
    if (pre) text = pre.textContent;
  }
  function onSuccess() {
    btn.textContent = 'Copied';
    btn.classList.add('copied');
    setTimeout(function() { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 1200);
  }
  if (window.pywebview && pywebview.api && pywebview.api.copy_to_clipboard) {
    pywebview.api.copy_to_clipboard(text).then(function(ok) { if (ok) onSuccess(); });
  } else if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(onSuccess).catch(onSuccess);
  }
});

// ---- Keyboard shortcut overlay ----
var shortcutsVisible = false;
var shortcutsEl = null;

function toggleShortcuts() {
  if (!shortcutsEl) {
    shortcutsEl = document.createElement('div');
    shortcutsEl.className = 'shortcuts-overlay hv-overlay';
    shortcutsEl.innerHTML = '<div class="shortcuts-panel hv-panel-modal">'
      + '<div class="shortcuts-title">Keyboard Shortcuts</div>'
      + '<div class="shortcuts-grid">'
      + sc('/', 'Focus input')
      + sc('?', 'Toggle shortcuts')
      + sc('Ctrl+B', 'Toggle sidebar')
      + sc('Ctrl+N', 'New tab')
      + sc('Ctrl+T', 'New tab')
      + sc('Ctrl+W', 'Close tab')
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
// Greeting text is provided by window.HvGreeting from 00-shared-modules.js
// (mirrored from Hypervisor per WI-113 Phase 6). Kaomoji detection + .emote
// class application is handled by HvGreeting.applyTo(element).
var welcomeGreetingsFallback = ['ready when you are.'];

var welcomePrompts = [
  'What\'s in flight right now?',
  'Let\'s review a PR.',
  'Run a health check.'
];

function showWelcome() {
  var w = document.createElement('div');
  w.className = 'welcome hv-noise-field';
  w.innerHTML = '<svg class="welcome-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108.28 108.28" fill="currentColor"><path d="M107.94,71.76l-35.71-35.71-.04-.04h-34.8c-.63,0-1.14-.51-1.14-1.14V1.14c0-.63-.51-1.14-1.14-1.14H1.14C.51,0,0,.51,0,1.14v34.58c0,.3.12.59.33.8l33.56,33.56c.72.72.21,1.94-.8,1.94H1.14c-.63,0-1.14.51-1.14,1.14v33.98c0,.63.51,1.14,1.14,1.14h33.98c.63,0,1.14-.51,1.14-1.14v-33.73c0-.63.51-1.14,1.14-1.14h33.48c.63,0,1.14.51,1.14,1.14v33.73c0,.63.51,1.14,1.14,1.14h33.98c.63,0,1.14-.51,1.14-1.14v-34.58c0-.3-.12-.59-.33-.8Z"/><path d="M72.67,18.01l7.88,3.11c2.6,1.03,4.66,3.08,5.68,5.68l3.11,7.87c.18.45.82.45,1,0l3.11-7.87c1.03-2.6,3.08-4.66,5.68-5.68l7.88-3.11c.45-.18.45-.82,0-1l-7.88-3.11c-2.6-1.03-4.66-3.08-5.68-5.68l-3.11-7.87c-.18-.45-.82-.45-1,0l-3.11,7.87c-1.03,2.6-3.08,4.66-5.68,5.68l-7.88,3.11c-.45.18-.45.82,0,1Z"/></svg>'
    + '<span class="welcome-text"></span>'
    + '<div class="welcome-prompts"></div>';
  var greetingEl = w.querySelector('.welcome-text');
  if (window.HvGreeting) {
    window.HvGreeting.applyTo(greetingEl);
  } else {
    greetingEl.textContent = welcomeGreetingsFallback[0];
  }
  var chips = w.querySelector('.welcome-prompts');
  welcomePrompts.forEach(function(p) {
    var chip = document.createElement('button');
    chip.className = 'welcome-chip';
    chip.textContent = p;
    chip.onclick = function() { input.value = p; send(); };
    chips.appendChild(chip);
  });
  msgs.appendChild(w);
  // Defer noise mount to next animation frame so layout is complete before
  // HvNoiseField reads container dimensions. Matches the pattern used in
  // createTab (08-tabs.js) which reliably shows the dither. Previously used
  // setTimeout(0) which fires after script parse but not necessarily after
  // first layout — leaving the canvas mounted at 0x0 on initial launch.
  // WI-118 fix: per-module <script> blocks mean startWelcomeNoise (from
  // 06-welcome.js) may not be defined yet at RAF time when this is called
  // from initial script parse. Wait until all inline scripts have parsed.
  function _mountNoise() {
    if (typeof startWelcomeNoise === 'function') startWelcomeNoise();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() { requestAnimationFrame(_mountNoise); });
  } else {
    requestAnimationFrame(_mountNoise);
  }
}

// Show welcome on load if empty
showWelcome();

// WebGL cursor trail (WI-119) — same shared module Hypervisor uses.
// Idempotent, idle-suspending, a11y-gated internally.
if (window.HvCursorTrail) {
  window.HvCursorTrail.start(document.body);
}

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

// --- Launch Hypereye ---
function launchHypereye() {
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.launch_hypereye) {
    if (window.HvToast) HvToast.show({ variant: 'warn', message: 'Hypereye launch requires the desktop app' });
    return;
  }
  window.pywebview.api.launch_hypereye().then(function (result) {
    if (result && result.ok) {
      if (window.HvToast) HvToast.show({ variant: 'ok', message: 'Hypereye launched' });
    } else {
      if (window.HvToast) HvToast.show({ variant: 'error', message: 'Failed: ' + (result && result.error || 'unknown') });
    }
  });
}
window.launchHypereye = launchHypereye;

// --- /hs prefix: zero-token semantic search (no agent round-trip) ---
function _handleHsSearch(query) {
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.semantic_search) {
    if (window.HvToast) HvToast.show({ variant: 'warn', message: '/hs requires the desktop app bridge' });
    return;
  }

  // Show the query as a user message styled differently
  var el = document.createElement('div');
  el.className = 'msg msg-user msg-hs';
  el.innerHTML = '<span class="msg-meta"><span class="msg-role">/hs</span></span>';
  var body = document.createElement('span');
  body.className = 'msg-body';
  body.textContent = query;
  el.appendChild(body);
  msgs.appendChild(el);
  scrollBottom();

  // Run the search
  pywebview.api.semantic_search(query, 5).then(function(results) {
    var panel = document.createElement('div');
    panel.className = 'hs-results';

    if (!results || !results.length) {
      panel.innerHTML = '<div class="hs-empty">no semantic matches</div>';
    } else {
      var header = document.createElement('div');
      header.className = 'hs-header';
      header.textContent = results.length + ' match' + (results.length > 1 ? 'es' : '');
      panel.appendChild(header);

      for (var i = 0; i < results.length; i++) {
        var r = results[i];
        var row = document.createElement('div');
        row.className = 'hs-result';
        var path = r.path || '';
        var section = r.section ? ' §' + r.section : '';
        var score = r.similarity != null ? r.similarity.toFixed(2) : '';
        var snippet = r.content ? r.content.substring(0, 150) : '';
        if (r.content && r.content.length > 150) snippet += '...';
        row.innerHTML =
          '<div class="hs-result-header">' +
            '<span class="hv-chip hv-chip-outlined-muted">' + path.split('/').pop().replace('.md', '') + '</span>' +
            '<span class="hs-section">' + section + '</span>' +
            '<span class="hs-score">' + score + '</span>' +
          '</div>' +
          '<div class="hs-snippet">' + snippet + '</div>';
        panel.appendChild(row);
      }
    }

    msgs.appendChild(panel);
    scrollBottom();
  }).catch(function(err) {
    var errPanel = document.createElement('div');
    errPanel.className = 'hs-results';
    errPanel.innerHTML = '<div class="hs-empty">search failed</div>';
    msgs.appendChild(errPanel);
    scrollBottom();
  });
}

// --- Cursor companion box ---
// Relocated to Hyperkit (WI-142 follow-up) — window.HvCursorBox is loaded
// before this file. Edit the module in .hyperkit/js/cursor-box.js, not here.
(function() {
  if (!window.HvCursorBox) return;
  HvCursorBox.start(document.body);
})();
