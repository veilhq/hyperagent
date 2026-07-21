/* ===== Hyperagent: Multi-Tab Architecture ===== */

// --- Tab state ---
var tabs = {};           // {tabId: {el, msgsEl, title, sessionId, state, unread, renderState}}
var activeTabId = null;
var sidebarTabList = null;

// --- Per-tab render state ---
// These are the globals from 02-handlers.js that need isolation per tab.
// We save/restore them when switching context for rendering.

function _newRenderState() {
  return {
    currentMsgEl: null,
    currentMsgText: '',
    streamBuffer: '',
    streamDraining: false,
    toolCards: {},
    currentToolRow: null,
    toolNameMap: {},
    sessionTitle: '',
    firstPrompt: '',
    activeSkills: {},
    // Task panel state (per-tab)
    taskList: [],
    taskDescription: '',
    taskPanelVisible: false,
    taskPanelCollapsed: false,
    // History loading state (per-tab)
    _loadingHistory: false,
    _loadingHistoryTimeout: null
  };
}

function _saveRenderState(tabId) {
  if (!tabs[tabId]) return;
  tabs[tabId].renderState = {
    currentMsgEl: currentMsgEl,
    currentMsgText: currentMsgText,
    streamBuffer: streamBuffer,
    streamDraining: streamDraining,
    toolCards: toolCards,
    currentToolRow: currentToolRow,
    toolNameMap: toolNameMap,
    sessionTitle: sessionTitle,
    firstPrompt: firstPrompt,
    activeSkills: activeSkills,
    // Task panel state
    taskList: taskList,
    taskDescription: taskDescription,
    taskPanelVisible: taskPanel ? taskPanel.classList.contains('visible') : false,
    taskPanelCollapsed: taskPanel ? taskPanel.classList.contains('collapsed') : false,
    // History loading state
    _loadingHistory: _loadingHistory,
    _loadingHistoryTimeout: _loadingHistoryTimeout
  };
}

function _loadRenderState(tabId) {
  var rs = tabs[tabId] && tabs[tabId].renderState;
  if (!rs) rs = _newRenderState();
  currentMsgEl = rs.currentMsgEl;
  currentMsgText = rs.currentMsgText;
  streamBuffer = rs.streamBuffer;
  streamDraining = rs.streamDraining;
  toolCards = rs.toolCards;
  currentToolRow = rs.currentToolRow;
  toolNameMap = rs.toolNameMap;
  sessionTitle = rs.sessionTitle;
  firstPrompt = rs.firstPrompt;
  activeSkills = rs.activeSkills;
  // Restore task data globals
  taskList = rs.taskList;
  taskDescription = rs.taskDescription;
  // Restore history loading state
  _loadingHistory = rs._loadingHistory;
  _loadingHistoryTimeout = rs._loadingHistoryTimeout;
}

// Update task panel DOM to reflect the active tab's task state.
// Called only on switchTab — NOT during background context swaps.
function _syncTaskPanelToActiveTab() {
  var rs = tabs[activeTabId] && tabs[activeTabId].renderState;
  if (!rs) rs = _newRenderState();
  if (taskPanel) {
    if (rs.taskPanelVisible && rs.taskList.length) {
      taskPanel.classList.add('visible');
      taskPanel.classList.toggle('collapsed', rs.taskPanelCollapsed);
      renderTasks();
    } else {
      taskPanel.classList.remove('visible');
    }
  }
}

// Execute a function with a specific tab's render context active,
// then restore the previous context. Used for background tab rendering.
function _withTabContext(tabId, fn) {
  var prevTab = activeTabId;
  var prevMsgs = msgs;
  var prevState = state;
  // Save current active tab's render state
  if (prevTab && tabs[prevTab]) _saveRenderState(prevTab);
  // Load target tab's state
  _loadRenderState(tabId);
  msgs = tabs[tabId].msgsEl;
  state = tabs[tabId].state || 'ready';
  // Execute
  fn();
  // Save target tab's state back
  _saveRenderState(tabId);
  // Restore previous active tab's state
  if (prevTab && tabs[prevTab]) _loadRenderState(prevTab);
  msgs = prevMsgs;
  state = prevState;
}

// --- Initialize sidebar tab section ---
(function initTabSection() {
  sidebarTabList = document.getElementById('sidebar-tab-list');
  var addBtn = document.getElementById('sidebar-tab-add');
  if (addBtn) addBtn.addEventListener('click', createTab);
})();

// --- Completed executions badge ---
// Counts background tabs whose execution finished but haven't been viewed yet
// (i.e. tabs carrying the `done` class). Cleared when the user activates the tab.
function _updateTabBadge() {
  var count = 0;
  var ids = Object.keys(tabs);
  for (var i = 0; i < ids.length; i++) {
    var t = tabs[ids[i]];
    if (t && t.el && t.el.classList.contains('done')) count++;
  }
  var badge = document.getElementById('sidebar-toggle-badge');
  if (!badge) return;
  if (count > 0) {
    badge.textContent = String(count);
    badge.classList.add('visible');
  } else {
    badge.classList.remove('visible');
  }
}

// --- Tab management ---

function createTab() {
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.create_tab) return;

  // Reuse rule: only one live welcome allowed at a time. If any existing tab
  // is still promptless (welcome node present, no messages sent), switch to
  // it instead of spawning another empty tab. Prevents two welcome canvases
  // racing for the singleton WebGL refs in 06-welcome.js.
  var tabIds = Object.keys(tabs);
  for (var i = 0; i < tabIds.length; i++) {
    var t = tabs[tabIds[i]];
    if (t && t.msgsEl && t.msgsEl.querySelector('.welcome')) {
      switchTab(tabIds[i]);
      return;
    }
  }

  pywebview.api.create_tab().then(function(tabId) {
    if (!tabId) return; // limit reached, error pushed from backend
    _addTabToUI(tabId, 'New Chat');
    switchTab(tabId);
    // Mount the WebGL2 noise field behind the welcome. requestAnimationFrame
    // ensures the tab's msgsEl has laid out (display swap from switchTab has
    // taken effect) before we read its dimensions.
    var host = tabs[tabId] && tabs[tabId].msgsEl;
    if (host) {
      requestAnimationFrame(function() {
        if (typeof startWelcomeNoise === 'function' && host.querySelector('.welcome')) {
          startWelcomeNoise(host);
        }
      });
    }
  });
}

function _addTabToUI(tabId, title, sessionId) {
  // Create per-tab messages container
  var msgsContainer = document.createElement('div');
  msgsContainer.className = 'tab-messages';
  msgsContainer.id = 'messages-' + tabId;
  msgsContainer.style.display = 'none';
  // Insert into main-layout
  var mainLayout = document.querySelector('.main-layout');
  mainLayout.appendChild(msgsContainer);

  // Create sidebar tab item
  var tabEl = document.createElement('div');
  tabEl.className = 'sidebar-tab-item';
  tabEl.setAttribute('data-tab-id', tabId);
  tabEl.innerHTML = '<span class="sidebar-tab-item-indicator"></span>'
    + '<span class="sidebar-tab-item-title">' + _escTabHtml(title || 'New Chat') + '</span>'
    + '<button class="sidebar-tab-item-close" title="Close tab">&times;</button>';

  tabEl.querySelector('.sidebar-tab-item-title').addEventListener('click', function() {
    switchTab(tabId);
  });
  tabEl.querySelector('.sidebar-tab-item-close').addEventListener('click', function(e) {
    e.stopPropagation();
    closeTab(tabId);
  });

  sidebarTabList.appendChild(tabEl);

  tabs[tabId] = {
    el: tabEl,
    msgsEl: msgsContainer,
    title: title || 'New Chat',
    sessionId: sessionId || null,
    state: 'starting',
    unread: false,
    renderState: _newRenderState()
  };

  _updateTabBadge();
  _makeTabTitleEditable(tabEl, tabId);
  // Show welcome in new tab (skip if loading an existing session)
  if (!sessionId) _showWelcomeInTab(msgsContainer);
}

function switchTab(tabId) {
  if (!tabs[tabId]) return;
  if (activeTabId === tabId) return;

  // Save current tab's render state before switching
  if (activeTabId && tabs[activeTabId]) {
    _saveRenderState(activeTabId);
    tabs[activeTabId].msgsEl.style.display = 'none';
    tabs[activeTabId].el.classList.remove('active');
  }

  activeTabId = tabId;
  tabs[tabId].msgsEl.style.display = '';
  tabs[tabId].el.classList.remove('unread');
  tabs[tabId].el.classList.remove('done');
  tabs[tabId].el.classList.add('active');
  tabs[tabId].unread = false;
  _updateTabBadge();

  // Load the new tab's render state
  _loadRenderState(tabId);

  // Sync task panel DOM to the new active tab's task state
  _syncTaskPanelToActiveTab();

  // Swap the global `msgs` reference to point to this tab's container
  msgs = tabs[tabId].msgsEl;

  // Flush any pending stream buffer immediately so the user sees complete content
  // rather than watching a delayed drain animation catch up
  if (streamBuffer.length) {
    flushStream();
    // Save the flushed state back
    _saveRenderState(tabId);
  }

  // Update the global `state` variable to match this tab's state
  // This is critical — send() and cancel() check this global
  var tabState = tabs[tabId].state || 'ready';
  state = tabState;
  statusEl.textContent = tabState;
  statusEl.className = 'topbar-status ' + tabState;
  sendBtn.disabled = (tabState !== 'ready' && tabState !== 'prompting') || _loadingHistory;
  app.classList.toggle('prompting', tabState === 'prompting');

  // Tell backend
  if (window.pywebview && window.pywebview.api && window.pywebview.api.switch_tab) {
    pywebview.api.switch_tab(tabId);
  }

  // Update thinking bar for new tab
  if (tabState === 'prompting') {
    showThinking();
  } else {
    hideThinking();
  }

  // Update topbar title for this tab
  var titleEl = document.getElementById('session-title');
  if (titleEl) {
    titleEl.textContent = tabs[tabId].title || '';
    titleEl.classList.toggle('has-title', !!tabs[tabId].title);
  }

  // Scroll to bottom
  tabs[tabId].msgsEl.scrollTo({ top: tabs[tabId].msgsEl.scrollHeight });
}

function closeTab(tabId) {
  if (!tabs[tabId]) return;
  // Log who's calling us so we can catch phantom closes.
  try {
    var stack = (new Error('closeTab trace')).stack || '';
    if (window.pywebview && window.pywebview.api && window.pywebview.api.debug_log) {
      window.pywebview.api.debug_log(
        '[TAB-CLOSE] tabId=' + tabId +
        ' activeTabId=' + activeTabId +
        ' tabs=' + Object.keys(tabs).join(',') +
        ' stack=' + stack.split('\n').slice(0, 4).join(' | ')
      );
    }
  } catch (e) {}
  var tabIds = Object.keys(tabs);
  if (tabIds.length <= 1) return; // Don't close last tab

  // Tell backend to kill the process
  if (window.pywebview && window.pywebview.api && window.pywebview.api.close_tab) {
    pywebview.api.close_tab(tabId);
  }

  // Remove DOM
  tabs[tabId].el.remove();
  tabs[tabId].msgsEl.remove();
  delete tabs[tabId];

  // If this was the active tab, switch to another
  if (activeTabId === tabId) {
    activeTabId = null;
    var remaining = Object.keys(tabs);
    if (remaining.length) switchTab(remaining[remaining.length - 1]);
  }

  _updateTabBadge();
}

function _escTabHtml(str) {
  return (str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _showWelcomeInTab(container) {
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
  container.appendChild(w);
}

// --- Get open session IDs (for sidebar filtering) ---
function _getOpenSessionIds() {
  var ids = {};
  var tabIds = Object.keys(tabs);
  for (var i = 0; i < tabIds.length; i++) {
    var sid = tabs[tabIds[i]].sessionId;
    if (sid) ids[sid] = true;
  }
  return ids;
}

// Expose for 04-sidebar.js
window._getOpenSessionIds = _getOpenSessionIds;

// --- Route events by tabId ---
// Wrap the original handlers to dispatch to correct tab container

var _origAcpUpdate = window.__acpUpdate;
var _origAcpTurnEnd = window.__acpTurnEnd;
var _origAcpStateChange = window.__acpStateChange;
var _origAcpNewSession = window.__acpNewSession;
var _origAcpSessionTitle = window.__acpSessionTitle;
var _origAcpSessionLoaded = window.__acpSessionLoaded;
var _origAcpSkillActivation = window.__acpSkillActivation;
var _origAcpError = window.__acpError;

// Helper: check if event belongs to active tab
function _isActiveTab(data) {
  if (!data || !data._tabId) return true; // No tab routing = legacy single tab
  return data._tabId === activeTabId;
}

// Helper: mark a background tab as having unread content
function _markUnread(data) {
  if (!data || !data._tabId) return;
  if (data._tabId === activeTabId) return;
  var tab = tabs[data._tabId];
  if (tab && !tab.unread) {
    tab.unread = true;
    tab.el.classList.add('unread');
  }
}

// Helper: mark a background tab as "execution complete" (! indicator)
function _markDone(data) {
  if (!data || !data._tabId) return;
  if (data._tabId === activeTabId) return;
  var tab = tabs[data._tabId];
  if (tab) {
    tab.unread = true;
    tab.el.classList.remove('unread');
    tab.el.classList.add('done');
    _updateTabBadge();
  }
}

// Helper: show a transient toast notification
var _toastEl = null;
var _toastTimer = null;
function _showToast(message) {
  if (!_toastEl) {
    _toastEl = document.createElement('div');
    _toastEl.className = 'ha-toast';
    document.body.appendChild(_toastEl);
  }
  _toastEl.innerHTML = '<span class="ha-toast-accent">&#9670;</span> ' + _escTabHtml(message);
  // Reset animation
  _toastEl.classList.remove('visible');
  void _toastEl.offsetWidth;
  _toastEl.classList.add('visible');
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(function() {
    _toastEl.classList.remove('visible');
  }, 3000);
}

// Wrap __acpUpdate: route to correct tab's messages container
window.__acpUpdate = function(update) {
  var tabId = update && update._tabId;

  // Update tab indicator state — only if tab is actually prompting
  if (tabId && tabs[tabId] && tabs[tabId].state === 'prompting') {
    tabs[tabId].el.classList.add('prompting');
  }

  if (_isActiveTab(update)) {
    // Active tab: render normally (state is already loaded)
    if (_origAcpUpdate) _origAcpUpdate(update);
  } else if (tabId && tabs[tabId]) {
    // Background tab: render using context swap
    _markUnread(update);
    _withTabContext(tabId, function() {
      if (_origAcpUpdate) _origAcpUpdate(update);
    });
  }
};

window.__acpTurnEnd = function(data) {
  var tabId = data && data._tabId;

  // Clear prompting indicator
  if (tabId && tabs[tabId]) {
    tabs[tabId].el.classList.remove('prompting');
  }

  if (_isActiveTab(data)) {
    if (_origAcpTurnEnd) _origAcpTurnEnd(data);
  } else if (tabId && tabs[tabId]) {
    // Background tab execution complete — mark as done and show toast
    _markDone(data);
    _showToast('Execution complete: ' + (tabs[tabId].title || 'Tab'));
    _withTabContext(tabId, function() {
      if (_origAcpTurnEnd) _origAcpTurnEnd(data);
    });
  }
};

window.__acpStateChange = function(data) {
  var tabId = data && data._tabId;

  // Register initial tab on first push with _tabId
  if (tabId && !_initialTabRegistered) {
    _registerInitialTab(tabId);
  }

  // Update tab state
  if (tabId && tabs[tabId]) {
    tabs[tabId].state = data.state;
    // Update indicator classes
    tabs[tabId].el.classList.toggle('prompting', data.state === 'prompting');
    tabs[tabId].el.classList.toggle('crashed', data.state === 'crashed');
  }

  // Only update global UI (status bar, thinking indicator) for active tab
  if (!_isActiveTab(data)) return;
  if (_origAcpStateChange) _origAcpStateChange(data);
};

window.__acpNewSession = function(data) {
  if (_isActiveTab(data)) {
    if (_origAcpNewSession) _origAcpNewSession(data);
  } else {
    var tabId = data && data._tabId;
    if (tabId && tabs[tabId]) {
      _withTabContext(tabId, function() {
        if (_origAcpNewSession) _origAcpNewSession(data);
      });
    }
  }
};

// Handle backend telling us a tab's session id changed (created lazily, loaded, or cleared).
// Keeps tabs[tabId].sessionId in sync so the sidebar's "hide open sessions" filter works,
// and refreshes the session list so the row disappears immediately.
window.__acpSessionIdChanged = function(data) {
  var tabId = data && data._tabId;
  if (!tabId || !tabs[tabId]) return;
  tabs[tabId].sessionId = data && data.sessionId ? data.sessionId : null;
  if (typeof refreshSessions === 'function' && sidebar && sidebar.classList.contains('open')) {
    refreshSessions();
  }
};

window.__acpSessionTitle = function(data) {
  var tabId = data && data._tabId;
  // Update sidebar tab title
  if (tabId && tabs[tabId]) {
    var title = data.title || 'New Chat';
    tabs[tabId].title = title;
    var titleEl = tabs[tabId].el.querySelector('.sidebar-tab-item-title');
    if (titleEl) titleEl.textContent = title;
  }
  if (!_isActiveTab(data)) return;
  if (_origAcpSessionTitle) _origAcpSessionTitle(data);
};

window.__acpSessionLoaded = function(data) {
  var tabId = data && data._tabId;
  // Track session ID on the tab
  if (tabId && tabs[tabId] && data && data.sessionId) {
    tabs[tabId].sessionId = data.sessionId;
  }
  if (_isActiveTab(data)) {
    if (_origAcpSessionLoaded) _origAcpSessionLoaded(data);
    // Sync derived title to sidebar tab ONLY if tab has no real title yet
    if (tabId && tabs[tabId] && sessionTitle && tabs[tabId].title === 'New Chat') {
      tabs[tabId].title = sessionTitle;
      var titleEl = tabs[tabId].el.querySelector('.sidebar-tab-item-title');
      if (titleEl) titleEl.textContent = sessionTitle;
    }
  } else {
    if (tabId && tabs[tabId]) {
      _withTabContext(tabId, function() {
        if (_origAcpSessionLoaded) _origAcpSessionLoaded(data);
      });
      // Sync derived title only if tab has no real title yet
      var rs = tabs[tabId].renderState;
      if (rs && rs.sessionTitle && tabs[tabId].title === 'New Chat') {
        tabs[tabId].title = rs.sessionTitle;
        var bgTitleEl = tabs[tabId].el.querySelector('.sidebar-tab-item-title');
        if (bgTitleEl) bgTitleEl.textContent = rs.sessionTitle;
      }
    }
  }
};

window.__acpSkillActivation = function(data) {
  if (_isActiveTab(data)) {
    if (_origAcpSkillActivation) _origAcpSkillActivation(data);
  } else {
    var tabId = data && data._tabId;
    if (tabId && tabs[tabId]) {
      _withTabContext(tabId, function() {
        if (_origAcpSkillActivation) _origAcpSkillActivation(data);
      });
    }
  }
};

window.__acpError = function(data) {
  // Errors always show (not tab-scoped)
  if (_origAcpError) _origAcpError(data);
};

// --- Task panel: tab-routed wrappers ---
var _origAcpTaskUpdate = window.__acpTaskUpdate;
var _origAcpTaskReset = window.__acpTaskReset;

window.__acpTaskUpdate = function(data) {
  var tabId = data && data._tabId;
  if (_isActiveTab(data)) {
    if (_origAcpTaskUpdate) _origAcpTaskUpdate(data);
  } else if (tabId && tabs[tabId]) {
    _withTabContext(tabId, function() {
      if (_origAcpTaskUpdate) _origAcpTaskUpdate(data);
    });
  }
};

window.__acpTaskReset = function(data) {
  var tabId = data && data._tabId;
  if (_isActiveTab(data)) {
    if (_origAcpTaskReset) _origAcpTaskReset(data);
  } else if (tabId && tabs[tabId]) {
    _withTabContext(tabId, function() {
      if (_origAcpTaskReset) _origAcpTaskReset(data);
    });
  }
};

// --- Keyboard shortcuts ---
document.addEventListener('keydown', function(e) {
  // Ctrl+T: new tab
  if (e.ctrlKey && e.key === 't') {
    e.preventDefault();
    createTab();
  }
  // Ctrl+W: close current tab
  if (e.ctrlKey && e.key === 'w') {
    e.preventDefault();
    if (activeTabId && Object.keys(tabs).length > 1) closeTab(activeTabId);
  }
  // Ctrl+Tab / Ctrl+Shift+Tab: cycle tabs
  if (e.ctrlKey && e.key === 'Tab') {
    e.preventDefault();
    var tabIds = Object.keys(tabs);
    if (tabIds.length <= 1) return;
    var idx = tabIds.indexOf(activeTabId);
    if (e.shiftKey) {
      idx = (idx - 1 + tabIds.length) % tabIds.length;
    } else {
      idx = (idx + 1) % tabIds.length;
    }
    switchTab(tabIds[idx]);
  }
});

// --- Register initial tab ---
// The backend creates an initial tab on startup. We register it here when we
// get the first state change with a _tabId.
var _initialTabRegistered = false;

function _registerInitialTab(tabId) {
  if (_initialTabRegistered) return;
  _initialTabRegistered = true;

  // The original #messages div becomes this tab's container
  var originalMsgs = document.getElementById('messages');

  // Create sidebar tab item for initial tab
  var tabEl = document.createElement('div');
  tabEl.className = 'sidebar-tab-item active';
  tabEl.setAttribute('data-tab-id', tabId);
  tabEl.innerHTML = '<span class="sidebar-tab-item-indicator"></span>'
    + '<span class="sidebar-tab-item-title">New Chat</span>'
    + '<button class="sidebar-tab-item-close" title="Close tab">&times;</button>';

  tabEl.querySelector('.sidebar-tab-item-title').addEventListener('click', function() {
    switchTab(tabId);
  });
  tabEl.querySelector('.sidebar-tab-item-close').addEventListener('click', function(e) {
    e.stopPropagation();
    closeTab(tabId);
  });

  sidebarTabList.appendChild(tabEl);

  tabs[tabId] = {
    el: tabEl,
    msgsEl: originalMsgs,
    title: 'New Chat',
    sessionId: null,
    state: 'starting',
    unread: false,
    renderState: _newRenderState()
  };
  activeTabId = tabId;
  // msgs is already pointing to originalMsgs from 00-core.js init

  _updateTabBadge();
  _makeTabTitleEditable(tabEl, tabId);
}

// Expose for inline handlers and keyboard shortcuts
window.createTab = createTab;
window.closeTab = closeTab;
window.switchTab = switchTab;

// --- Tab title editing (double-click to rename) ---
function _makeTabTitleEditable(tabEl, tabId) {
  var titleEl = tabEl.querySelector('.sidebar-tab-item-title');
  if (!titleEl) return;
  titleEl.addEventListener('dblclick', function(e) {
    e.stopPropagation();
    var current = tabs[tabId] ? tabs[tabId].title : titleEl.textContent;
    var inp = document.createElement('input');
    inp.type = 'text';
    inp.className = 'sidebar-tab-rename-input';
    inp.value = current;
    titleEl.style.display = 'none';
    tabEl.insertBefore(inp, titleEl.nextSibling);
    inp.focus();
    inp.select();

    function commit() {
      var newTitle = inp.value.trim() || current;
      titleEl.textContent = newTitle;
      titleEl.style.display = '';
      if (inp.parentNode) inp.parentNode.removeChild(inp);
      if (tabs[tabId]) tabs[tabId].title = newTitle;
      // Persist via rename_session
      if (window.pywebview && window.pywebview.api && window.pywebview.api.rename_session) {
        pywebview.api.rename_session(null, newTitle);
      }
    }

    inp.addEventListener('keydown', function(ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
      if (ev.key === 'Escape') { ev.preventDefault(); titleEl.style.display = ''; if (inp.parentNode) inp.parentNode.removeChild(inp); }
    });
    inp.addEventListener('blur', commit);
  });
}
