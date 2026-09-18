/* ===== Hyperagent: UI Interactions ===== */

/* ===== Image attachments (paste + drag-drop) =============================
   pendingAttachments holds {mimeType, dataUrl} for images staged but not yet
   sent. Capture is gated on window._imageCapable (set from the ACP agent's
   advertised promptCapabilities.image). Data-URI prefix is stripped to raw
   base64 at send time — the ACP image content block wants raw base64. */
var pendingAttachments = [];

function _rawBase64(dataUrl) {
  // "data:image/png;base64,AAAA" -> "AAAA"
  var comma = dataUrl.indexOf(',');
  return comma === -1 ? dataUrl : dataUrl.slice(comma + 1);
}

function renderAttachments() {
  var strip = document.getElementById('attach-strip');
  if (!strip) return;
  strip.innerHTML = '';
  if (!pendingAttachments.length) {
    strip.classList.remove('has-items');
    return;
  }
  strip.classList.add('has-items');
  pendingAttachments.forEach(function(att, i) {
    var chip = document.createElement('div');
    chip.className = 'attach-chip';
    var img = document.createElement('img');
    img.className = 'attach-chip-thumb';
    img.src = att.dataUrl;
    img.alt = 'attached image';
    var rm = document.createElement('button');
    rm.className = 'attach-chip-remove';
    rm.type = 'button';
    rm.setAttribute('aria-label', 'Remove attachment');
    rm.textContent = 'x';
    rm.addEventListener('click', function() {
      pendingAttachments.splice(i, 1);
      renderAttachments();
    });
    chip.appendChild(img);
    chip.appendChild(rm);
    strip.appendChild(chip);
  });
}

function addImageFile(file) {
  if (!window._imageCapable) return;
  if (!file || !/^image\//.test(file.type)) return;
  var reader = new FileReader();
  reader.onload = function() {
    pendingAttachments.push({ mimeType: file.type, dataUrl: String(reader.result) });
    renderAttachments();
  };
  reader.readAsDataURL(file);
}

function clearAttachments() {
  pendingAttachments = [];
  renderAttachments();
}

// Clipboard paste: pull image items out of the paste event.
input.addEventListener('paste', function(e) {
  if (!window._imageCapable) return;
  var items = (e.clipboardData && e.clipboardData.items) || [];
  var found = false;
  for (var i = 0; i < items.length; i++) {
    if (items[i].kind === 'file' && /^image\//.test(items[i].type)) {
      var file = items[i].getAsFile();
      if (file) { addImageFile(file); found = true; }
    }
  }
  // Only suppress default when an image was captured, so text paste is intact.
  if (found) e.preventDefault();
});

// Drag-and-drop: accept image files dropped onto the input wrap.
(function wireDrop() {
  var wrap = document.querySelector('.input-wrap');
  if (!wrap) return;
  var _savedPlaceholder = null;
  function enterDropZone() {
    wrap.classList.add('drag-over');
    if (_savedPlaceholder === null) {
      _savedPlaceholder = input.getAttribute('placeholder') || '';
      input.setAttribute('placeholder', 'drop image');
    }
  }
  function leaveDropZone() {
    wrap.classList.remove('drag-over');
    if (_savedPlaceholder !== null) {
      input.setAttribute('placeholder', _savedPlaceholder);
      _savedPlaceholder = null;
    }
  }
  wrap.addEventListener('dragover', function(e) {
    if (!window._imageCapable) return;
    e.preventDefault();
    enterDropZone();
  });
  wrap.addEventListener('dragleave', function(e) {
    // Ignore leave events fired while moving onto a child (e.g. the textarea);
    // only restore when the pointer actually exits the wrap.
    if (e.relatedTarget && wrap.contains(e.relatedTarget)) return;
    leaveDropZone();
  });
  wrap.addEventListener('drop', function(e) {
    leaveDropZone();
    if (!window._imageCapable) return;
    var files = (e.dataTransfer && e.dataTransfer.files) || [];
    var imgs = [];
    for (var i = 0; i < files.length; i++) {
      if (/^image\//.test(files[i].type)) imgs.push(files[i]);
    }
    if (imgs.length) {
      e.preventDefault();
      imgs.forEach(addImageFile);
    }
  });
})();

// Attach button -> hidden file input -> stage selected images.
(function wireFileInput() {
  var fi = document.getElementById('attach-file-input');
  if (!fi) return;
  fi.addEventListener('change', function() {
    var files = fi.files || [];
    for (var i = 0; i < files.length; i++) addImageFile(files[i]);
    fi.value = '';  // allow re-selecting the same file
  });
})();

function _collectImagesForSend() {
  // Map pending attachments to the bridge shape {mimeType, data(rawBase64)}.
  return pendingAttachments.map(function(att) {
    return { mimeType: att.mimeType, data: _rawBase64(att.dataUrl) };
  });
}

function send() {
  var text = input.value.trim();
  var hasImages = pendingAttachments.length > 0;
  // Allow an image-only send; block only when there is neither.
  if (!text && !hasImages) return;

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
    var qText = text;
    var qImages = _collectImagesForSend();
    var qDataUrls = pendingAttachments.map(function(a) { return a.dataUrl; });
    input.value = '';
    input.style.height = 'auto';
    clearAttachments();
    pywebview.api.cancel('interrupt');
    // Queue the new prompt — poll until state becomes ready
    var attempts = 0;
    var pollReady = setInterval(function() {
      attempts++;
      if (state === 'ready') {
        clearInterval(pollReady);
        appendUser(qText, qDataUrls);
        if (!sessionTitle) firstPrompt = qText;
        window._lastUserPrompt = qText;
        pywebview.api.send_prompt(qText, qImages);
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
  var images = _collectImagesForSend();
  var dataUrls = pendingAttachments.map(function(a) { return a.dataUrl; });
  appendUser(text, dataUrls);
  input.value = '';
  input.style.height = 'auto';
  clearAttachments();
  window._lastUserPrompt = text;
  pywebview.api.send_prompt(text, images);
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
    shortcutsEl.className = 'shortcuts-overlay modal-overlay';
    shortcutsEl.innerHTML = '<div class="shortcuts-panel modal-panel">'
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
  'Run a health check.',
  { text: 'Check logs', action: '_showRecentLogs' }
];

function showWelcome() {
  var w = document.createElement('div');
  w.className = 'welcome noise-field';
  w.innerHTML = '<svg class="welcome-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 35.02 35.02" fill="currentColor"><path d="M13.58,20.85c.19,0,.28-.23.15-.36,0,0-5.08-5.08-6.1-6.11-.11-.11-.27-.14-.42-.09L.34,17.01c-.45.18-.45.82,0,1l7.05,2.79c.11.04.22.06.33.06.98,0,5.85,0,5.85,0Z"/><path d="M20.85,27.42v-6.3c0-.12-.1-.21-.21-.21h-6.26c-.12,0-.21.1-.21.21v6.3s0,.03-.01.05l2.85,7.21c.18.45.82.45,1,0l2.85-7.21s-.01-.03-.01-.05Z"/><path d="M34.68,17.01l-7.87-3.11c-2.6-1.03-4.66-3.08-5.68-5.68L18.01.34c-.18-.45-.82-.45-1,0l-2.85,7.21s.01.03.01.05v6.3c0,.12.1.21.21.21h6.09c.27,0,.52.11.71.3,1.14,1.14,5.26,5.26,6.2,6.2.12.12.29.15.44.09l6.85-2.71c.45-.18.45-.82,0-1Z"/></svg>'
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
    if (typeof p === 'string') {
      chip.textContent = p;
      chip.onclick = function() { input.value = p; send(); };
    } else {
      chip.textContent = p.text;
      chip.onclick = function() { if (window[p.action]) window[p.action](); };
    }
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
            '<span class="status-chip status-chip-outlined-muted">' + path.split('/').pop().replace('.md', '') + '</span>' +
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

// --- Check Logs (welcome chip action) ---
function _showRecentLogs() {
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_recent_logs) {
    if (window.HvToast) HvToast.show({ variant: 'warn', message: 'Log reader requires the desktop app bridge' });
    return;
  }

  // Remove welcome screen
  var w = msgs.querySelector('.welcome');
  if (w) {
    destroyWelcomeNoise(true);
    w.remove();
  }

  pywebview.api.get_recent_logs(40).then(function(results) {
    if (!results || !results.length) {
      if (window.HvToast) HvToast.show({ variant: 'warn', message: 'No log files found' });
      return;
    }
    for (var i = 0; i < results.length; i++) {
      var logFile = results[i];
      var panel = document.createElement('div');
      panel.className = 'log-panel';
      var header = document.createElement('div');
      header.className = 'log-panel-header';
      header.textContent = logFile.file;
      panel.appendChild(header);
      var body = document.createElement('pre');
      body.className = 'log-panel-body';
      body.textContent = logFile.lines.join('\n');
      panel.appendChild(body);
      msgs.appendChild(panel);
    }
    scrollBottom();
  }).catch(function(err) {
    if (window.HvToast) HvToast.show({ variant: 'error', message: 'Failed to read logs' });
  });
}
window._showRecentLogs = _showRecentLogs;

// --- Cursor companion box ---
// Relocated to Hyperkit (WI-142 follow-up) — window.HvCursorBox is loaded
// before this file. Edit the module in .hyperkit/js/cursor-box.js, not here.
(function() {
  if (!window.HvCursorBox) return;
  HvCursorBox.start(document.body);
})();
