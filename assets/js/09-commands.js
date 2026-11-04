/* ===== Hyperagent: Slash commands =====
   The agent advertises its slash commands (available_commands_update); typing "/" in the
   input opens a filterable menu of them. Commands listed in COMMAND_VIEWS print a report
   instead of chatting: they run as a normal prompt, but their reply is held back from the
   transcript and shown in a full-page view (09-command-view.js). */

// Commands whose reply opens a full-page view: name -> {title}. Everything else is chat.
var COMMAND_VIEWS = {
  usage: { title: 'Usage' },
  'extra-usage': { title: 'Extra usage' },
  context: { title: 'Context' },
  mcp: { title: 'MCP servers' },
  status: { title: 'Status' }
};

var commandsByTab = {};
var commandMenuEl = null;
var commandMenuItems = [];
var commandMenuIndex = 0;

function activeCommands() {
  return commandsByTab[activeTabId] || commandsByTab._ || [];
}

// "/name rest" -> {name, args}, or null when the text is not a slash command.
function parseSlashCommand(text) {
  var m = /^\/([A-Za-z0-9][\w:.-]*)(?:\s+([\s\S]*))?$/.exec((text || '').trim());
  return m ? { name: m[1], args: (m[2] || '').trim() } : null;
}

// Commands matching the first word typed after "/": prefix matches first, then substring.
function filterCommands(commands, query) {
  var q = (query || '').toLowerCase();
  var prefix = [];
  var inside = [];
  commands.forEach(function(c) {
    var n = c.name.toLowerCase();
    if (n.indexOf(q) === 0) prefix.push(c);
    else if (n.indexOf(q) > -1) inside.push(c);
  });
  return prefix.concat(inside);
}

window.__acpCommands = function(data) {
  if (!data || !Array.isArray(data.commands)) return;
  commandsByTab[data._tabId || '_'] = data.commands;
  if (commandMenuEl) refreshCommandMenu();
};

function seedCommands() {
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_commands) return;
  var tabId = activeTabId;
  window.pywebview.api.get_commands().then(function(list) {
    if (Array.isArray(list) && list.length) {
      commandsByTab[tabId || '_'] = list;
      if (commandMenuEl) refreshCommandMenu();
    }
  }).catch(function() { /* menu stays empty until the agent pushes its list */ });
}

function closeCommandMenu() {
  if (commandMenuEl) commandMenuEl.remove();
  commandMenuEl = null;
  commandMenuItems = [];
  commandMenuIndex = 0;
}

function commandMenuItem(cmd, active) {
  var row = document.createElement('div');
  row.className = 'menu-item command-item' + (active ? ' active' : '');
  row.setAttribute('role', 'option');
  row.setAttribute('data-command', cmd.name);
  var name = document.createElement('span');
  name.className = 'command-name';
  name.textContent = '/' + cmd.name;
  row.appendChild(name);
  if (cmd.hint) {
    var hint = document.createElement('span');
    hint.className = 'command-hint';
    hint.textContent = cmd.hint;
    row.appendChild(hint);
  }
  if (COMMAND_VIEWS[cmd.name]) {
    var tag = document.createElement('span');
    tag.className = 'label-badge command-view-tag';
    tag.textContent = 'view';
    row.appendChild(tag);
  }
  if (cmd.description) {
    var desc = document.createElement('span');
    desc.className = 'command-desc';
    desc.textContent = cmd.description;
    row.appendChild(desc);
  }
  row.addEventListener('mousedown', function(e) {
    e.preventDefault();
    pickCommand(cmd, false);
  });
  return row;
}

// Rebuild the menu for the text in the input; closes it when there is nothing to offer.
function refreshCommandMenu() {
  var m = /^\/([^\s]*)$/.exec(input.value);
  var commands = activeCommands();
  if (!m || !commands.length) { closeCommandMenu(); return; }
  var matches = filterCommands(commands, m[1]);
  if (!matches.length) { closeCommandMenu(); return; }
  var prev = commandMenuItems[commandMenuIndex];
  commandMenuItems = matches;
  commandMenuIndex = Math.max(0, prev ? matches.indexOf(prev) : 0);
  if (!commandMenuEl) {
    commandMenuEl = document.createElement('div');
    commandMenuEl.className = 'floating-panel command-menu';
    commandMenuEl.setAttribute('role', 'listbox');
    commandMenuEl.setAttribute('data-testid', 'command-menu');
    input.parentNode.appendChild(commandMenuEl);
  }
  commandMenuEl.textContent = '';
  matches.forEach(function(c, i) { commandMenuEl.appendChild(commandMenuItem(c, i === commandMenuIndex)); });
  var active = commandMenuEl.querySelector('.command-item.active');
  if (active && active.scrollIntoView) active.scrollIntoView({ block: 'nearest' });
}

function moveCommandMenu(step) {
  var count = commandMenuItems.length;
  commandMenuIndex = (commandMenuIndex + step + count) % count;
  var rows = commandMenuEl.querySelectorAll('.command-item');
  rows.forEach(function(r, i) { r.classList.toggle('active', i === commandMenuIndex); });
  if (rows[commandMenuIndex] && rows[commandMenuIndex].scrollIntoView) rows[commandMenuIndex].scrollIntoView({ block: 'nearest' });
}

// Fill the input with the command. A command that takes no arguments and was picked
// with Enter runs right away; otherwise the cursor waits after "/name ".
function pickCommand(cmd, run) {
  input.value = '/' + cmd.name + (cmd.hint || !run ? ' ' : '');
  input.focus();
  closeCommandMenu();
  if (run && !cmd.hint) send();
}

// Called from the input's keydown. Returns true when the menu consumed the key.
function commandMenuKey(e) {
  if (!commandMenuEl) return false;
  if (e.key === 'ArrowDown') { moveCommandMenu(1); e.preventDefault(); return true; }
  if (e.key === 'ArrowUp') { moveCommandMenu(-1); e.preventDefault(); return true; }
  if (e.key === 'Escape') { closeCommandMenu(); e.stopPropagation(); return true; }
  if (e.key === 'Tab' && !e.shiftKey) { pickCommand(commandMenuItems[commandMenuIndex], false); e.preventDefault(); return true; }
  if (e.key === 'Enter' && !e.shiftKey) {
    var cmd = commandMenuItems[commandMenuIndex];
    // Exactly what is typed: nothing left to complete, so let the send go through.
    if (input.value.trim() === '/' + cmd.name) { closeCommandMenu(); return false; }
    pickCommand(cmd, true);
    e.preventDefault();
    return true;
  }
  return false;
}

// Called from send() before the normal prompt path. True when the text ran as a
// command view (the reply opens a full-page view instead of the transcript).
function tryCommandView(text) {
  var cmd = parseSlashCommand(text);
  if (!cmd || !COMMAND_VIEWS.hasOwnProperty(cmd.name)) return false;
  if (state !== 'ready' || _loadingHistory || pendingAttachments.length) return false;
  input.value = '';
  input.style.height = 'auto';
  closeCommandMenu();
  commandTurn = { name: cmd.name, text: text.trim(), buf: '' };
  openCommandView(commandTurn);
  pywebview.api.send_prompt(commandTurn.text);
  return true;
}

input.addEventListener('input', function() {
  if (input.value.charAt(0) === '/' && !activeCommands().length) seedCommands();
  refreshCommandMenu();
});
input.addEventListener('blur', closeCommandMenu);
