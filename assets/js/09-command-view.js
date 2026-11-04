// Command view: a full-page overlay for a slash command's report (/usage, /context, ...). It opens as soon as the
// command is sent, fills in as the reply streams, and closes with Esc, the close button, or a click outside.
var commandViewEl = null;
var commandViewTurn = null;
var COMMAND_LIMIT_LINE = /^\*\*(.+?)\*\*\s+\u2014\s+\*\*(\d+(?:\.\d+)?)%\*\*(?:\s+\u00b7\s+Resets\s+(.+?))?\s*$/;
var COMMAND_MARKDOWN_ESCAPE = /\\(.)/g;
// The adapter draws bars as block characters: a line of its own under each limit, and inside table cells.
var COMMAND_BAR_LINE = /^`[\u2588\u2591]+`$/;
var COMMAND_BAR_TEXT = /^[\u2588\u2591]+$/;
var COMMAND_PERCENT_TEXT = /^(\d+(?:\.\d+)?)%$/;
var COMMAND_KV_BOLD = /^(?:[-*]\s+)?\*\*([^*]+?):\*\*\s*(.+)$/;
var COMMAND_KV_BOLD_OUT = /^(?:[-*]\s+)?\*\*([^*]+?)\*\*:\s*(.+)$/;
var COMMAND_KV_PLAIN = /^(?:[-*]\s+)?([A-Z][^:|`*]{0,28}):\s+(.+)$/;
var COMMAND_BOLD_HEAD = /^\*\*([^*]+?):?\*\*:?$/;
var COMMAND_CONTEXT_LINE = /^(?:[-*]\s+)?\**Tokens:?\**:?\s*(.*?\((\d+(?:\.\d+)?)%\).*)$/mi;
// MCP status group titles and the state color each takes.
var COMMAND_TONES = { connected: 'success', failed: 'warning', 'needs authentication': 'warning', connecting: 'live' };

// Pull the "**label** - **42%** - Resets ..." lines out of a usage report.
// Returns {limits: [{label, pct, reset}], rest} where rest is the Markdown without those lines.
function parseUsageReport(text) {
  var limits = [];
  var rest = [];
  (text || '').split('\n').forEach(function(line) {
    var m = COMMAND_LIMIT_LINE.exec(line.trim());
    if (!m) {
      if (!COMMAND_BAR_LINE.test(line.trim())) rest.push(line);
      return;
    }
    limits.push({
      label: m[1].replace(COMMAND_MARKDOWN_ESCAPE, '$1').trim(),
      pct: parseFloat(m[2]),
      reset: (m[3] || '').replace(COMMAND_MARKDOWN_ESCAPE, '$1').trim()
    });
  });
  var markdown = rest.join('\n');
  if (limits.length) markdown = markdown.replace(/^#+\s*Limits\s*$/m, '');
  return { limits: limits, rest: markdown.replace(/\n{3,}/g, '\n\n').trim() };
}

// Swap each block-character bar in a rendered report (`code` cells) for a cell bar from the shared viz primitives.
function replaceCommandBars(root) {
  root.querySelectorAll('code').forEach(function(code) {
    var text = code.textContent;
    if (!COMMAND_BAR_TEXT.test(text)) return;
    var filled = text.replace(/\u2591/g, '').length;
    var bar = workflowEl('span', 'command-view-cellbar');
    HvViz.cellBar(bar, { value: filled, max: text.length, cells: text.length });
    code.parentNode.replaceChild(bar, code);
    var cell = bar.parentNode;
    if (cell.tagName !== 'TD') return;
    var wrap = workflowEl('span', 'command-view-bar-cell');
    while (cell.firstChild) wrap.appendChild(cell.firstChild);
    cell.appendChild(wrap);
  });
}

// Split a report into an intro (text before the first section) and sections cut at its "###" headings.
// A leading "#"/"##" title repeats the view title, so it is dropped. A section whose body has two or more
// "**Label** - stats" lead lines is cut again, one child per lead line, so periods sit side by side.
function splitCommandReport(markdown) {
  var intro = [];
  var sections = [];
  var current = null;
  markdown.split('\n').forEach(function(line, i) {
    if (/^#{1,2}\s/.test(line) && !sections.length && !current) return;
    if (/^-{3,}\s*$/.test(line)) return;
    var h = /^#{3,}\s+(.*)$/.exec(line);
    if (h) {
      current = { title: h[1].trim(), lines: [] };
      sections.push(current);
    } else {
      (current ? current.lines : intro).push(line);
    }
  });
  sections.forEach(function(s) {
    var body = s.lines.join('\n').trim();
    var lead = /^\*\*([^*]+)\*\*\s+[^\s\w*]\s+(.*)$/;
    var chunks = [];
    var note = [];
    body.split('\n').forEach(function(line) {
      var m = lead.exec(line);
      if (m) chunks.push({ title: m[1].trim(), lines: [m[2]] });
      else (chunks.length ? chunks[chunks.length - 1].lines : note).push(line);
    });
    if (chunks.length > 1) {
      s.note = note.join('\n').trim();
      s.children = chunks.map(function(c) { return { title: c.title, body: c.lines.join('\n').trim() }; });
    } else {
      s.body = body;
    }
    delete s.lines;
  });
  return { intro: intro.join('\n').trim(), sections: sections };
}

// A table cell that is only a percentage gets a cell bar beside the number.
function replaceCommandPercents(root) {
  root.querySelectorAll('td').forEach(function(td) {
    var m = COMMAND_PERCENT_TEXT.exec(td.textContent.trim());
    if (!m || td.querySelector('.command-view-cellbar')) return;
    var pct = parseFloat(m[1]);
    var wrap = workflowEl('span', 'command-view-bar-cell');
    var bar = workflowEl('span', 'command-view-cellbar');
    HvViz.cellBar(bar, { value: Math.min(100, pct), max: 100, cells: 20 });
    wrap.appendChild(bar);
    wrap.appendChild(workflowEl('span', '', td.textContent.trim()));
    td.textContent = '';
    td.appendChild(wrap);
  });
}

function commandMarkdownEl(markdown) {
  var doc = workflowEl('div', 'command-view-markdown');
  doc.innerHTML = renderMarkdown(markdown);
  replaceCommandBars(doc);
  replaceCommandPercents(doc);
  return doc;
}

// "**Key:** value", "**Key**: value", or "Key: value" (optionally bulleted) -> [key, value], else null.
function parseCommandKv(line) {
  var m = COMMAND_KV_BOLD.exec(line) || COMMAND_KV_BOLD_OUT.exec(line) || COMMAND_KV_PLAIN.exec(line);
  return m ? [m[1].replace(COMMAND_MARKDOWN_ESCAPE, '$1').trim(), m[2].trim()] : null;
}

// Every non-blank line of the text as key/value rows, or null when any line is not one.
function commandKvRows(text) {
  var rows = [];
  var lines = text.split('\n').filter(function(l) { return l.trim(); });
  for (var i = 0; i < lines.length; i++) {
    var kv = parseCommandKv(lines[i].trim());
    if (!kv) return null;
    rows.push(kv);
  }
  return rows.length ? rows : null;
}

// Cut a headingless block of text into cards: key/value lines gather into one "Overview" card, a bold line over
// a list becomes a titled card, and any other prose gathers into one untitled card.
function splitCommandBlocks(text) {
  var cards = [];
  var overview = null;
  var prose = null;
  text.split(/\n{2,}/).forEach(function(block) {
    block = block.trim();
    if (!block) return;
    var rows = commandKvRows(block);
    if (rows) {
      if (!overview) { overview = { title: 'Overview', rows: [] }; cards.push(overview); }
      overview.rows = overview.rows.concat(rows);
      return;
    }
    var lines = block.split('\n');
    var head = COMMAND_BOLD_HEAD.exec(lines[0].trim());
    if (head && lines.length > 1) {
      cards.push({ title: head[1].trim(), body: lines.slice(1).join('\n'), tone: COMMAND_TONES[head[1].trim().toLowerCase()] });
      return;
    }
    if (!prose) { prose = { title: '', body: '' }; cards.push(prose); }
    prose.body += (prose.body ? '\n\n' : '') + block;
  });
  return cards;
}

function commandKvEl(rows) {
  var list = workflowEl('dl', 'command-view-kv');
  rows.forEach(function(row) {
    list.appendChild(workflowEl('dt', '', row[0]));
    var dd = workflowEl('dd', '');
    dd.appendChild(commandMarkdownEl(row[1]));
    list.appendChild(dd);
  });
  return list;
}

// One raised card: a mono label over its key/value rows or Markdown, or over a note and a row of child cards.
function commandCard(section) {
  var card = workflowEl('section', 'command-view-card' + (section.children ? ' command-view-card-wide' : ''));
  if (section.tone) card.setAttribute('data-tone', section.tone);
  if (section.title) card.appendChild(workflowEl('div', 'workflow-detail-meter-label', section.title));
  if (section.note) card.appendChild(commandMarkdownEl(section.note));
  if (section.children) {
    var row = workflowEl('div', 'command-view-cards');
    section.children.forEach(function(child) { row.appendChild(commandCard(child)); });
    card.appendChild(row);
  } else if (section.rows) {
    card.appendChild(commandKvEl(section.rows));
  } else if (section.body) {
    var rows = commandKvRows(section.body);
    card.appendChild(rows ? commandKvEl(rows) : commandMarkdownEl(section.body));
  }
  return card;
}

// The context window gauge: a "Tokens ... (42%)" line of the /context report as a meter.
function contextMeter(markdown) {
  var m = COMMAND_CONTEXT_LINE.exec(markdown);
  if (!m) return null;
  return workflowMeter('Context window', parseFloat(m[2]), 100, Math.round(parseFloat(m[2])) + '%  ' + m[1].replace(/[*`]/g, '').trim());
}

// Fill the body for a command's report: limit meters for usage, then the rest as a grid of cards.
function renderCommandBody(body, turn) {
  var markdown = turn.buf || '';
  if (turn.name === 'usage' || turn.name === 'extra-usage') {
    var report = parseUsageReport(markdown);
    if (report.limits.length) {
      var meters = workflowEl('div', 'command-view-meters');
      report.limits.forEach(function(l) {
        meters.appendChild(workflowMeter(l.label, l.pct, 100, Math.round(l.pct) + '%' + (l.reset ? '  resets ' + l.reset : '')));
      });
      body.appendChild(meters);
      markdown = report.rest;
    }
  }
  if (turn.name === 'context') {
    var gauge = contextMeter(markdown);
    if (gauge) {
      var gaugeRow = workflowEl('div', 'command-view-meters');
      gaugeRow.appendChild(gauge);
      body.appendChild(gaugeRow);
    }
  }
  if (!markdown) return;
  var parts = splitCommandReport(markdown);
  var cards = splitCommandBlocks(parts.intro).concat(parts.sections);
  if (!cards.length) cards = [{ body: markdown }];
  var grid = workflowEl('div', 'command-view-cards');
  cards.forEach(function(s) { grid.appendChild(commandCard(s)); });
  body.appendChild(grid);
}

function renderCommandView(turn, status, failed) {
  var panel = commandViewEl.querySelector('.command-view-panel');
  var oldBody = panel.querySelector('.command-view-body');
  var scroll = oldBody ? oldBody.scrollTop : 0;
  panel.textContent = '';
  panel.setAttribute('data-state', status);

  var header = workflowEl('div', 'workflow-detail-header');
  var kicker = workflowEl('div', 'workflow-detail-kicker');
  kicker.appendChild(workflowEl('span', '', 'Command'));
  kicker.appendChild(workflowEl('span', 'workflow-detail-state', status));
  var close = workflowEl('button', 'workflow-detail-close', '\u00d7');
  close.type = 'button';
  close.title = 'Close (Esc)';
  close.setAttribute('data-testid', 'command-view-close');
  close.addEventListener('click', closeCommandView);
  kicker.appendChild(close);
  header.appendChild(kicker);
  var view = COMMAND_VIEWS[turn.name] || {};
  header.appendChild(workflowEl('div', 'workflow-detail-title', view.title || turn.name));
  header.appendChild(workflowEl('div', 'command-view-input', turn.text));
  panel.appendChild(header);

  var body = workflowEl('div', 'workflow-detail-body command-view-body');
  if (turn.buf) renderCommandBody(body, turn);
  else if (status === 'running') body.appendChild(workflowEl('div', 'command-view-note', 'Waiting for the agent'));
  else body.appendChild(workflowEl('div', 'command-view-note', failed ? 'The command failed.' : 'The command returned no output.'));
  panel.appendChild(body);
  body.scrollTop = scroll;
}

function commandViewKey(e) {
  if (e.key === 'Escape') { closeCommandView(); e.stopPropagation(); }
}

function openCommandView(turn) {
  closeCommandView();
  commandViewTurn = turn;
  var overlay = workflowEl('div', 'modal-overlay workflow-detail-overlay command-view-overlay');
  overlay.setAttribute('data-testid', 'command-view');
  overlay.appendChild(workflowEl('div', 'modal-panel workflow-detail-panel command-view-panel'));
  overlay.addEventListener('mousedown', function(e) { if (e.target === overlay) closeCommandView(); });
  document.body.appendChild(overlay);
  commandViewEl = overlay;
  renderCommandView(turn, 'running', false);
  document.addEventListener('keydown', commandViewKey, true);
  requestAnimationFrame(function() { overlay.classList.add('visible'); });
}

function closeCommandView() {
  document.removeEventListener('keydown', commandViewKey, true);
  if (commandViewEl) commandViewEl.remove();
  commandViewEl = null;
  commandViewTurn = null;
}

// Called as the reply streams in; only repaints when this turn's view is the one on screen.
function refreshCommandView(turn) {
  if (commandViewEl && commandViewTurn === turn) renderCommandView(turn, 'running', false);
}

function finishCommandView(turn, data) {
  if (!commandViewEl || commandViewTurn !== turn) return;
  var failed = !!(data && (data._failed || data._cancelled));
  renderCommandView(turn, failed ? 'failed' : 'done', failed);
}
