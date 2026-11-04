// Workflow detail: a full-page overlay opened from a workflow card's expand control. It re-renders from the
// card's latest payload on every __acpWorkflowTask, so it follows the run: budget meters, phases with their
// agent rows, and the result.
var workflowDetailId = null;
var workflowDetailEl = null;
var WORKFLOW_RESULT_MAX_CHARS = 300000;
var WORKFLOW_METER_CELLS = 20;
var WORKFLOW_METER_WARN = 0.8;
var workflowDetailOpen = {};

// Optional inFlight lights extra cells past `used` in the dim accent (agents running but not finished).
function workflowMeter(label, used, total, valueText, inFlight, cells) {
  var cellCount = cells > 0 ? cells : WORKFLOW_METER_CELLS;
  var box = workflowEl('div', 'workflow-detail-meter');
  var head = workflowEl('div', 'workflow-detail-meter-head');
  head.appendChild(workflowEl('span', 'workflow-detail-meter-label', label));
  head.appendChild(workflowEl('span', 'workflow-detail-meter-value', valueText));
  box.appendChild(head);
  var ratio = total > 0 && typeof used === 'number' ? Math.min(used / total, 1) : 0;
  var flightRatio = total > 0 && inFlight > 0 ? Math.min(((used || 0) + inFlight) / total, 1) : ratio;
  var bar = workflowEl('div', 'viz-cell-bar');
  if (total > 0 && ratio >= WORKFLOW_METER_WARN) box.setAttribute('data-warn', 'true');
  var lit = Math.round(ratio * cellCount);
  var flight = Math.round(flightRatio * cellCount);
  for (var i = 0; i < cellCount; i++) {
    var cell = workflowEl('span', 'viz-cell' + (i < flight ? ' viz-cell-filled' : ''));
    if (i < lit) cell.style.background = 'var(--magnitude)';
    else if (i < flight) cell.style.background = 'var(--accent-dim)';
    bar.appendChild(cell);
  }
  box.appendChild(bar);
  return box;
}

function workflowStat(label, value) {
  var box = workflowEl('div', 'workflow-detail-stat');
  box.appendChild(workflowEl('span', 'workflow-detail-meter-label', label));
  box.appendChild(workflowEl('span', 'workflow-detail-stat-value', value));
  return box;
}

// Phases in script order (titles from the run), then any phase only seen on an agent row.
function workflowPhaseGroups(data) {
  var groups = [];
  var byTitle = {};
  function group(title) {
    var key = title || '';
    if (!byTitle[key]) {
      byTitle[key] = { title: key, agents: [] };
      groups.push(byTitle[key]);
    }
    return byTitle[key];
  }
  (data.phases || []).forEach(function(title) { group(title); });
  (data.agents || []).forEach(function(agent) { group(agent.phase).agents.push(agent); });
  return groups;
}

// done: all agents finished; running: something is in flight (or the run is live between agents);
// stopped: the run ended with agents unfinished; pending: no agents yet.
function workflowPhaseState(group, active) {
  if (!group.agents.length) return 'pending';
  var done = group.agents.filter(function(a) { return a.state === 'done'; }).length;
  if (done === group.agents.length) return 'done';
  return active ? 'running' : 'stopped';
}

// Open (or closed) state of a fold: the user's last choice, else the default for that state.
function workflowFoldOpen(key, fallback) {
  return key in workflowDetailOpen ? workflowDetailOpen[key] : fallback;
}

function workflowDetailPhase(group, active, index) {
  var state = workflowPhaseState(group, active);
  var section = document.createElement('details');
  section.className = 'workflow-detail-phase';
  section.setAttribute('data-state', state);
  var key = 'phase:' + group.title;
  section.open = workflowFoldOpen(key, !(state === 'done' && active) && state !== 'pending');
  section.addEventListener('toggle', function() { workflowDetailOpen[key] = section.open; });
  var done = group.agents.filter(function(a) { return a.state === 'done'; }).length;
  var tokens = group.agents.reduce(function(sum, a) { return sum + (typeof a.tokens === 'number' ? a.tokens : 0); }, 0);
  var span = group.agents.reduce(function(max, a) { return typeof a.durationMs === 'number' && a.durationMs > max ? a.durationMs : max; }, 0);
  var head = workflowEl('summary', 'workflow-detail-phase-head');
  head.appendChild(workflowEl('span', 'workflow-detail-phase-index', String(index + 1)));
  head.appendChild(workflowEl('span', 'workflow-detail-phase-title', group.title || 'No phase'));
  head.appendChild(workflowEl('span', 'workflow-detail-phase-state', state));
  head.appendChild(workflowEl('span', 'workflow-detail-phase-meta',
    group.agents.length
      ? done + '/' + group.agents.length + ' agents  ' + workflowTokens(tokens) + ' tokens' + (span ? '  ' + workflowSeconds(span) : '')
      : 'not started'));
  section.appendChild(head);
  group.agents.forEach(function(agent) { section.appendChild(workflowAgentRow(agent, active)); });
  return section;
}

// One agent: state glyph, label, what it is doing now (or how it ended), tokens, time. Live agents expand.
function workflowAgentRow(agent, active) {
  var live = typeof agent.turns === 'number';
  var state = agent.state === 'done' ? 'done' : (active ? 'running' : 'idle');
  var block = workflowEl(live ? 'details' : 'div', 'workflow-detail-agent-block');
  block.setAttribute('data-state', state);
  if (live) {
    var key = 'agent:' + agent.id;
    block.open = workflowFoldOpen(key, state === 'running');
    block.addEventListener('toggle', function() { workflowDetailOpen[key] = block.open; });
  }
  var row = workflowEl(live ? 'summary' : 'div', 'workflow-detail-agent workflow-detail-agent-wide');
  row.setAttribute('data-state', state);
  row.appendChild(workflowStateGlyph());
  row.appendChild(workflowEl('span', 'workflow-detail-agent-label', agent.label || 'agent'));
  var doing = state === 'done' ? (agent.result || '') :
    (agent.lastTool ? agent.lastTool + ' ' + (agent.lastToolSummary || '') : (state === 'running' ? 'starting' : ''));
  row.appendChild(workflowEl('span', 'workflow-detail-agent-doing', doing));
  row.appendChild(workflowEl('span', 'workflow-detail-agent-num', workflowTokens(agent.tokens)));
  row.appendChild(workflowEl('span', 'workflow-detail-agent-num', workflowSeconds(agent.durationMs)));
  block.appendChild(row);
  if (live) block.appendChild(workflowAgentDetail(agent));
  return block;
}

function workflowFold(key, label, text) {
  var fold = document.createElement('details');
  fold.className = 'workflow-detail-fold';
  fold.open = !!workflowDetailOpen[key];
  fold.addEventListener('toggle', function() { workflowDetailOpen[key] = fold.open; });
  fold.appendChild(workflowEl('summary', 'workflow-detail-fold-label', label));
  fold.appendChild(workflowEl('pre', 'workflow-detail-fold-text', text));
  return fold;
}

// Stats line, narration, last tool call with its input, per-tool counts and time, flags, then folds for prompt and result.
function workflowAgentDetail(agent) {
  var detail = workflowEl('div', 'workflow-detail-agent-detail');
  var stats = workflowEl('div', 'workflow-detail-agent-counts', [
    (agent.model || '-').replace(/^claude-/, '') + (agent.effort ? ' ' + agent.effort : ''),
    'turns ' + agent.turns,
    'tools ' + agent.toolCalls,
    'errors ' + (agent.errors || 0),
    'cache ' + (typeof agent.cacheHit === 'number' ? Math.round(agent.cacheHit * 100) + '%' : '-'),
    'output ' + workflowTokens(agent.outTokens)
  ].join('   '));
  if (agent.errors) stats.setAttribute('data-warn', 'true');
  detail.appendChild(stats);
  if (agent.narration) detail.appendChild(workflowEl('div', 'workflow-detail-agent-narration', agent.narration));
  if (agent.lastTool) {
    var last = workflowEl('div', 'workflow-detail-agent-last');
    last.appendChild(workflowEl('span', 'workflow-detail-agent-tool', agent.lastTool));
    last.appendChild(workflowEl('span', 'workflow-detail-agent-summary', agent.lastToolSummary || ''));
    detail.appendChild(last);
  }
  var tools = agent.tools || {};
  var ms = agent.toolMs || {};
  var counts = Object.keys(tools).sort(function(a, b) { return tools[b] - tools[a]; });
  if (counts.length) {
    detail.appendChild(workflowEl('div', 'workflow-detail-agent-counts', counts.map(function(name) {
      return name + ' ' + tools[name] + (ms[name] ? ' (' + workflowSeconds(ms[name]) + ')' : '');
    }).join('   ')));
  }
  var flags = [];
  if (agent.thinkingTurns) flags.push('thinking ' + agent.thinkingTurns + ' turns');
  if (agent.blocked) flags.push(agent.blocked + ' blocked');
  if (agent.stopReason) flags.push('stop ' + agent.stopReason);
  if (flags.length) {
    var line = workflowEl('div', 'workflow-detail-agent-counts', flags.join('   '));
    if (agent.blocked) line.setAttribute('data-warn', 'true');
    detail.appendChild(line);
  }
  if (agent.prompt) detail.appendChild(workflowFold('prompt:' + agent.id, 'prompt', agent.prompt));
  if (agent.result) detail.appendChild(workflowFold('result:' + agent.id, 'result', agent.result));
  return detail;
}

// Live readout while the run has no agent rows: each label seen so far with the tokens spent while it was active.
function workflowActivity(rows, active) {
  var section = workflowEl('div', 'workflow-detail-phase');
  var head = workflowEl('div', 'workflow-detail-phase-head');
  head.appendChild(workflowEl('span', 'workflow-detail-phase-title', 'Activity'));
  head.appendChild(workflowEl('span', 'workflow-detail-phase-meta', rows.length + ' seen'));
  section.appendChild(head);
  rows.forEach(function(r) {
    var row = workflowEl('div', 'workflow-detail-agent');
    row.setAttribute('data-state', active && r.current ? 'running' : 'done');
    row.appendChild(workflowStateGlyph());
    row.appendChild(workflowEl('span', 'workflow-detail-agent-label', r.label));
    row.appendChild(workflowEl('span', 'workflow-detail-agent-num', workflowTokens(r.tokens)));
    row.appendChild(workflowEl('span', 'workflow-detail-agent-num', ''));
    section.appendChild(row);
  });
  return section;
}

function renderWorkflowDetail(data) {
  var panel = workflowDetailEl.querySelector('.workflow-detail-panel');
  var oldBody = panel.querySelector('.workflow-detail-body');
  var scroll = oldBody ? oldBody.scrollTop : 0;
  panel.textContent = '';
  var active = workflowIsActive(data.state);
  var limits = data.limits || {};
  var state = data.stopping && active ? 'stopping' : data.state;
  panel.setAttribute('data-state', state);

  var header = workflowEl('div', 'workflow-detail-header');
  var kicker = workflowEl('div', 'workflow-detail-kicker');
  kicker.appendChild(workflowEl('span', '', 'Workflow'));
  kicker.appendChild(workflowEl('span', 'workflow-detail-state', state));
  var close = workflowEl('button', 'workflow-detail-close', '×');
  close.type = 'button';
  close.title = 'Close (Esc)';
  close.setAttribute('data-testid', 'workflow-detail-close');
  close.addEventListener('click', closeWorkflowDetail);
  kicker.appendChild(close);
  header.appendChild(kicker);
  header.appendChild(workflowEl('div', 'workflow-detail-title', data.name || 'workflow'));
  if (data.description) header.appendChild(workflowEl('div', 'workflow-detail-desc', data.description));
  panel.appendChild(header);

  var body = workflowEl('div', 'workflow-detail-body');
  var agents = data.agents || [];
  var done = agents.filter(function(a) { return a.state === 'done'; }).length;
  var count = typeof data.agentCount === 'number' ? data.agentCount : (agents.length || (data.activity || []).length);
  var running = active ? Math.max(count - done, 0) : 0;
  var meters = workflowEl('div', 'workflow-detail-meters');
  meters.appendChild(workflowMeter('Tokens', data.tokens, limits.budget,
    workflowTokens(data.tokens) + (limits.budget ? ' / ' + workflowTokens(limits.budget) : '')));
  meters.appendChild(workflowMeter('Agents', done, limits.agentCap || count,
    (running ? running + ' running  ' : '') + done + '/' + count + ' done' +
    (limits.agentCap ? ' (cap ' + limits.agentCap + ')' : ''), running, limits.agentCap || count));
  meters.appendChild(workflowStat('Elapsed', workflowSeconds(data.durationMs)));
  meters.appendChild(workflowStat('Tool uses', typeof data.toolUses === 'number' ? String(data.toolUses) : '-'));
  body.appendChild(meters);

  var groups = workflowPhaseGroups(data);
  var activity = data.activity || [];
  if (agents.length || (groups.length && !activity.length)) {
    var phases = workflowEl('div', 'workflow-detail-phases');
    groups.forEach(function(g, i) { phases.appendChild(workflowDetailPhase(g, active, i)); });
    body.appendChild(phases);
  } else if (activity.length) {
    body.appendChild(workflowActivity(activity, active));
  } else if (data.lastTool && active) {
    body.appendChild(workflowEl('div', 'workflow-detail-note', 'last tool: ' + data.lastTool));
  }

  if (data.script && data.script.text) {
    var script = workflowEl('div', 'workflow-detail-section');
    script.appendChild(workflowFold('script', 'Script  ' + (data.script.name || ''), data.script.text));
    body.appendChild(script);
  }

  if (!active && data.result) {
    var result = workflowEl('div', 'workflow-detail-section');
    result.appendChild(workflowEl('div', 'workflow-detail-section-label', 'Result'));
    result.appendChild(workflowEl('pre', 'workflow-detail-result', data.result));
    if (data.result.length >= WORKFLOW_RESULT_MAX_CHARS) {
      result.appendChild(workflowEl('div', 'workflow-detail-note', 'Result cut at ' + WORKFLOW_RESULT_MAX_CHARS + ' characters.'));
    }
    body.appendChild(result);
  }
  panel.appendChild(body);

  if (active && data.canStop) {
    var footer = workflowEl('div', 'workflow-detail-actions');
    var stop = workflowStopButton(data);
    footer.appendChild(stop.firstChild);
    panel.appendChild(footer);
  }
  body.scrollTop = scroll;
}

function workflowDetailKey(e) {
  if (e.key === 'Escape') closeWorkflowDetail();
}

function openWorkflowDetail(id) {
  var entry = workflowCards[id];
  if (!entry || !entry.data) return;
  closeWorkflowDetail();
  workflowDetailId = id;
  workflowDetailOpen = {};
  var overlay = workflowEl('div', 'modal-overlay workflow-detail-overlay');
  overlay.setAttribute('data-testid', 'workflow-detail');
  overlay.appendChild(workflowEl('div', 'modal-panel workflow-detail-panel'));
  overlay.addEventListener('mousedown', function(e) { if (e.target === overlay) closeWorkflowDetail(); });
  document.body.appendChild(overlay);
  workflowDetailEl = overlay;
  renderWorkflowDetail(entry.data);
  document.addEventListener('keydown', workflowDetailKey);
  requestAnimationFrame(function() { overlay.classList.add('visible'); });
}

function closeWorkflowDetail() {
  document.removeEventListener('keydown', workflowDetailKey);
  if (workflowDetailEl) workflowDetailEl.remove();
  workflowDetailEl = null;
  workflowDetailId = null;
}

// Called by the card whenever its payload changes.
function refreshWorkflowDetail(id, data) {
  if (workflowDetailId === id && workflowDetailEl) renderWorkflowDetail(data);
}
