/* ===== Hyperagent: Tool Cards + Tool Helpers ===== */

// --- Claude Code tool identity ---
// Claude Code's built-in tool names don't contain the keywords toolIcon /
// toolGroup look for, so map them onto those keywords first.
var CLAUDE_TOOL_ALIASES = {
  bash: 'shell', powershell: 'shell', agent: 'subagent', task: 'subagent',
  todowrite: 'todo_list', taskcreate: 'todo_list', taskupdate: 'todo_list', tasklist: 'todo_list',
  taskoutput: 'todo_list'
};

// Exact-name glyph and color group for Claude Code built-ins (lowercased).
// Checked before the keyword fallback in toolIcon / toolGroup, which still
// serves kiro and MCP tool names.
var CLAUDE_TOOL_TABLE = {
  read: ['<', 'read'], write: ['>', 'write'], edit: ['>', 'write'], notebookedit: ['>', 'write'],
  shell: ['$', 'shell'], glob: ['/', 'read'], grep: ['?', 'read'], toolsearch: ['?', 'read'],
  webfetch: ['~', 'web'], websearch: ['~', 'web'],
  subagent: ['※', 'subagent'], sendmessage: [')', 'subagent'],
  todo_list: ['☰', 'tasks'], taskget: ['☰', 'tasks'], taskstop: ['☰', 'tasks'],
  askuserquestion: ['"', 'tasks'], enterplanmode: ['|', 'tasks'], exitplanmode: ['|', 'tasks'],
  monitor: ['.', 'tasks'], croncreate: ['(', 'tasks'], crondelete: ['(', 'tasks'], cronlist: ['(', 'tasks'],
  pushnotification: [')', 'tasks'], remotetrigger: ['}', 'tasks'],
  skill: [':', 'knowledge'], listmcpresourcestool: ['%', 'knowledge'],
  readmcpresourcetool: ['%', 'knowledge'], readmcpresourcedirtool: ['%', 'knowledge'],
  enterworktree: ['[', 'devops'], exitworktree: ['[', 'devops']
};

function claudeToolName(update) {
  var meta = update && update._meta && update._meta.claudeCode;
  return (meta && meta.toolName) || '';
}

function parentToolUseId(update) {
  var meta = update && update._meta && update._meta.claudeCode;
  return (meta && meta.parentToolUseId) || null;
}

// Name used to pick a card's icon and color group.
function toolKindName(update) {
  var claude = claudeToolName(update);
  if (claude) return CLAUDE_TOOL_ALIASES[claude.toLowerCase()] || claude;
  return toolNameMap[update.toolCallId] || update.title || update.toolCallId;
}

function hasKeys(obj) {
  return !!obj && typeof obj === 'object' && Object.keys(obj).length > 0;
}

// Set a card's icon, label, tooltip, and color group, keeping its status classes.
function setToolCardIdentity(card, label, kindName) {
  var iconEl = card.querySelector('.tool-indicator-icon');
  var labelEl = card.querySelector('.tool-indicator-label');
  if (iconEl) iconEl.textContent = toolIcon(kindName);
  if (labelEl) labelEl.textContent = label;
  card.title = label;
  card.className = card.className.replace(/\btc-\w+/g, '').trim() + ' tc-' + toolGroup(kindName);
  if (card._toolData) card._toolData.name = label;
}

// Build a tool card (icon + label) with a click-to-expand detail panel.
function buildToolCard(id, label, kindName, statusClass, input, output) {
  var card = document.createElement('div');
  card.className = 'tool-indicator ' + statusClass;
  if (id) card.id = 'tool-' + id;
  card.innerHTML = '<span class="tool-indicator-icon"></span><span class="tool-indicator-label"></span>';
  card._toolData = { name: label, input: input || null, output: output || null };
  setToolCardIdentity(card, label, kindName);
  card.addEventListener('click', function() {
    var existing = card._detailEl;
    if (existing) {
      existing.classList.toggle('visible');
      card.classList.toggle('show-label');
      return;
    }
    card.classList.toggle('show-label');
    var d = card._toolData;
    if (!d || !(d.input || d.output)) return;
    var det = document.createElement('div');
    det.className = 'tool-detail visible';
    var content = '';
    if (d.input) content += '<div class="tool-detail-header">Input</div><div class="tool-detail-body">' + truncateDetail(d.input) + '</div>';
    if (d.output) content += '<div class="tool-detail-header">Output</div><div class="tool-detail-body">' + truncateDetail(d.output) + '</div>';
    det.innerHTML = content;
    var row = card.closest('.tool-row');
    if (row && row.parentNode) row.parentNode.insertBefore(det, row.nextSibling);
    card._detailEl = det;
  });
  return card;
}

// Turn-end line (done / cancelled / failed). `live` adds the failed-turn
// Continue button; replayed history passes false.
function buildTurnEndEl(data, live) {
  var m = data._metadata || {};
  var div = document.createElement('div');
  if (data._failed) {
    div.className = 'turn-end turn-failed';
    var ftext = document.createElement('span');
    ftext.textContent = ' - failed: ' + (data._failedMessage || 'Unknown error');
    div.appendChild(ftext);
    if (live && window._lastUserPrompt) {
      var retryBtn = document.createElement('button');
      retryBtn.type = 'button';
      retryBtn.className = 'retry-btn';
      retryBtn.textContent = 'Continue';
      retryBtn.addEventListener('click', function() {
        if (state === 'ready') {
          var continuePrompt = 'You were interrupted, please continue.';
          appendUser(continuePrompt);
          window._lastUserPrompt = continuePrompt;
          pywebview.api.send_prompt(continuePrompt);
        }
      });
      div.appendChild(retryBtn);
    }
    return div;
  }
  var parts = [];
  if (data._elapsed) parts.push(data._elapsed + 's');
  if (m.contextUsagePercentage != null) parts.push(Math.round(m.contextUsagePercentage) + '% ctx');
  if (!data._cancelled) {
    if (m.usage) parts.push(m.usage);
    if (m.cost) parts.push(m.cost);
  }
  if (m.meteringUsage && m.meteringUsage.length) {
    var total = 0;
    for (var i = 0; i < m.meteringUsage.length; i++) total += m.meteringUsage[i].value;
    parts.push(total.toFixed(2) + ' credits');
  } else if (m.creditsUsed) { parts.push(m.creditsUsed + ' credits'); }
  if (!data._cancelled && (m.inputTokens || m.outputTokens)) {
    parts.push((m.inputTokens || 0) + ' tok in');
    parts.push((m.outputTokens || 0) + ' tok out');
  }
  div.className = 'turn-end' + (data._cancelled ? ' cancelled' : '');
  div.textContent = (data._cancelled ? ' - cancelled' : ' - done')
    + (parts.length ? ' · ' + parts.join(' · ') : '');
  return div;
}

// Readable output from a tool_call_update (Claude: rawOutput; kiro: output/result).
function toolOutputOf(update) {
  var raw = update.rawOutput;
  if (Array.isArray(raw)) {
    var texts = raw.filter(function(p) { return p && p.type === 'text'; }).map(function(p) { return p.text; });
    if (texts.length) return texts.join('\n');
  }
  return raw || update.output || update.result || null;
}

// --- Tool icon mapping ---
function toolIcon(name) {
  var n = (name || '').toLowerCase();
  if (CLAUDE_TOOL_TABLE.hasOwnProperty(n)) return CLAUDE_TOOL_TABLE[n][0];
  if (n.indexOf('devops') > -1) return '&';
  if (n.indexOf('playwright') > -1) return '~';
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
  if (n.indexOf('subagent') > -1) return '※';
  if (n.indexOf('todo_list') > -1 || n.indexOf('goal') > -1) return '☰';
  if (n.indexOf('introspect') > -1) return '◉';
  return '+';
}

// --- Tool MCP group for badge color ---
function toolGroup(name) {
  var n = (name || '').toLowerCase();
  if (CLAUDE_TOOL_TABLE.hasOwnProperty(n)) return CLAUDE_TOOL_TABLE[n][1];
  if (n.indexOf('playwright') > -1) return 'web';
  if (n.indexOf('devops') > -1) return 'devops';
  if (n.indexOf('aws') > -1) return 'shell';
  if (n.indexOf('web') > -1 || n.indexOf('fetch') > -1 || n.indexOf('http') > -1) return 'web';
  if (n.indexOf('git') > -1 || n.indexOf('repo') > -1 || n.indexOf('pull') > -1 || n.indexOf('wit') > -1) return 'devops';
  if (n.indexOf('work_item') > -1 || n.indexOf('move_work') > -1 || n.indexOf('outline') > -1 || n.indexOf('tag') > -1 || n.indexOf('health') > -1 || n.indexOf('validate') > -1 || n.indexOf('migrate') > -1 || n.indexOf('session_brief') > -1 || n.indexOf('suggest_next') > -1 || n.indexOf('recent_activity') > -1 || n.indexOf('stale') > -1 || n.indexOf('hyperspace') > -1 || n.indexOf('similar') > -1 || n.indexOf('hypervisor') > -1 || n.indexOf('search_hyper') > -1 || n.indexOf('create_doc') > -1 || n.indexOf('update_doc') > -1) return 'hyper';
  if (n.indexOf('knowledge') > -1 || n.indexOf('index') > -1) return 'knowledge';
  if (n.indexOf('subagent') > -1) return 'subagent';
  if (n.indexOf('todo_list') > -1 || n.indexOf('goal') > -1) return 'tasks';
  if (n.indexOf('write') > -1 || n.indexOf('create') > -1 || n.indexOf('edit') > -1) return 'write';
  if (n.indexOf('shell') > -1 || n.indexOf('command') > -1 || n.indexOf('terminal') > -1) return 'shell';
  if (n.indexOf('read') > -1 || n.indexOf('file') > -1 || n.indexOf('search') > -1 || n.indexOf('grep') > -1 || n.indexOf('find') > -1 || n.indexOf('glob') > -1 || n.indexOf('directory') > -1) return 'read';
  return 'core';
}

// --- Truncate tool detail for display ---
function truncateDetail(data) {
  var str = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
  var esc = str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  // Strip emoji from tool output (terminal aesthetic)
  esc = esc.replace(/[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FA6F}\u{1FA70}-\u{1FAFF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{FE00}-\u{FE0F}\u{1F1E0}-\u{1F1FF}]/gu, '');
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
