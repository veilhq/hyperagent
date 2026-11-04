/* ===== Hyperagent: ACP Handlers + Message Helpers ===== */

function scrollBottom() { msgs.scrollTo({ top: msgs.scrollHeight, behavior: 'smooth' }); }

function collapseToolRow() {
  if (currentToolRow) {
    var cards = currentToolRow.querySelectorAll('.tool-indicator.show-label');
    for (var i = 0; i < cards.length; i++) cards[i].classList.remove('show-label');
  }
}

function _renderTraceCard() {
  // Collect semantic_search results from this turn's tool cards
  var sources = [];
  for (var id in toolCards) {
    var card = toolCards[id];
    if (!card || !card._toolData) continue;
    var name = (card._toolData.name || '').toLowerCase();
    if (name !== 'semantic_search') continue;
    var output = card._toolData.output;
    if (!output) continue;
    var items = [];
    try { items = typeof output === 'string' ? JSON.parse(output) : output; } catch (e) { continue; }
    if (!Array.isArray(items)) {
      // MCP returns [{content:[{text:"[...]"}]}] - unwrap if nested
      if (items.content && Array.isArray(items.content)) {
        for (var ci = 0; ci < items.content.length; ci++) {
          try { items = JSON.parse(items.content[ci].text || '[]'); } catch (e2) { items = []; }
        }
      } else { continue; }
    }
    for (var j = 0; j < items.length; j++) sources.push(items[j]);
  }
  if (!sources.length) return;

  var trace = document.createElement('div');
  trace.className = 'trace-card';

  var header = document.createElement('div');
  header.className = 'trace-header';
  header.innerHTML = '<span class="trace-toggle">&#9656;</span> TRACE [' + sources.length + ']';
  header.addEventListener('click', function () {
    trace.classList.toggle('trace-expanded');
    header.querySelector('.trace-toggle').innerHTML = trace.classList.contains('trace-expanded') ? '&#9662;' : '&#9656;';
  });
  trace.appendChild(header);

  var body = document.createElement('div');
  body.className = 'trace-body';
  for (var k = 0; k < sources.length; k++) {
    var s = sources[k];
    var row = document.createElement('div');
    row.className = 'trace-source';
    var path = s.path || '';
    var section = s.section ? ' §' + s.section : '';
    var score = s.similarity != null ? s.similarity.toFixed(2) : '';
    row.innerHTML =
      '<span class="status-chip status-chip-outlined-muted trace-chip">' + HvUtils.escapeHtml(path.split('/').pop().replace('.md', '')) + '</span>' +
      '<span class="trace-section">' + HvUtils.escapeHtml(section) + '</span>' +
      '<span class="trace-dots"></span>' +
      '<span class="trace-score">' + score + '</span>';
    body.appendChild(row);
  }
  trace.appendChild(body);
  msgs.appendChild(trace);
}

function msgTime() {
  var d = new Date();
  return String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
}

// Shared markup for the broken/missing-media placeholder primitive
// (.media-missing lives in hyperkit/css/primitives.css). Used when a
// prior-turn image cannot be rehydrated from session history. Lucide image-off.
function mediaMissingHTML() {
  return '<div class="media-missing" role="img" aria-label="image not available">'
    + '<svg class="media-missing-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="2" x2="22" y1="2" y2="22"/><path d="M10.41 10.41a2 2 0 1 1-2.83-2.83"/><line x1="13.5" x2="6" y1="13.5" y2="21"/><line x1="18" x2="21" y1="12" y2="15"/><path d="M3.59 3.59A1.99 1.99 0 0 0 3 5v14a2 2 0 0 0 2 2h14c.55 0 1.052-.22 1.41-.59"/><path d="M21 15V5a2 2 0 0 0-2-2H9"/></svg>'
    + '<span class="media-missing-label">image not available</span>'
    + '</div>';
}

function appendUser(text, images) {
  var w = msgs.querySelector('.welcome');
  if (w) { w.classList.add('welcome-exit'); setTimeout(function() { if (w.parentNode) w.remove(); }, 200); }
  var el = document.createElement('div');
  el.className = 'msg msg-user';
  el.innerHTML = '<span class="msg-meta"><span class="msg-role">(一︿一+ )</span><span class="msg-time">' + msgTime() + '</span></span>';
  // Inline attached images above the text body, when present.
  if (images && images.length) {
    var gallery = document.createElement('span');
    gallery.className = 'msg-images';
    images.forEach(function(src) {
      var im = document.createElement('img');
      im.className = 'msg-image';
      im.src = src;
      im.alt = 'attached image';
      gallery.appendChild(im);
    });
    el.appendChild(gallery);
  }
  if (text) {
    var body = document.createElement('span');
    body.className = 'msg-body';
    body.textContent = text;
    el.appendChild(body);
  }
  msgs.appendChild(el);
  scrollBottom();
}

function ensureAgentMsg() {
  if (!currentMsgEl) {
    // A new agent message starts a fresh phase - close any in-flight
    // thought batch so the next thought_chunk creates its own row below.
    resetThoughtStream();
    currentMsgEl = document.createElement('div');
    currentMsgEl.className = 'msg msg-agent';
    currentMsgEl.innerHTML = '<span class="msg-meta"><span class="msg-role">Agent</span><span class="msg-time">' + msgTime() + '</span></span>';
    msgs.appendChild(currentMsgEl);
    currentMsgText = '';
  }
  return currentMsgEl;
}

// --- Skill activation handler ---
// `activeSkills` is per-tab state (swapped by 08-tabs.js), name -> {name, desc}.
// The topbar skills chip (09-skills-picker.js) is a single shared element, so
// it is redrawn from the active tab's map only: 08-tabs.js syncs it on an
// active-tab activation and on every tab switch.
var activeSkills = {};

// Record a skill in the current tab's map; a repeat activation is a no-op.
function _trackSkill(name, desc) {
  if (!activeSkills[name]) activeSkills[name] = { name: name, desc: desc };
}

window.__acpSkillActivation = function(data) {
  var name = data.name || 'unknown';
  var desc = data.description || '';

  // Whether the shared chip redraws now is the caller's call: 08-tabs.js
  // syncs it for the active tab and on switch for background activations.
  _trackSkill(name, desc);

  // Inline skill card in message stream (per-tab DOM, safe to always render)
  var card = document.createElement('div');
  card.className = 'skill-card';
  card.innerHTML = '<span class="skill-card-name">&#9670; ' + HvUtils.escapeHtml(String(name)) + '</span>'
    + (desc ? '<span class="skill-card-desc">' + HvUtils.escapeHtml(String(desc)) + '</span>' : '');
  msgs.appendChild(card);
  scrollBottom();
};

// A session/load failed or timed out: the tab says so and offers Retry, and
// the load's hold on sends is released.
window.__acpSessionLoadFailed = function(data) {
  var tabId = data && data._tabId;
  function settle() {
    _loadingHistory = false;
    showTabLoadFailed(msgs, data && data.error, data && data.sessionId);
  }
  if (tabId && typeof tabs !== 'undefined' && tabs[tabId] && tabId !== activeTabId) {
    _withTabContext(tabId, settle);
  } else {
    settle();
    sendBtn.disabled = state !== 'ready' && state !== 'prompting';
  }
};

// --- ACP handlers (called from Python via evaluate_js) ---

window.__acpUpdate = function(update) {
  if (state === 'starting' || state !== 'prompting' || _loadingHistory) return;
  // Subagent text is shown in the thought element, not the main transcript.
  if (update.sessionUpdate === 'agent_message_chunk' && parentToolUseId(update)) {
    update = Object.assign({}, update, { sessionUpdate: 'agent_thought_chunk' });
  }
  switch (update.sessionUpdate) {
    case 'agent_message_chunk':
      var ti = document.getElementById('typing-indicator');
      if (ti) ti.remove();
      if (commandTurn) {
        commandTurn.buf += update.content.text;
        refreshCommandView(commandTurn);
        break;
      }
      collapseToolRow();
      currentToolRow = null;
      ensureAgentMsg();
      // Buffer the delta - drainStream renders it word-by-word with fade
      streamBuffer += update.content.text;
      if (!streamDraining) {
        streamDraining = true;
        setTimeout(drainStream, streamDrainMs);
      }
      break;

    case 'agent_thought_chunk':
      // Extended-thinking reasoning stream. Rendered in a collapsed-by-default
      // container above the visible message, with a dither activity indicator
      // that animates only while chunks are actively arriving.
      var tiT = document.getElementById('typing-indicator');
      if (tiT) tiT.remove();
      collapseToolRow();
      currentToolRow = null;
      appendThoughtChunk((update.content && update.content.text) || '');
      break;

    case 'user_message_chunk':
      // Echo of the user's own prompt over ACP - Hyperagent renders the user
      // message locally in appendUser() at send time, so this event is
      // intentionally ignored to avoid double-rendering.
      break;

    case 'tool_call':
      var ti2 = document.getElementById('typing-indicator');
      if (ti2) ti2.remove();
      // Flush buffered stream text before showing tool card
      flushStream();
      // Finalize any in-progress agent message so subsequent text appears
      // below the tool row - but only if it has content (avoid empty boxes).
      if (currentMsgEl && !currentMsgText.trim()) {
        currentMsgEl.parentNode.removeChild(currentMsgEl);
      } else if (currentMsgEl) {
        var sc2 = currentMsgEl.querySelector('.streaming-cursor');
        if (sc2) sc2.remove();
      }
      currentMsgEl = null;
      currentMsgText = '';
      // Thought row (if any) belonged to the just-finished reasoning phase;
      // the next thought batch will open a new row below the tool row.
      resetThoughtStream();
      // Ensure a tool-row container exists for this turn
      if (!currentToolRow) {
        currentToolRow = document.createElement('div');
        currentToolRow.className = 'tool-row';
        msgs.appendChild(currentToolRow);
      }
      var toolName = toolKindName(update);
      // Fade thinking bar color based on tool category
      setThinkingColorForTool(toolName);
      var card = buildToolCard(update.toolCallId, update.title || update.toolCallId, toolName,
        'tool-indicator-running show-label',
        hasKeys(update.rawInput) ? update.rawInput : update.input, null);
      toolCards[update.toolCallId] = card;
      currentToolRow.appendChild(card);
      toolTimers[update.toolCallId] = setTimeout(function() {
        if (card.classList.contains('tool-indicator-running')) card.classList.add('tool-indicator-long');
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
        resetThoughtStream();
        if (!currentToolRow) {
          currentToolRow = document.createElement('div');
          currentToolRow.className = 'tool-row';
          msgs.appendChild(currentToolRow);
        }
        tc = buildToolCard(update.toolCallId, update.title || update.toolCallId, toolKindName(update),
          'tool-indicator-running', null, null);
        toolCards[update.toolCallId] = tc;
        currentToolRow.appendChild(tc);
        scrollBottom();
      }
      if (tc) {
        // Claude Code sends a placeholder title first ("Terminal", "Edit") and
        // the real one (the command, the file path) in a later update.
        if (update.title && update.title !== tc._toolData.name) {
          setToolCardIdentity(tc, update.title, toolKindName(update));
        }
        if (hasKeys(update.rawInput)) tc._toolData.input = update.rawInput;
        var outputU = toolOutputOf(update);
        if (outputU) tc._toolData.output = outputU;
        var done = update.status === 'completed';
        var fail = update.status === 'failed';
        // Refinement updates carry no status; only a status change moves the card's state.
        if (update.status) {
          clearTimeout(toolTimers[update.toolCallId]);
          delete toolTimers[update.toolCallId];
          var grp = (tc.className.match(/tc-\w+/) || [''])[0];
          tc.className = 'tool-indicator ' + (done ? 'tool-indicator-completed' : fail ? 'tool-indicator-failed' : 'tool-indicator-running') + (tc.classList.contains('show-label') ? ' show-label' : '') + (grp ? ' ' + grp : '');
        }
        if (fail) {
          // Flash the error bar briefly as a notification
          var toolLabel = tc._toolData && tc._toolData.name ? tc._toolData.name : 'Tool';
          // Don't steal the bar from a crash that's offering a Reconnect action - 
          // a transient tool failure is far less important than the recovery path.
          if (!window.HaErrorBar.hasAction()) {
            window.HaErrorBar.setMessage(toolLabel + ' failed', 'warning');
            if (_toolFailTimer) clearTimeout(_toolFailTimer);
            _toolFailTimer = setTimeout(function() {
              // Only dismiss if it's still showing our tool failure message
              if (window.HaErrorBar.getMessage().indexOf('failed') > -1) window.HaErrorBar.hide();
            }, 3000);
          }
        }
      }
      break;

    case 'plan':
      // ACP agent plan (e.g. Claude Code's todo list). Each update carries the
      // complete entry list; the task panel replaces its contents with it.
      if (window.__acpPlanUpdate) window.__acpPlanUpdate(update.entries);
      break;

    default:
      // Unknown / unhandled ACP sessionUpdate. Debug-log so future protocol
      // additions become visible immediately rather than silently dropped.
      if (update && update.sessionUpdate) {
        console.debug('[ACP] unhandled sessionUpdate:', update.sessionUpdate, update);
      }
      break;
  }
};

window.__acpTurnEnd = function(data) {
  // Flush any remaining buffered stream text
  flushStream();
  // Remove streaming cursors
  msgs.querySelectorAll('.streaming-cursor').forEach(function(el) { el.remove(); });
  collapseToolRow();
  updateSessionMetrics(data._metadata);
  // Reset thinking bar color to accent
  resetThinkingColor();

  // A command view turn ends in its full-page view, not the transcript.
  if (commandTurn) {
    var doneTurn = commandTurn;
    commandTurn = null;
    var tiC = document.getElementById('typing-indicator');
    if (tiC) tiC.remove();
    finishCommandView(doneTurn, data);
    return;
  }

  // Clear skill badges from topbar
  // (Skills persist for the session - only clear on new session)

  // If cancelled, show short indicator with available stats
  if (data._cancelled) {
    if (data._metadata && data._metadata.contextUsagePercentage != null) {
      updateCtxMeter(data._metadata.contextUsagePercentage);
    }
    msgs.appendChild(buildTurnEndEl(data, true));
    scrollBottom();
    currentMsgEl = null;
    currentMsgText = '';
    toolCards = {};
    currentToolRow = null;
    resetThoughtStream();
    return;
  }

  // If the prompt failed (server-side error), show inline error with retry
  if (data._failed) {
    msgs.appendChild(buildTurnEndEl(data, true));
    scrollBottom();
    currentMsgEl = null;
    currentMsgText = '';
    toolCards = {};
    currentToolRow = null;
    resetThoughtStream();
    return;
  }

  // Request AI-generated title after first turn
  if (firstPrompt && !sessionTitle) {
    // Pass the originating tab_id so the title lands on the tab that sent
    // the prompt, even if the user has switched tabs by the time this fires.
    pywebview.api.generate_title(firstPrompt, (data && data._tabId) || null);
    firstPrompt = '';
  }

  // Build turn-end info string
  if (data._metadata && data._metadata.contextUsagePercentage != null) {
    updateCtxMeter(data._metadata.contextUsagePercentage);
  }
  msgs.appendChild(buildTurnEndEl(data, true));

  // Trace card: if semantic_search was called this turn, render retrieval sources
  _renderTraceCard();

  scrollBottom();

  currentMsgEl = null;
  currentMsgText = '';
  toolCards = {};
  currentToolRow = null;
  resetThoughtStream();
};

window.__acpStateChange = function(data) {
  var prevState = state;
  state = data.state;
  // Track the agent's advertised image capability so the attach affordance
  // can be gated - never offer image attach to an agent that would reject it.
  if (typeof data.imageCapable !== 'undefined') {
    window._imageCapable = !!data.imageCapable;
    app.classList.toggle('image-capable', window._imageCapable);
  }
  statusEl.textContent = state;
  statusEl.className = 'ha-cluster-chip topbar-status ' + state;
  sendBtn.disabled = (state !== 'ready' && state !== 'prompting') || _loadingHistory;
  app.classList.toggle('prompting', state === 'prompting');

  // Toast on successful reconnect (crashed → ready).
  if (state === 'ready' && prevState === 'crashed' && window.HvToast) {
    window.HvToast.show({ variant: 'success', message: 'reconnected' });
  }

  // Thinking indicator
  if (state === 'prompting') {
    showThinking();
    // Clear prompt-failure error bar when user sends a new prompt
    if (window._errorBarPromptFailed) {
      window._errorBarPromptFailed = false;
      window.HaErrorBar.hide();
    }
  } else {
    hideThinking();
  }

  // Dismiss the boot splash once the first agent is up. Later starts and
  // session loads show their loading state inside their own tab instead.
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
    // Only set a generic message if a specific error hasn't already been shown.
    if (!window.HaErrorBar.getMessage()) {
      window.HaErrorBar.setMessage('Connection lost.');
    }
    // The action lives in its own slot, so a subsequent __acpError message
    // cannot destroy it. Reconnect is scoped to the tab that actually crashed
    // rather than whichever tab happens to be active.
    var crashedTabId = (data && data._tabId) || null;
    window.HaErrorBar.setAction('Reconnect', function() {
      window.HaErrorBar.setMessage('Reconnecting...');
      window.HaErrorBar.clearAction();
      if (window.pywebview && window.pywebview.api && window.pywebview.api.reconnect) {
        pywebview.api.reconnect(crashedTabId);
      }
    });
  } else if (state === 'ready' || state === 'starting') {
    // Don't auto-dismiss if the error bar is showing a prompt failure - the
    // user needs to see it. It gets cleared on the next successful prompt or
    // when the user manually retries.
    if (!window._errorBarPromptFailed) {
      window.HaErrorBar.hide();
    }
  }
};


window.__acpError = function(data) {
  var msg = (data && data.error) ? String(data.error) : 'Unknown error';
  var source = (data && data.source) ? String(data.source) : '';
  // Track prompt failures so the error bar persists through the ready state change
  window._errorBarPromptFailed = (source === 'jsonrpc');
  // Message slot only - leaves any existing action (e.g. Reconnect) intact.
  window.HaErrorBar.setMessage(msg, 'error');
  // Also mirror to the JS console so failures show up under devtools without
  // needing to reach for the hyperagent.log file.
  try { console.error('[acp-error]', source ? '(' + source + ')' : '', msg, data); } catch (e) {}
};

// Agent is slow to answer initialize: say so in the starting tab's loading
// block, or in the info bar when the active tab has no loading block. The
// info bar clears itself on the next ready/starting state change.
window.__acpStartSlow = function(data) {
  var text = 'Still starting ' + ((data && data.agent) || 'agent') + '...';
  if (setTabLoadingText(tabContainerFor(data), text)) return;
  if (typeof _isActiveTab === 'function' && !_isActiveTab(data)) return;
  window.HaErrorBar.setMessage(text, 'info');
};

// Agent notice (rate-limit, policy, mode fallback...): toast it, prefixed with
// the tab title when it comes from a background tab.
window.__acpNotice = function(data) {
  if (!data || !data.title || !window.HvToast) return;
  var variant = { warning: 'warn', error: 'error', info: 'info' }[data.severity] || 'info';
  var prefix = '';
  if (data._tabId && typeof tabs !== 'undefined' && tabs[data._tabId] && data._tabId !== activeTabId) {
    prefix = (tabs[data._tabId].title || 'Tab') + ': ';
  }
  window.HvToast.show({ variant: variant, title: prefix + data.title, message: data.description || '' });
};

// Conversation compaction ({trigger, preTokens, postTokens, durationMs}): an
// inline card in the tab it happened in, with the retained share as a meter.
function formatTokenCount(count) {
  return count >= 1000 ? (count / 1000).toFixed(1) + 'k' : String(count);
}

function buildCompactionCard(data) {
  var retained = data.preTokens > 0 ? Math.min(100, Math.round(data.postTokens * 100 / data.preTokens)) : 0;
  var card = document.createElement('div');
  card.className = 'compaction-card';
  card.setAttribute('role', 'status');
  card.setAttribute('data-testid', 'compaction-card');
  var parts = [
    ['compaction-label', 'compacted'],
    ['status-chip status-chip-outlined-muted', data.trigger === 'auto' ? 'auto' : 'manual'],
    ['compaction-figures', formatTokenCount(data.preTokens) + ' -> ' + formatTokenCount(data.postTokens) + ' tokens']
  ];
  parts.forEach(function(part) {
    var span = document.createElement('span');
    span.className = part[0];
    span.textContent = part[1];
    card.appendChild(span);
  });
  var track = document.createElement('span');
  track.className = 'progress-track compaction-meter';
  track.title = retained + '% of the context kept';
  var fill = document.createElement('span');
  fill.className = 'progress-fill';
  fill.style.width = retained + '%';
  track.appendChild(fill);
  card.appendChild(track);
  if (typeof data.durationMs === 'number') {
    var duration = document.createElement('span');
    duration.className = 'compaction-duration';
    duration.textContent = (data.durationMs / 1000).toFixed(1) + 's';
    card.appendChild(duration);
  }
  return card;
}

window.__acpCompaction = function(data) {
  if (!data || typeof data.preTokens !== 'number' || typeof data.postTokens !== 'number') return;
  var container = tabContainerFor(data);
  if (!container) return;
  container.appendChild(buildCompactionCard(data));
  if (container === msgs) scrollBottom();
};

// Live context-window occupancy ({pct, used, size}) from an ACP usage_update.
// A background tab's value is stored in its render state so the shared meter
// shows it when that tab is activated.
window.__acpContextUsage = function(data) {
  if (!data || data.pct == null) return;
  if (data._tabId && typeof tabs !== 'undefined' && tabs[data._tabId] && data._tabId !== activeTabId) {
    var rs = tabs[data._tabId].renderState;
    if (rs) rs.ctxPercentage = data.pct;
    return;
  }
  updateCtxMeter(data.pct);
};

window.__acpAuthRequired = function(data) {
  var url = data && data.url;
  errorBar.classList.add('visible');
  if (url) {
    var safeUrl = HvUtils.escapeHtml(String(url));
    if (/^https?:\/\//i.test(String(url).trim())) {
      errorBar.innerHTML = 'ACP auth required - complete authentication at: <a href="' + safeUrl + '" target="_blank" rel="noopener" style="color:var(--interactive)">' + safeUrl + '</a>';
    } else {
      errorBar.innerHTML = 'ACP auth required - complete authentication at: ' + safeUrl;
    }
  } else {
    errorBar.innerHTML = 'ACP auth required - waiting for device flow...';
  }
};

window.__acpAuthComplete = function() {
  errorBar.classList.remove('visible');
};

window.__acpNewSession = function() {
  msgs.innerHTML = '';
  currentMsgEl = null;
  currentMsgText = '';
  streamBuffer = '';
  streamDraining = false;
  toolCards = {};
  toolNameMap = {};
  currentToolRow = null;
  resetThoughtStream();
  sessionTitle = '';
  firstPrompt = '';
  activeSkills = {};
  window._syncSkillsToActiveTab();
  var titleEl = document.getElementById('session-title');
  if (titleEl) { titleEl.textContent = ''; titleEl.classList.remove('has-title'); }
  updateCtxMeter(0);
  resetSessionMetrics();
  if (window.__acpTaskReset) window.__acpTaskReset();
  showWelcome();
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
  _loadingHistory = true;
  msgs.classList.add('no-animate');
  // History can arrive before the agent finishes connecting (kiro reads it
  // from disk first). Render it behind the loading block, which stays until
  // the tab is ready; an existing block is kept so it does not restart.
  var connecting = state === 'starting';
  clearTabContent(msgs, connecting);
  if (connecting) {
    if (!setTabLoadingText(msgs, 'Connecting to session')) showTabLoading(msgs, 'Connecting to session');
    msgs.classList.add('tab-connecting');
  }
  currentMsgEl = null;
  currentMsgText = '';
  toolCards = {};
  currentToolRow = null;
  resetThoughtStream();
  activeSkills = {};
  if (msgs.style.display !== 'none') window._syncSkillsToActiveTab();
  var messages = data && data.messages;
  // Restore the session's last task list (replayed sessions only).
  if (data && Array.isArray(data.plan) && window.__acpPlanUpdate) window.__acpPlanUpdate(data.plan);
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
    var total = messages.length;
    var idx = 0;
    // Detect if we're rendering into a hidden container (background tab).
    // If so, render all messages synchronously - rAF won't fire for hidden elements
    // and the context swap in _withTabContext won't persist across async callbacks.
    var isBackground = (msgs.style.display === 'none');
    // The tab being rendered. If the user switches away between animation
    // frames, the rest renders synchronously in that tab's own context so it
    // never lands in the newly active tab.
    var renderTabId = (!isBackground && typeof activeTabId !== 'undefined') ? activeTabId : null;
    function renderChunk() {
      if (renderTabId && activeTabId !== renderTabId) {
        var owner = renderTabId;
        renderTabId = null;
        if (!tabs[owner]) return; // tab closed mid-render
        _withTabContext(owner, function() { isBackground = true; renderChunk(); });
        return;
      }
      var chunkSize = isBackground ? total : 5;
      var end = Math.min(idx + chunkSize, total);
      while (idx < end) {
        var m = messages[idx];
        var el = document.createElement('div');
        // Format ts (unix seconds) as HH:MM if present
        var tStr = '';
        if (m.ts) {
          var _d = new Date(m.ts * 1000);
          tStr = String(_d.getHours()).padStart(2,'0') + ':' + String(_d.getMinutes()).padStart(2,'0');
        }
        var tSpan = tStr ? '<span class="msg-time">' + tStr + '</span>' : '';
        if (m.role === 'user') {
          el.className = 'msg msg-user';
          el.innerHTML = '<span class="msg-meta"><span class="msg-role">(一︿一+ )</span>' + tSpan + '</span>';
          // Rebuilt images render inline; only reconstructions that failed
          // (m.missingImages) fall back to the missing-media placeholder.
          var hasReal = m.images && m.images.length;
          if (hasReal || m.missingImages) {
            var gallery = document.createElement('span');
            gallery.className = 'msg-images';
            if (hasReal) {
              m.images.forEach(function(src) {
                var im = document.createElement('img');
                im.className = 'msg-image';
                im.src = src;
                im.alt = 'attached image';
                gallery.appendChild(im);
              });
            }
            for (var mi = 0; mi < (m.missingImages || 0); mi++) {
              gallery.insertAdjacentHTML('beforeend', mediaMissingHTML());
            }
            el.appendChild(gallery);
          }
          if (m.text) {
            var body = document.createElement('span');
            body.className = 'msg-body';
            body.textContent = m.text;
            el.appendChild(body);
          }
        } else if (m.role === 'skill') {
          // Render skill activation card + track for the topbar chip
          var sCard = document.createElement('div');
          sCard.className = 'skill-card';
          sCard.innerHTML = '<span class="skill-card-name">&#9670; ' + HvUtils.escapeHtml(String(m.name)) + '</span>'
            + (m.description ? '<span class="skill-card-desc">' + HvUtils.escapeHtml(String(m.description)) + '</span>' : '');
          msgs.appendChild(sCard);
          _trackSkill(m.name, m.description || '');
          if (!isBackground) window._syncSkillsToActiveTab();
          idx++;
          continue;
        } else if (m.role === 'tool') {
          // Collect consecutive tool entries into one tool-row
          var row = document.createElement('div');
          row.className = 'tool-row';
          while (idx < total && messages[idx].role === 'tool') {
            var tm = messages[idx];
            var tName = tm.name || 'tool';
            var tKind = tm.tool ? (CLAUDE_TOOL_ALIASES[tm.tool.toLowerCase()] || tm.tool) : tName;
            var tStatus = tm.status === 'failed' ? 'tool-indicator-failed' : 'tool-indicator-completed';
            row.appendChild(buildToolCard(null, tName, tKind, tStatus, tm.input, tm.output));
            idx++;
          }
          msgs.appendChild(row);
          continue;
        } else if (m.role === 'thought') {
          // Reasoning and subagent output render in a collapsed thought row.
          resetThoughtStream();
          appendThoughtChunk(m.text || '');
          setThoughtActive(false);
          resetThoughtStream();
          idx++;
          continue;
        } else if (m.role === 'compaction') {
          msgs.appendChild(buildCompactionCard(m));
          idx++;
          continue;
        } else if (m.role === 'turnend') {
          msgs.appendChild(buildTurnEndEl(m.data || {}, false));
          idx++;
          continue;
        } else {
          el.className = 'msg msg-agent';
          el._rawText = m.text;
          el.innerHTML = '<span class="msg-meta"><span class="msg-role">Agent</span>' + tSpan + '</span>' + renderMarkdown(m.text) + '<button class="msg-copy">Copy</button>';
        }
        msgs.appendChild(el);
        idx++;
      }
      if (idx < total) {
        if (isBackground) {
          // Synchronous: continue immediately (no rAF for hidden containers)
          renderChunk();
        } else {
          requestAnimationFrame(renderChunk);
        }
      } else {
        msgs.classList.remove('no-animate');
        _loadingHistory = false;
        msgs.scrollTop = msgs.scrollHeight;
        // Re-enable send button now that history is done loading
        if (state === 'ready') {
          sendBtn.disabled = false;
        }
      }
    }
    renderChunk();
  } else {
    msgs.classList.remove('no-animate');
    _loadingHistory = false;
    msgs.scrollTop = msgs.scrollHeight;
  }
};