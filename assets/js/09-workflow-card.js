// Workflow card: one card per background workflow, anchored at the bottom of the sidebar, fed by __acpWorkflowTask. It shows the
// run's state, spend against its budget, phases and agent rows, a stop control, and the result.
var workflowCards = {};
var WORKFLOW_CARD_RESULT_CHARS = 1500;
var WORKFLOW_ACTIVE_STATES =['running', 'pending', 'started'];

function workflowIsActive(state) {
  return WORKFLOW_ACTIVE_STATES.indexOf(state) > -1;
}

function workflowTokens(n) {
  if (typeof n !== 'number') return '-';
  return n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n);
}

function workflowSeconds(ms) {
  return typeof ms === 'number' ? (ms / 1000).toFixed(1) + 's' : '-';
}

function workflowEl(tag, className, text) {
  var el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined && text !== null) el.textContent = text;
  return el;
}

function workflowCardShell(data) {
  var card = workflowEl('div', 'workflow-card');
  card.setAttribute('data-testid', 'workflow-card');
  var header = workflowEl('div', 'panel-header workflow-card-header');
  header.appendChild(workflowEl('span', 'workflow-card-name', ''));
  header.appendChild(workflowEl('span', 'workflow-card-state', ''));
  var expand = workflowEl('button', 'workflow-card-close', '↗');
  expand.type = 'button';
  expand.title = 'Open full view';
  expand.setAttribute('data-testid', 'workflow-card-expand');
  expand.addEventListener('click', function(e) {
    e.stopPropagation();
    openWorkflowDetail(data.id);
  });
  header.appendChild(expand);
  var close = workflowEl('button', 'workflow-card-close', '×');
  close.type = 'button';
  close.title = 'Dismiss (the workflow keeps running)';
  close.addEventListener('click', function(e) {
    e.stopPropagation();
    if (workflowDetailId === data.id) closeWorkflowDetail();
    card.remove();
    delete workflowCards[data.id];
  });
  header.appendChild(close);
  header.addEventListener('click', function() { card.classList.toggle('collapsed'); });
  card.appendChild(header);
  card.appendChild(workflowEl('div', 'workflow-card-body'));
  document.getElementById('sidebar-workflows').appendChild(card);
  return card;
}

function workflowFacts(data) {
  var limits = data.limits || {};
  var facts = workflowEl('div', 'workflow-card-facts');
  var agents = data.agents || [];
  var done = agents.filter(function(a) { return a.state === 'done'; }).length;
  var count = typeof data.agentCount === 'number' ? data.agentCount : agents.length;
  var running = workflowIsActive(data.state) ? Math.max(count - done, 0) : 0;
  var parts = [];
  if (running) parts.push(running + ' running');
  if (count || agents.length) parts.push(done + '/' + count + ' done' + (limits.agentCap ? ' (cap ' + limits.agentCap + ')' : ''));
  parts.push(workflowTokens(data.tokens) + (limits.budget ? ' / ' + workflowTokens(limits.budget) + ' tokens' : ' tokens'));
  parts.push(workflowSeconds(data.durationMs));
  parts.forEach(function(text) { facts.appendChild(workflowEl('span', 'status-chip status-chip-outlined-muted', text)); });
  return facts;
}

// The 3x3 dot-matrix state glyph; the parent row's data-state (running / done / idle) picks the lit pattern.
function workflowStateGlyph() {
  var glyph = workflowEl('span', 'sidebar-tab-dither');
  for (var i = 0; i < 9; i++) glyph.appendChild(document.createElement('span'));
  return glyph;
}

function workflowRows(data) {
  var list = workflowEl('div', 'workflow-card-agents');
  var agents = data.agents || [];
  var active = workflowIsActive(data.state);
  var phase = null;
  agents.forEach(function(agent) {
    if (agent.phase && agent.phase !== phase) {
      phase = agent.phase;
      list.appendChild(workflowEl('div', 'workflow-card-phase', phase));
    }
    var row = workflowEl('div', 'workflow-card-agent');
    row.setAttribute('data-state', agent.state === 'done' ? 'done' : (active ? 'running' : 'idle'));
    row.appendChild(workflowStateGlyph());
    row.appendChild(workflowEl('span', 'workflow-card-agent-label', agent.label || 'agent'));
    row.appendChild(workflowEl('span', 'workflow-card-agent-tokens', workflowTokens(agent.tokens)));
    list.appendChild(row);
  });
  if (!agents.length && data.lastTool && workflowIsActive(data.state)) {
    list.appendChild(workflowEl('div', 'workflow-card-phase', 'last: ' + data.lastTool));
  }
  return list;
}

function workflowStopButton(data) {
  var btn = workflowEl('button', 'action-button action-button-danger', data.stopping ? 'Stopping...' : 'Stop');
  btn.type = 'button';
  btn.disabled = !!data.stopping;
  btn.setAttribute('data-testid', 'workflow-card-stop');
  btn.addEventListener('click', function() {
    var a = window.pywebview && pywebview.api;
    if (a && a.stop_workflow) a.stop_workflow(data.id, data._tabId || null);
  });
  var wrap = workflowEl('div', 'workflow-card-actions');
  wrap.appendChild(btn);
  return wrap;
}

function renderWorkflowCard(card, data) {
  var active = workflowIsActive(data.state);
  card.setAttribute('data-state', data.stopping && active ? 'stopping' : data.state);
  card.querySelector('.workflow-card-name').textContent = data.name || 'workflow';
  card.querySelector('.workflow-card-state').textContent = data.stopping && active ? 'stopping' : data.state;
  var body = card.querySelector('.workflow-card-body');
  body.textContent = '';
  if (data.description) body.appendChild(workflowEl('div', 'workflow-card-desc', data.description));
  body.appendChild(workflowFacts(data));
  body.appendChild(workflowRows(data));
  if (active && data.canStop) body.appendChild(workflowStopButton(data));
  if (!active && data.result) {
    var preview = data.result.length > WORKFLOW_CARD_RESULT_CHARS
      ? data.result.slice(0, WORKFLOW_CARD_RESULT_CHARS) + '\n... (open the full view for the rest)' : data.result;
    body.appendChild(workflowEl('pre', 'workflow-card-result', preview));
  }
}

// Show only the cards that belong to the active tab; drop cards whose tab is gone.
window._syncWorkflowCards = function() {
  Object.keys(workflowCards).forEach(function(id) {
    var entry = workflowCards[id];
    if (entry.tabId && typeof tabs !== 'undefined' && !tabs[entry.tabId]) {
      if (workflowDetailId === id) closeWorkflowDetail();
      entry.el.remove();
      delete workflowCards[id];
      return;
    }
    entry.el.hidden = !!entry.tabId && entry.tabId !== activeTabId;
    if (entry.el.hidden && workflowDetailId === id) closeWorkflowDetail();
  });
};

// Toast for a launch, naming the tab when it is not the one in view.
function announceWorkflowLaunch(data) {
  if (!window.HvToast) return;
  var limits = data.limits || {};
  var tab = data._tabId && typeof tabs !== 'undefined' && tabs[data._tabId] && data._tabId !== activeTabId
    ? (tabs[data._tabId].title || 'Tab') + ': ' : '';
  window.HvToast.show({
    variant: 'info',
    title: tab + 'Workflow started: ' + (data.name || 'workflow'),
    message: 'cap ' + limits.agentCap + ' agents, budget +' + Math.round((limits.budget || 0) / 1000) + 'k tokens'
  });
  if (typeof _markUnread === 'function') _markUnread(data);
}

window.__acpWorkflowTask = function(data) {
  if (!data || !data.id) return;
  var entry = workflowCards[data.id];
  if (!entry) {
    entry = workflowCards[data.id] = { el: workflowCardShell(data), tabId: data._tabId || null };
    if (workflowIsActive(data.state)) {
      announceWorkflowLaunch(data);
      if (typeof sidebar !== 'undefined' && !sidebar.classList.contains('open')) toggleSidebar();
    }
  }
  entry.data = data;
  renderWorkflowCard(entry.el, data);
  refreshWorkflowDetail(data.id, data);
  window._syncWorkflowCards();
};
