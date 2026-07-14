/* ===== Hyperagent: Multi-Tab Architecture ===== */

// --- Tab state ---
var tabs = {};           // {tabId: {el, msgsEl, title, state, unread, renderState}}
var activeTabId = null;
var tabBarEl = null;

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
    activeSkills: {}
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
    activeSkills: activeSkills
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

// --- Initialize tab bar UI ---
(function initTabBar() {
  tabBarEl = document.getElementById('tab-bar');

  var addBtn = document.createElement('div');
  addBtn.className = 'tab-add';
  addBtn.textContent = '+';
  addBtn.title = 'New tab (Ctrl+T)';
  addBtn.addEventListener('click', createTab);
  tabBarEl.appendChild(addBtn);
})();

// --- Tab management ---

function createTab() {
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.create_tab) return;
  pywebview.api.create_tab().then(function(tabId) {
    if (!tabId) return; // limit reached, error pushed from backend
    _addTabToUI(tabId, 'New Chat');
    switchTab(tabId);
  });
}

function _addTabToUI(tabId, title) {
  // Create per-tab messages container
  var msgsContainer = document.createElement('div');
  msgsContainer.className = 'tab-messages';
  msgsContainer.id = 'messages-' + tabId;
  msgsContainer.style.display = 'none';
  // Insert after the existing #messages div in main-layout
  var mainLayout = document.querySelector('.main-layout');
  mainLayout.appendChild(msgsContainer);

  // Create tab element
  var tabEl = document.createElement('div');
  tabEl.className = 'tab-item';
  tabEl.setAttribute('data-tab-id', tabId);
  tabEl.innerHTML = '<span class="tab-title">' + _escTabHtml(title || 'New Chat') + '</span>'
    + '<span class="tab-indicator"></span>'
    + '<span class="tab-close">&times;</span>';

  tabEl.querySelector('.tab-title').addEventListener('click', function() {
    switchTab(tabId);
  });
  tabEl.querySelector('.tab-close').addEventListener('click', function(e) {
    e.stopPropagation();
    closeTab(tabId);
  });

  // Insert before the [+] button
  var addBtn = tabBarEl.querySelector('.tab-add');
  tabBarEl.insertBefore(tabEl, addBtn);

  tabs[tabId] = {
    el: tabEl,
    msgsEl: msgsContainer,
    title: title || 'New Chat',
    state: 'starting',
    unread: false,
    renderState: _newRenderState()
  };

  _updateTabBarVisibility();
  _makeTabTitleEditable(tabEl, tabId);
  // Show welcome in new tab
  _showWelcomeInTab(msgsContainer);
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
  tabs[tabId].el.classList.add('active');
  tabs[tabId].unread = false;

  // Load the new tab's render state
  _loadRenderState(tabId);

  // Swap the global `msgs` reference to point to this tab's container
  msgs = tabs[tabId].msgsEl;

  // Update the global `state` variable to match this tab's state
  // This is critical — send() and cancel() check this global
  var tabState = tabs[tabId].state || 'ready';
  state = tabState;
  statusEl.textContent = tabState;
  statusEl.className = 'topbar-status ' + tabState;
  sendBtn.disabled = (tabState !== 'ready' && tabState !== 'prompting');
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

  // Scroll to bottom
  tabs[tabId].msgsEl.scrollTo({ top: tabs[tabId].msgsEl.scrollHeight });
}

function closeTab(tabId) {
  if (!tabs[tabId]) return;
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

  _updateTabBarVisibility();
}

function _updateTabBarVisibility() {
  var count = Object.keys(tabs).length;
  tabBarEl.classList.toggle('single-tab', count <= 1);
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

// Wrap __acpUpdate: route to correct tab's messages container
window.__acpUpdate = function(update) {
  var tabId = update && update._tabId;

  // Update tab indicator state
  if (tabId && tabs[tabId]) {
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
    _markUnread(data);
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

window.__acpSessionTitle = function(data) {
  var tabId = data && data._tabId;
  // Update tab title in the tab bar
  if (tabId && tabs[tabId]) {
    var title = data.title || 'New Chat';
    tabs[tabId].title = title;
    var titleEl = tabs[tabId].el.querySelector('.tab-title');
    if (titleEl) titleEl.textContent = title;
  }
  if (!_isActiveTab(data)) return;
  if (_origAcpSessionTitle) _origAcpSessionTitle(data);
};

window.__acpSessionLoaded = function(data) {
  if (_isActiveTab(data)) {
    if (_origAcpSessionLoaded) _origAcpSessionLoaded(data);
  } else {
    var tabId = data && data._tabId;
    if (tabId && tabs[tabId]) {
      _withTabContext(tabId, function() {
        if (_origAcpSessionLoaded) _origAcpSessionLoaded(data);
      });
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

// --- Keyboard shortcut: Ctrl+T for new tab ---
document.addEventListener('keydown', function(e) {
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

  tabs[tabId] = {
    el: null,  // will create tab element
    msgsEl: originalMsgs,
    title: 'New Chat',
    state: 'starting',
    unread: false,
    renderState: _newRenderState()
  };
  activeTabId = tabId;
  // msgs is already pointing to originalMsgs from 00-core.js init

  // Create tab bar item for initial tab
  var tabEl = document.createElement('div');
  tabEl.className = 'tab-item active';
  tabEl.setAttribute('data-tab-id', tabId);
  tabEl.innerHTML = '<span class="tab-title">New Chat</span>'
    + '<span class="tab-indicator"></span>'
    + '<span class="tab-close">&times;</span>';

  tabEl.querySelector('.tab-title').addEventListener('click', function() {
    switchTab(tabId);
  });
  tabEl.querySelector('.tab-close').addEventListener('click', function(e) {
    e.stopPropagation();
    closeTab(tabId);
  });

  var addBtn = tabBarEl.querySelector('.tab-add');
  tabBarEl.insertBefore(tabEl, addBtn);
  tabs[tabId].el = tabEl;

  _updateTabBarVisibility();
  _makeTabTitleEditable(tabEl, tabId);
}

// Expose for inline handlers and keyboard shortcuts
window.createTab = createTab;
window.closeTab = closeTab;
window.switchTab = switchTab;

// --- Tab title editing (double-click to rename) ---
function _makeTabTitleEditable(tabEl, tabId) {
  var titleEl = tabEl.querySelector('.tab-title');
  if (!titleEl) return;
  titleEl.addEventListener('dblclick', function(e) {
    e.stopPropagation();
    var current = tabs[tabId] ? tabs[tabId].title : titleEl.textContent;
    var input = document.createElement('input');
    input.type = 'text';
    input.className = 'tab-rename-input';
    input.value = current;
    titleEl.style.display = 'none';
    tabEl.insertBefore(input, titleEl.nextSibling);
    input.focus();
    input.select();

    function commit() {
      var newTitle = input.value.trim() || current;
      titleEl.textContent = newTitle;
      titleEl.style.display = '';
      if (input.parentNode) input.parentNode.removeChild(input);
      if (tabs[tabId]) tabs[tabId].title = newTitle;
      // Persist via rename_session if we have a session
      if (window.pywebview && window.pywebview.api && window.pywebview.api.rename_session) {
        var client = tabs[tabId];
        // The backend tracks session<->tab, rename propagates to preferences
        pywebview.api.rename_session(null, newTitle);
      }
      // Save tab state
      if (window.pywebview && window.pywebview.api && window.pywebview.api.switch_tab) {
        // Trigger a save by switching to self (no-op on backend but saves state)
      }
    }

    input.addEventListener('keydown', function(ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
      if (ev.key === 'Escape') { ev.preventDefault(); titleEl.style.display = ''; if (input.parentNode) input.parentNode.removeChild(input); }
    });
    input.addEventListener('blur', commit);
  });
}

// --- Handle restored tabs from backend ---
window.__acpTabRestored = function(data) {
  if (!data || !data.tabId) return;
  var tabId = data.tabId;
  var title = data.title || 'New Chat';
  var isActive = data.active;

  if (!_initialTabRegistered) {
    // First restored tab replaces the default #messages
    _registerInitialTab(tabId);
    tabs[tabId].title = title;
    var titleEl = tabs[tabId].el.querySelector('.tab-title');
    if (titleEl) titleEl.textContent = title;
  } else if (!tabs[tabId]) {
    // Additional restored tabs
    _addTabToUI(tabId, title);
    if (isActive) switchTab(tabId);
  }
};
