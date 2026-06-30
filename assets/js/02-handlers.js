/* ===== Hyperagent: ACP Handlers ===== */

function scrollBottom() { msgs.scrollTo({ top: msgs.scrollHeight, behavior: 'smooth' }); }

function collapseToolRow() {
  if (currentToolRow) {
    var cards = currentToolRow.querySelectorAll('.tool-card.show-label');
    for (var i = 0; i < cards.length; i++) cards[i].classList.remove('show-label');
  }
}

function msgTime() {
  var d = new Date();
  return String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
}

function appendUser(text) {
  var w = msgs.querySelector('.welcome');
  if (w) w.remove();
  var el = document.createElement('div');
  el.className = 'msg msg-user';
  el.innerHTML = '<span class="msg-meta"><span class="msg-role">Operator</span><span class="msg-time">' + msgTime() + '</span></span>';
  var body = document.createElement('span');
  body.className = 'msg-body';
  body.textContent = text;
  el.appendChild(body);
  msgs.appendChild(el);
  scrollBottom();
}

function ensureAgentMsg() {
  if (!currentMsgEl) {
    currentMsgEl = document.createElement('div');
    currentMsgEl.className = 'msg msg-agent';
    currentMsgEl.innerHTML = '<span class="msg-meta"><span class="msg-role">Agent</span><span class="msg-time">' + msgTime() + '</span></span>';
    msgs.appendChild(currentMsgEl);
    currentMsgText = '';
  }
  return currentMsgEl;
}

// --- Tool icon mapping ---
function toolIcon(name) {
  var n = (name || '').toLowerCase();
  if (n.indexOf('read') > -1 || n.indexOf('file') > -1) return '<';
  if (n.indexOf('write') > -1 || n.indexOf('create') > -1 || n.indexOf('edit') > -1) return '>';
  if (n.indexOf('shell') > -1 || n.indexOf('command') > -1 || n.indexOf('terminal') > -1) return '$';
  if (n.indexOf('search') > -1 || n.indexOf('grep') > -1 || n.indexOf('find') > -1) return '?';
  if (n.indexOf('web') > -1 || n.indexOf('fetch') > -1 || n.indexOf('http') > -1) return '~';
  if (n.indexOf('code') > -1 || n.indexOf('symbol') > -1) return '@';
  if (n.indexOf('glob') > -1 || n.indexOf('directory') > -1) return '/';
  if (n.indexOf('knowledge') > -1 || n.indexOf('index') > -1) return '%';
  if (n.indexOf('aws') > -1) return '^';
  if (n.indexOf('git') > -1 || n.indexOf('repo') > -1 || n.indexOf('pull') > -1) return '&';
  // Hypervisor MCP tools
  if (n.indexOf('work_item') > -1 || n.indexOf('move_work') > -1 || n.indexOf('outline') > -1) return '=';
  if (n.indexOf('tag') > -1) return '#';
  if (n.indexOf('health') > -1 || n.indexOf('validate') > -1 || n.indexOf('migrate') > -1) return '!';
  if (n.indexOf('session_brief') > -1 || n.indexOf('suggest_next') > -1 || n.indexOf('recent_activity') > -1 || n.indexOf('stale') > -1) return '.';
  if (n.indexOf('hyperspace') > -1 || n.indexOf('similar') > -1 || n.indexOf('hypervisor') > -1) return '*';
  return '+';
}

// --- Tool MCP group for badge color ---
function toolGroup(name) {
  var n = (name || '').toLowerCase();
  if (n.indexOf('aws') > -1) return 'aws';
  if (n.indexOf('web') > -1 || n.indexOf('fetch') > -1 || n.indexOf('http') > -1) return 'web';
  if (n.indexOf('git') > -1 || n.indexOf('repo') > -1 || n.indexOf('pull') > -1 || n.indexOf('wit') > -1) return 'devops';
  if (n.indexOf('work_item') > -1 || n.indexOf('move_work') > -1 || n.indexOf('outline') > -1 || n.indexOf('tag') > -1 || n.indexOf('health') > -1 || n.indexOf('validate') > -1 || n.indexOf('migrate') > -1 || n.indexOf('session_brief') > -1 || n.indexOf('suggest_next') > -1 || n.indexOf('recent_activity') > -1 || n.indexOf('stale') > -1 || n.indexOf('hyperspace') > -1 || n.indexOf('similar') > -1 || n.indexOf('hypervisor') > -1 || n.indexOf('search_hyper') > -1 || n.indexOf('create_doc') > -1 || n.indexOf('update_doc') > -1) return 'hyper';
  if (n.indexOf('knowledge') > -1 || n.indexOf('index') > -1) return 'knowledge';
  return 'core';
}

// --- Truncate tool detail for display ---
function truncateDetail(data) {
  var str = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  var esc = str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  if (esc.length > 600) esc = esc.slice(0, 600) + '\n… (truncated)';
  return esc;
}

// --- Long-running tool detection ---
var toolTypicalMs = {
  'read': 3000, 'write': 3000, 'glob': 5000, 'grep': 5000,
  'code': 10000, 'search': 10000, 'knowledge': 10000,
  'web_fetch': 15000, 'web_search': 15000,
  'shell': 30000, 'use_aws': 30000, 'subagent': 120000
};
var toolTimers = {};

function getToolTimeout(name) {
  var n = (name || '').toLowerCase();
  var keys = Object.keys(toolTypicalMs);
  for (var i = 0; i < keys.length; i++) {
    if (n.indexOf(keys[i]) > -1) return toolTypicalMs[keys[i]];
  }
  return 60000;
}

// --- Tool name map (populated by dev channel hints before standard events arrive) ---
var toolNameMap = {};

window.__acpToolHint = function(data) {
  if (data.toolCallId && data.name) {
    toolNameMap[data.toolCallId] = data.name;
  }
};

// --- Skill activation handler ---
var activeSkills = {};

function getSkillStrip() {
  return document.getElementById('skill-strip');
}

window.__acpSkillActivation = function(data) {
  var name = data.name || 'unknown';
  var desc = data.description || '';
  var strip = getSkillStrip();

  // Topbar badge (deduplicate by name)
  if (!activeSkills[name] && strip) {
    var badge = document.createElement('span');
    badge.className = 'skill-badge';
    badge.innerHTML = '<span class="skill-badge-icon">&#9670;</span>' + name;
    badge.title = desc;
    strip.appendChild(badge);
    activeSkills[name] = badge;
  }

  // Inline skill card in message stream
  var card = document.createElement('div');
  card.className = 'skill-card';
  card.innerHTML = '<span class="skill-card-name">&#9670; ' + name + '</span>'
    + (desc ? '<span class="skill-card-desc">' + desc + '</span>' : '');
  msgs.appendChild(card);
  scrollBottom();
};

// --- ACP handlers (called from Python via evaluate_js) ---

window.__acpUpdate = function(update) {
  if (state === 'starting' || state !== 'prompting' || window._loadingHistory) return;
  switch (update.sessionUpdate) {
    case 'agent_message_chunk':
      var ti = document.getElementById('typing-indicator');
      if (ti) ti.remove();
      collapseToolRow();
      currentToolRow = null;
      ensureAgentMsg();
      currentMsgText += update.content.text;
      currentMsgEl._rawText = currentMsgText;
      var meta = currentMsgEl.querySelector('.msg-meta');
      currentMsgEl.innerHTML = '';
      if (meta) currentMsgEl.appendChild(meta);
      currentMsgEl.insertAdjacentHTML('beforeend', renderMarkdown(currentMsgText) + '<span class="streaming-cursor"></span><button class="msg-copy">Copy</button>');
      scrollBottom();
      break;

    case 'tool_call':
      var ti2 = document.getElementById('typing-indicator');
      if (ti2) ti2.remove();
      // Finalize any in-progress agent message so subsequent text appears
      // below the tool row — but only if it has content (avoid empty boxes).
      if (currentMsgEl && !currentMsgText.trim()) {
        currentMsgEl.parentNode.removeChild(currentMsgEl);
      } else if (currentMsgEl) {
        var sc2 = currentMsgEl.querySelector('.streaming-cursor');
        if (sc2) sc2.remove();
      }
      currentMsgEl = null;
      currentMsgText = '';
      // Ensure a tool-row container exists for this turn
      if (!currentToolRow) {
        currentToolRow = document.createElement('div');
        currentToolRow.className = 'tool-row';
        msgs.appendChild(currentToolRow);
      }
      var card = document.createElement('div');
      var toolName = toolNameMap[update.toolCallId] || update.title || update.toolCallId;
      card.className = 'tool-card running show-label tc-' + toolGroup(toolName);
      card.id = 'tool-' + update.toolCallId;
      var icon = toolIcon(toolName);
      var label = update.title || update.toolCallId;
      card.innerHTML = '<span class="tool-card-icon">' + icon + '</span><span class="tool-card-label">' + label + '</span>';
      card.title = label;
      // Store input data for detail panel
      card._toolData = { name: label, input: update.input || null, output: null };
      card.addEventListener('click', function() {
        var existing = card._detailEl;
        if (existing) {
          existing.classList.toggle('visible');
          card.classList.toggle('show-label');
        } else {
          card.classList.toggle('show-label');
          // Create detail panel on first click if we have data
          var d = card._toolData;
          if (d && (d.input || d.output)) {
            var det = document.createElement('div');
            det.className = 'tool-detail visible';
            var content = '';
            if (d.input) content += '<div class="tool-detail-header">Input</div><div class="tool-detail-body">' + truncateDetail(d.input) + '</div>';
            if (d.output) content += '<div class="tool-detail-header">Output</div><div class="tool-detail-body">' + truncateDetail(d.output) + '</div>';
            det.innerHTML = content;
            currentToolRow.parentNode.insertBefore(det, currentToolRow.nextSibling);
            card._detailEl = det;
          }
        }
      });
      toolCards[update.toolCallId] = card;
      currentToolRow.appendChild(card);
      toolTimers[update.toolCallId] = setTimeout(function() {
        if (card.classList.contains('running')) card.classList.add('long-running');
      }, getToolTimeout(update.title || update.toolCallId));
      scrollBottom();
      break;

    case 'tool_call_update':
      var tc = toolCards[update.toolCallId];
      if (!tc) {
        // Auto-create card for orphaned tool_call_update (server skipped tool_call event)
        var ti3 = document.getElementById('typing-indicator');
        if (ti3) ti3.remove();
        if (currentMsgEl && !currentMsgText.trim()) {
          currentMsgEl.parentNode.removeChild(currentMsgEl);
        } else if (currentMsgEl) {
          var sc3 = currentMsgEl.querySelector('.streaming-cursor');
          if (sc3) sc3.remove();
        }
        currentMsgEl = null;
        currentMsgText = '';
        if (!currentToolRow) {
          currentToolRow = document.createElement('div');
          currentToolRow.className = 'tool-row';
          msgs.appendChild(currentToolRow);
        }
        tc = document.createElement('div');
        var toolNameU = toolNameMap[update.toolCallId] || update.title || update.toolCallId;
        tc.className = 'tool-card running tc-' + toolGroup(toolNameU);
        tc.id = 'tool-' + update.toolCallId;
        var iconU = toolIcon(toolNameU);
        var labelU = update.title || update.toolCallId;
        tc.innerHTML = '<span class="tool-card-icon">' + iconU + '</span><span class="tool-card-label">' + labelU + '</span>';
        tc.title = labelU;
        tc._toolData = { name: labelU, input: null, output: null };
        tc.addEventListener('click', function() {
          var existing = tc._detailEl;
          if (existing) { existing.classList.toggle('visible'); tc.classList.toggle('show-label'); }
          else { tc.classList.toggle('show-label'); var d = tc._toolData; if (d && (d.input || d.output)) { var det = document.createElement('div'); det.className = 'tool-detail visible'; var content = ''; if (d.input) content += '<div class="tool-detail-header">Input</div><div class="tool-detail-body">' + truncateDetail(d.input) + '</div>'; if (d.output) content += '<div class="tool-detail-header">Output</div><div class="tool-detail-body">' + truncateDetail(d.output) + '</div>'; det.innerHTML = content; currentToolRow.parentNode.insertBefore(det, currentToolRow.nextSibling); tc._detailEl = det; } }
        });
        toolCards[update.toolCallId] = tc;
        currentToolRow.appendChild(tc);
        scrollBottom();
      }
      if (tc) {
        clearTimeout(toolTimers[update.toolCallId]);
        delete toolTimers[update.toolCallId];
        var done = update.status === 'completed';
        var fail = update.status === 'failed';
        var grp = (tc.className.match(/tc-\w+/) || [''])[0];
        tc.className = 'tool-card ' + (done ? 'completed' : fail ? 'failed' : 'running') + (tc.classList.contains('show-label') ? ' show-label' : '') + (grp ? ' ' + grp : '');
        // Store output
        if (update.output) tc._toolData.output = update.output;
        if (update.result) tc._toolData.output = update.result;
        // Auto-show error inline for failed tools
        if (fail) {
          var errText = update.output || update.result || update.error || '';
          if (typeof errText === 'object') errText = errText.message || errText.error || JSON.stringify(errText);
          if (errText) {
            var errEl = document.createElement('div');
            errEl.className = 'tool-error-inline';
            var esc = String(errText).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
            if (esc.length > 300) esc = esc.slice(0, 300) + '…';
            errEl.textContent = esc;
            // Insert after the tool-row containing this card
            var row = tc.closest('.tool-row');
            if (row && row.parentNode) {
              row.parentNode.insertBefore(errEl, row.nextSibling);
            } else {
              msgs.appendChild(errEl);
            }
            scrollBottom();
          }
          // Flash the error bar briefly as a notification
          var toolLabel = tc._toolData && tc._toolData.name ? tc._toolData.name : 'Tool';
          errorBar.textContent = toolLabel + ' failed';
          errorBar.classList.add('visible');
          if (window._toolFailTimer) clearTimeout(window._toolFailTimer);
          window._toolFailTimer = setTimeout(function() {
            // Only dismiss if it's still showing our tool failure message
            if (errorBar.textContent.indexOf('failed') > -1) errorBar.classList.remove('visible');
          }, 3000);
        }
      }
      break;
  }
};

window.__acpTurnEnd = function(data) {
  // Remove streaming cursors
  msgs.querySelectorAll('.streaming-cursor').forEach(function(el) { el.remove(); });
  collapseToolRow();

  // Clear skill badges from topbar
  // (Skills persist for the session — only clear on new session)

  // If cancelled, show short indicator instead of full stats
  if (data._cancelled) {
    var cdiv = document.createElement('div');
    cdiv.className = 'turn-end cancelled';
    cdiv.textContent = '— cancelled';
    msgs.appendChild(cdiv);
    scrollBottom();
    currentMsgEl = null;
    currentMsgText = '';
    toolCards = {};
    currentToolRow = null;
    return;
  }

  // Request AI-generated title after first turn
  if (firstPrompt && !sessionTitle) {
    pywebview.api.generate_title(firstPrompt);
    firstPrompt = '';
  }

  // Build turn-end info string
  var parts = [];
  if (data._elapsed) parts.push(data._elapsed + 's');
  if (data._metadata) {
    var m = data._metadata;
    if (m.contextUsagePercentage != null) {
      parts.push(Math.round(m.contextUsagePercentage) + '% ctx');
      updateCtxMeter(m.contextUsagePercentage);
    }
    if (m.usage) parts.push(m.usage);
    if (m.cost) parts.push(m.cost);
    if (m.meteringUsage && m.meteringUsage.length) {
      var total = 0;
      for (var i = 0; i < m.meteringUsage.length; i++) total += m.meteringUsage[i].value;
      parts.push(total.toFixed(2) + ' credits');
    } else if (m.creditsUsed) { parts.push(m.creditsUsed + ' credits'); }
    if (m.inputTokens || m.outputTokens) parts.push((m.inputTokens||0) + '→' + (m.outputTokens||0) + ' tok');
  }
  var div = document.createElement('div');
  div.className = 'turn-end';
  div.textContent = '— done' + (parts.length ? ' · ' + parts.join(' · ') : '');
  msgs.appendChild(div);
  scrollBottom();

  currentMsgEl = null;
  currentMsgText = '';
  toolCards = {};
  currentToolRow = null;
};

window.__acpStateChange = function(data) {
  state = data.state;
  statusEl.textContent = state;
  statusEl.className = 'topbar-status ' + state;
  sendBtn.disabled = state !== 'ready';
  app.classList.toggle('prompting', state === 'prompting');

  // Thinking indicator
  if (state === 'prompting') {
    showThinking();
  } else {
    hideThinking();
  }

  // Show loading when switching sessions
  if (state === 'starting' && !document.getElementById('ha-splash')) {
    var splash = document.createElement('div');
    splash.id = 'ha-splash';
    splash.className = 'ha-splash';
    splash.innerHTML = '<div class="ha-splash-flag"><svg class="ha-splash-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108.28 108.28" fill="currentColor"><path d="M107.94,71.76l-35.71-35.71-.04-.04h-34.8c-.63,0-1.14-.51-1.14-1.14V1.14c0-.63-.51-1.14-1.14-1.14H1.14C.51,0,0,.51,0,1.14v34.58c0,.3.12.59.33.8l33.56,33.56c.72.72.21,1.94-.8,1.94H1.14c-.63,0-1.14.51-1.14,1.14v33.98c0,.63.51,1.14,1.14,1.14h33.98c.63,0,1.14-.51,1.14-1.14v-33.73c0-.63.51-1.14,1.14-1.14h33.48c.63,0,1.14.51,1.14,1.14v33.73c0,.63.51,1.14,1.14,1.14h33.98c.63,0,1.14-.51,1.14-1.14v-34.58c0-.3-.12-.59-.33-.8Z"/><path d="M72.67,18.01l7.88,3.11c2.6,1.03,4.66,3.08,5.68,5.68l3.11,7.87c.18.45.82.45,1,0l3.11-7.87c1.03-2.6,3.08-4.66,5.68-5.68l7.88-3.11c.45-.18.45-.82,0-1l-7.88-3.11c-2.6-1.03-4.66-3.08-5.68-5.68l-3.11-7.87c-.18-.45-.82-.45-1,0l-3.11,7.87c-1.03,2.6-3.08,4.66-5.68,5.68l-7.88,3.11c-.45.18-.45.82,0,1Z"/></svg></div>'
      + '<div class="ha-splash-loading">Loading session history</div>'
      + '<div class="ha-splash-pct" id="ha-splash-pct">0%</div>';
    document.body.appendChild(splash);
  }

  // Dismiss splash on ready
  if (state === 'ready') {
    var splash = document.getElementById('ha-splash');
    if (splash) {
      splash.classList.add('hidden');
      setTimeout(function() { if (splash.parentNode) splash.parentNode.removeChild(splash); }, 700);
    }
  }

  if (state === 'crashed') {
    // Dismiss splash so error bar is visible (e.g. auth failure on cold start)
    var splashCrash = document.getElementById('ha-splash');
    if (splashCrash) {
      splashCrash.classList.add('hidden');
      setTimeout(function() { if (splashCrash.parentNode) splashCrash.parentNode.removeChild(splashCrash); }, 700);
    }
    // Only set generic message if no specific error already displayed
    if (!errorBar.classList.contains('visible')) {
      errorBar.innerHTML = 'Connection lost. <a href="#" onclick="pywebview.api.reconnect();return false">Reconnect</a>';
    } else if (errorBar.innerHTML.indexOf('Reconnect') === -1) {
      errorBar.innerHTML += ' <a href="#" onclick="pywebview.api.reconnect();return false">Reconnect</a>';
    }
    errorBar.classList.add('visible');
  } else {
    errorBar.classList.remove('visible');
  }
};

// --- CRT scan thinking indicator ---
var thinkingBar = document.getElementById('thinking-bar');

function showThinking() {
  thinkingBar.classList.add('active');
  // Show inline typing dots
  if (!document.getElementById('typing-indicator')) {
    var ti = document.createElement('div');
    ti.id = 'typing-indicator';
    ti.className = 'typing-indicator';
    ti.innerHTML = '<span class="thinking-dot"></span>';
    msgs.appendChild(ti);
    scrollBottom();
  }
}

function hideThinking() {
  thinkingBar.classList.remove('active');
  var ti = document.getElementById('typing-indicator');
  if (ti) ti.remove();
}

window.__acpError = function(data) {
  errorBar.classList.add('visible');
  errorBar.textContent = data.error;
};

window.__acpAuthRequired = function(data) {
  var url = data && data.url;
  errorBar.classList.add('visible');
  if (url) {
    errorBar.innerHTML = 'Login required — complete authentication at: <a href="' + url + '" target="_blank" style="color:var(--accent)">' + url + '</a>';
  } else {
    errorBar.innerHTML = 'Login required — waiting for device flow...';
  }
};

window.__acpAuthComplete = function() {
  errorBar.classList.remove('visible');
};

window.__acpNewSession = function() {
  msgs.innerHTML = '';
  currentMsgEl = null;
  currentMsgText = '';
  toolCards = {};
  toolNameMap = {};
  currentToolRow = null;
  sessionTitle = '';
  firstPrompt = '';
  var skillStripEl = getSkillStrip();
  if (skillStripEl) skillStripEl.innerHTML = '';
  activeSkills = {};
  var titleEl = document.getElementById('session-title');
  if (titleEl) { titleEl.textContent = ''; titleEl.classList.remove('has-title'); }
  updateCtxMeter(0);
  if (window.showWelcome) showWelcome();
};

window.__acpSessionTitle = function(data) {
  sessionTitle = data.title || '';
  var titleEl = document.getElementById('session-title');
  if (titleEl && sessionTitle) {
    titleEl.textContent = sessionTitle;
    titleEl.classList.add('has-title');
  }
};

window.__acpSessionLoaded = function(data) {
  if (window._loadingHistoryTimeout) { clearTimeout(window._loadingHistoryTimeout); window._loadingHistoryTimeout = null; }
  window._loadingHistory = true;
  msgs.classList.add('no-animate');
  msgs.innerHTML = '';
  currentMsgEl = null;
  currentMsgText = '';
  toolCards = {};
  currentToolRow = null;
  var messages = data && data.messages;
  // Derive session title from first user message
  if (messages && messages.length) {
    for (var ti = 0; ti < messages.length; ti++) {
      if (messages[ti].role === 'user') {
        var t = messages[ti].text || '';
        sessionTitle = t.length > 30 ? t.slice(0, 30).trim() + '...' : t;
        var titleEl = document.getElementById('session-title');
        if (titleEl) { titleEl.textContent = sessionTitle; titleEl.classList.add('has-title'); }
        break;
      }
    }
  }
  if (messages && messages.length) {
    var pctEl = document.getElementById('ha-splash-pct');
    var total = messages.length;
    var idx = 0;
    function renderChunk() {
      var end = Math.min(idx + 5, total);
      while (idx < end) {
        var m = messages[idx];
        var el = document.createElement('div');
        if (m.role === 'user') {
          el.className = 'msg msg-user';
          el.innerHTML = '<span class="msg-meta"><span class="msg-role">Operator</span></span>';
          var body = document.createElement('span');
          body.className = 'msg-body';
          body.textContent = m.text;
          el.appendChild(body);
        } else {
          el.className = 'msg msg-agent';
          el.innerHTML = '<span class="msg-meta"><span class="msg-role">Agent</span></span>' + renderMarkdown(m.text);
        }
        msgs.appendChild(el);
        idx++;
      }
      if (pctEl) pctEl.textContent = Math.round((idx / total) * 100) + '%';
      if (idx < total) {
        requestAnimationFrame(renderChunk);
      } else {
        msgs.scrollTop = msgs.scrollHeight;
        msgs.classList.remove('no-animate');
        window._loadingHistory = false;
        // If agent is already ready, dismiss splash; otherwise show waiting message
        if (state === 'ready') {
          var splash = document.getElementById('ha-splash');
          if (splash) {
            splash.classList.add('hidden');
            setTimeout(function() { if (splash.parentNode) splash.parentNode.removeChild(splash); }, 700);
          }
        } else {
          var loadingEl = document.querySelector('.ha-splash-loading');
          if (loadingEl) loadingEl.textContent = 'Waiting for agent start';
          if (pctEl) pctEl.innerHTML = '<div class="ha-splash-blink"></div>';
        }
      }
    }
    renderChunk();
  } else {
    msgs.scrollTop = msgs.scrollHeight;
    msgs.classList.remove('no-animate');
    window._loadingHistory = false;
  }
};
