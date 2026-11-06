// Agent question card: the agent asks the Operator to choose. One question per step in a reused
// card body; a question from a background tab waits behind a toast until that tab is focused.
var questionCards = {};

function questionEl(tag, className, text) {
  var el = document.createElement(tag);
  if (className) el.className = className;
  if (text) el.textContent = text;
  return el;
}

function questionTabTitle(data) {
  return data._tabId && typeof tabs !== 'undefined' && tabs[data._tabId] ? tabs[data._tabId].title : '';
}

function answerQuestion(card, action) {
  var a = window.pywebview && pywebview.api;
  if (!a || !a.answer_question) return;
  var answers = {};
  Object.keys(card.answers).forEach(function(id) { answers[id] = card.answers[id]; });
  a.answer_question(card.data.toolCallId, answers, action, card.data._tabId || null);
}

// Save the visible step's picks into the card state.
function collectQuestionStep(card, body) {
  var q = card.data.questions[card.step];
  var picked = [];
  body.querySelectorAll('input.question-card-pick').forEach(function(input) {
    if (input.checked) picked.push(input.value);
  });
  var other = body.querySelector('.question-card-other');
  card.answers[q.id] = {
    value: q.multiSelect ? picked : (picked[0] || ''),
    custom: other ? other.value.trim() : '',
  };
}

function renderQuestionStep(card) {
  var data = card.data;
  var q = data.questions[card.step];
  var total = data.questions.length;
  var saved = card.answers[q.id] || { value: q.multiSelect ? [] : '', custom: '' };

  card.kickerCount.textContent = total > 1 ? (card.step + 1) + ' of ' + total : '';
  card.title.textContent = q.title || 'Question';
  card.title.hidden = !q.title;
  card.body.textContent = '';

  var text = total === 1 ? data.message : q.description;
  if (text) card.body.appendChild(questionEl('div', 'question-card-text', text));

  var list = questionEl('div', 'question-card-options');
  q.options.forEach(function(opt) {
    var row = questionEl('label', 'question-card-option');
    var input = document.createElement('input');
    input.type = q.multiSelect ? 'checkbox' : 'radio';
    input.name = 'q-' + data.toolCallId;
    input.className = 'form-check question-card-pick';
    input.value = opt.value;
    input.checked = q.multiSelect ? saved.value.indexOf(opt.value) !== -1 : saved.value === opt.value;
    row.appendChild(input);
    row.appendChild(questionEl('span', 'question-card-option-title', opt.title));
    if (opt.description && opt.description !== opt.title) {
      row.appendChild(questionEl('span', 'question-card-option-desc', opt.description));
    }
    list.appendChild(row);
  });
  card.body.appendChild(list);

  if (q.custom) {
    card.body.appendChild(questionEl('div', 'question-card-other-label', 'Other (optional)'));
    var other = document.createElement('textarea');
    other.className = 'form-textarea question-card-other';
    other.rows = 2;
    other.placeholder = 'Type your own answer, or add a note...';
    other.value = saved.custom;
    card.body.appendChild(other);
  }

  var last = card.step === total - 1;
  card.back.style.display = card.step === 0 ? 'none' : '';
  card.next.textContent = last ? 'Submit' : 'Next';
  card.next.setAttribute('data-testid', last ? 'question-card-submit' : 'question-card-next');
}

function buildQuestionCard(card) {
  var data = card.data;
  var overlay = questionEl('div', 'modal-overlay');
  overlay.setAttribute('data-testid', 'question-card');
  var panel = questionEl('div', 'question-card-panel modal-panel');
  panel.setAttribute('role', 'alertdialog');

  var header = questionEl('div', 'question-card-header');
  var kicker = questionEl('div', 'question-card-kicker');
  kicker.appendChild(questionEl('span', '', 'Question'));
  var tab = questionTabTitle(data);
  if (tab) kicker.appendChild(questionEl('span', 'question-card-kicker-tab', tab));
  card.kickerCount = questionEl('span', 'question-card-count');
  kicker.appendChild(card.kickerCount);
  header.appendChild(kicker);
  card.title = questionEl('div', 'question-card-title');
  header.appendChild(card.title);
  panel.appendChild(header);

  card.body = questionEl('div', 'question-card-body');
  panel.appendChild(card.body);

  var actions = questionEl('div', 'question-card-actions');
  var skip = questionEl('button', 'action-button action-button-ghost question-card-skip', 'Skip');
  skip.type = 'button';
  skip.setAttribute('data-testid', 'question-card-skip');
  skip.addEventListener('click', function() { answerQuestion(card, 'decline'); });
  card.back = questionEl('button', 'action-button action-button-ghost', 'Back');
  card.back.type = 'button';
  card.back.setAttribute('data-testid', 'question-card-back');
  card.back.addEventListener('click', function() {
    collectQuestionStep(card, card.body);
    card.step -= 1;
    renderQuestionStep(card);
  });
  card.next = questionEl('button', 'action-button action-button-primary');
  card.next.type = 'button';
  card.next.addEventListener('click', function() {
    collectQuestionStep(card, card.body);
    if (card.step < data.questions.length - 1) {
      card.step += 1;
      renderQuestionStep(card);
    } else {
      answerQuestion(card, 'accept');
    }
  });
  [skip, card.back, card.next].forEach(function(btn) { actions.appendChild(btn); });
  panel.appendChild(actions);

  overlay.appendChild(panel);
  card.el = overlay;
  renderQuestionStep(card);
}

function showQuestionCard(card) {
  if (card.el) return;
  buildQuestionCard(card);
  document.body.appendChild(card.el);
  requestAnimationFrame(function() { card.el.classList.add('visible'); });
}

function hideQuestionCard(card) {
  if (!card.el) return;
  card.el.remove();
  card.el = null;
}

// Show the card of the focused tab and park the rest; called on every tab switch.
window._syncQuestionCards = function() {
  Object.keys(questionCards).forEach(function(id) {
    var card = questionCards[id];
    var mine = !card.data._tabId || card.data._tabId === activeTabId;
    if (mine) showQuestionCard(card); else hideQuestionCard(card);
  });
};

window.__acpQuestion = function(data) {
  if (!data || !data.toolCallId || questionCards[data.toolCallId]) return;
  if (!data.questions || !data.questions.length) return;
  questionCards[data.toolCallId] = { data: data, step: 0, answers: {}, el: null };
  if (!data._tabId || data._tabId === activeTabId) {
    showQuestionCard(questionCards[data.toolCallId]);
  } else if (window.HvToast) {
    window.HvToast.show({
      variant: 'warn',
      title: (questionTabTitle(data) || 'A tab') + ' has a question',
      message: 'It is waiting for your answer.',
      action: { label: 'Go to tab', onClick: function() { if (window.switchTab) window.switchTab(data._tabId); } },
    });
  }
};

// Inline record of the Operator's answers: collapsed by default, one Question -> Answer row each.
// items: [{question, answer, note}]; an empty list means the questions were skipped.
function buildAnswersCard(items) {
  var card = questionEl('div', 'answers-card collapsed');
  card.setAttribute('data-testid', 'answers-card');
  var head = questionEl('button', 'answers-card-header');
  head.type = 'button';
  head.appendChild(questionEl('span', 'answers-card-label', items.length ? 'answered' : 'skipped'));
  if (items.length) {
    head.appendChild(questionEl('span', 'status-chip status-chip-outlined-muted', items.length + (items.length === 1 ? ' question' : ' questions')));
  }
  card.appendChild(head);
  var body = questionEl('div', 'answers-card-body');
  items.forEach(function(item) {
    var row = questionEl('div', 'answers-card-row');
    row.appendChild(questionEl('div', 'answers-card-question', item.question));
    row.appendChild(questionEl('div', 'answers-card-answer', item.answer || 'No answer'));
    if (item.note) row.appendChild(questionEl('div', 'answers-card-note', item.note));
    body.appendChild(row);
  });
  card.appendChild(body);
  if (!items.length) {
    head.disabled = true;
  } else if (window.HvCollapsible) {
    window.HvCollapsible.register({ header: head, panel: card, collapsed: true });
  }
  return card;
}

function answerItems(card) {
  return card.data.questions.map(function(q) {
    var a = card.answers[q.id] || { value: q.multiSelect ? [] : '', custom: '' };
    var picks = [].concat(a.value || []).map(function(v) {
      var opt = q.options.filter(function(o) { return o.value === v; })[0];
      return opt ? opt.title : v;
    });
    var answer = picks.join(', ');
    var note = '';
    if (a.custom) {
      if (answer) note = a.custom; else answer = a.custom;
    }
    return { question: q.title || q.description || 'Question', answer: answer, note: note };
  });
}

window.__acpQuestionClosed = function(data) {
  var card = data && questionCards[data.toolCallId];
  if (!card) return;
  hideQuestionCard(card);
  delete questionCards[data.toolCallId];
  if (data.action !== 'accept' && data.action !== 'decline') return;
  var container = tabContainerFor(card.data);
  if (!container) return;
  container.appendChild(buildAnswersCard(data.action === 'accept' ? answerItems(card) : []));
  if (container === msgs) scrollBottom();
};

// Replay a past AskUserQuestion tool entry as an answers card; null when the output is not parseable.
function buildAnswersCardFromTool(input, output) {
  var qs = input && input.questions;
  if (!Array.isArray(qs) || !qs.length) return null;
  var found = {};
  var re = /"([^"]+)"="([^"]*)"/g;
  var m;
  var text = String(output || '');
  while ((m = re.exec(text)) !== null) found[m[1]] = m[2];
  var items = qs.filter(function(q) { return found[q.question] !== undefined; }).map(function(q) {
    return { question: q.header || q.question, answer: found[q.question], note: '' };
  });
  return items.length ? buildAnswersCard(items) : null;
}
window.buildAnswersCardFromTool = buildAnswersCardFromTool;
