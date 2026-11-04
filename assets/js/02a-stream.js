/* ===== Hyperagent: Stream Renderer + Thought Stream ===== */

// --- Stream buffer + word-by-word drain ---
var streamBuffer = '';
var streamDraining = false;
var streamDrainMs = 120;        // ms between word renders
var streamMaxBuffer = 200;      // chars in buffer before we speed up

function drainStream() {
  if (!streamBuffer.length) { streamDraining = false; return; }
  // Adaptive: drain faster when buffer is deep
  var wordsThisTick = 1;
  var delay = streamDrainMs;
  if (streamBuffer.length > streamMaxBuffer) {
    wordsThisTick = Math.ceil(streamBuffer.length / 40);
    delay = 16;
  }
  // Pull words (preserving whitespace boundaries)
  var pulled = '';
  var count = 0;
  var i = 0;
  while (i < streamBuffer.length && count < wordsThisTick) {
    while (i < streamBuffer.length && /\s/.test(streamBuffer[i])) { pulled += streamBuffer[i]; i++; }
    while (i < streamBuffer.length && !/\s/.test(streamBuffer[i])) { pulled += streamBuffer[i]; i++; }
    count++;
  }
  streamBuffer = streamBuffer.slice(i);
  if (!pulled) { streamDraining = false; return; }
  // Append to currentMsgText and re-render
  currentMsgText += pulled;
  if (currentMsgEl) {
    currentMsgEl._rawText = currentMsgText;
    var meta = currentMsgEl.querySelector('.msg-meta');
    currentMsgEl.innerHTML = '';
    if (meta) currentMsgEl.appendChild(meta);
    var rendered = renderMarkdown(currentMsgText);
    currentMsgEl.insertAdjacentHTML('beforeend', rendered + '<button class="msg-copy">Copy</button>');
    // Fade-in: wrap trailing chars in .stream-word
    fadeTrailingText(currentMsgEl, pulled.length);
    scrollBottom();
  }
  if (streamBuffer.length) {
    setTimeout(drainStream, delay);
  } else {
    streamDraining = false;
  }
}

function fadeTrailingText(el, charCount) {
  var walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  var nodes = [];
  var node;
  while (node = walker.nextNode()) {
    var p = node.parentElement;
    if (p && (p.classList.contains('msg-meta') || p.classList.contains('msg-role') || p.classList.contains('msg-time') || p.classList.contains('msg-copy') || p.classList.contains('streaming-cursor'))) continue;
    nodes.push(node);
  }
  var remaining = charCount;
  for (var i = nodes.length - 1; i >= 0 && remaining > 0; i--) {
    var n = nodes[i];
    var text = n.textContent;
    if (text.length <= remaining) {
      var wrap = document.createElement('span');
      wrap.className = 'stream-word';
      n.parentNode.insertBefore(wrap, n);
      wrap.appendChild(n);
      remaining -= text.length;
    } else {
      var splitAt = text.length - remaining;
      var tail = n.splitText(splitAt);
      var wrap2 = document.createElement('span');
      wrap2.className = 'stream-word';
      tail.parentNode.insertBefore(wrap2, tail);
      wrap2.appendChild(tail);
      remaining = 0;
    }
  }
}

function flushStream() {
  if (streamBuffer.length) {
    currentMsgText += streamBuffer;
    streamBuffer = '';
  }
  streamDraining = false;
  if (currentMsgEl) {
    currentMsgEl._rawText = currentMsgText;
    var meta = currentMsgEl.querySelector('.msg-meta');
    currentMsgEl.innerHTML = '';
    if (meta) currentMsgEl.appendChild(meta);
    currentMsgEl.insertAdjacentHTML('beforeend', renderMarkdown(currentMsgText) + '<button class="msg-copy">Copy</button>');
    currentMsgEl.querySelectorAll('.stream-word').forEach(function(sw) {
      while (sw.firstChild) sw.parentNode.insertBefore(sw.firstChild, sw);
      sw.remove();
    });
  }
}

// --- Thought stream ---
// agent_thought_chunk arrives per-word from reasoning-enabled models.
// Rendered into a collapsed-by-default panel under the Agent nameplate,
// with a live char count and a CSS-masked Bayer dither indicator that
// animates only while chunks are actively arriving. Distinct from the
// visible message stream: no word-fade, no markdown parsing.
var currentThoughtEl = null;      // .msg-thought container for current turn
var currentThoughtBodyEl = null;  // <pre> holding the accumulated text
var currentThoughtCountEl = null; // live char/token count on the bar
var thoughtCharCount = 0;
var thoughtIdleTimer = null;
var thoughtIdleMs = 500;          // idle ms before the dither settles
var thoughtIdSeq = 0;

function ensureThoughtEl() {
  // Called on first agent_thought_chunk of a reasoning batch. The row is a
  // sibling of .msg-user / .msg-agent / .tool-row inside #messages - each
  // reasoning batch reads as its own phase of the turn, matching how tool
  // rows are their own line rather than nested inside a message bubble.
  if (currentThoughtEl) return currentThoughtEl;
  var id = 'thought-body-' + (++thoughtIdSeq);
  var wrap = document.createElement('div');
  wrap.className = 'msg-thought';
  wrap.setAttribute('data-expanded', 'false');
  wrap.setAttribute('data-active', 'false');

  var bar = document.createElement('button');
  bar.type = 'button';
  bar.className = 'msg-thought-bar';
  bar.setAttribute('aria-expanded', 'false');
  bar.setAttribute('aria-controls', id);

  var dither = document.createElement('span');
  dither.className = 'msg-thought-dither';
  dither.setAttribute('aria-hidden', 'true');
  // 9 grid cells: 8 perimeter cells + 1 center. CSS assigns animation-delay
  // per :nth-child to trace a clockwise perimeter chase; center stays empty.
  for (var i = 0; i < 9; i++) {
    dither.appendChild(document.createElement('span'));
  }

  var marker = document.createElement('span');
  marker.className = 'msg-thought-marker';
  marker.textContent = 'thought';

  var count = document.createElement('span');
  count.className = 'msg-thought-count';
  count.textContent = '0';

  bar.appendChild(dither);
  bar.appendChild(marker);
  bar.appendChild(count);

  var body = document.createElement('pre');
  body.className = 'msg-thought-body';
  body.id = id;
  body.hidden = true;

  bar.addEventListener('click', function () {
    var expanded = wrap.getAttribute('data-expanded') === 'true';
    var next = !expanded;
    wrap.setAttribute('data-expanded', next ? 'true' : 'false');
    bar.setAttribute('aria-expanded', next ? 'true' : 'false');
    body.hidden = !next;
    if (next) scrollBottom();
  });

  wrap.appendChild(bar);
  wrap.appendChild(body);
  msgs.appendChild(wrap);

  currentThoughtEl = wrap;
  currentThoughtBodyEl = body;
  currentThoughtCountEl = count;
  thoughtCharCount = 0;
  return wrap;
}

function setThoughtActive(active) {
  if (currentThoughtEl) {
    currentThoughtEl.setAttribute('data-active', active ? 'true' : 'false');
  }
}

function bumpThoughtIdle() {
  if (thoughtIdleTimer) clearTimeout(thoughtIdleTimer);
  // Capture the element at schedule time. With per-tab render state, the
  // module-level `currentThoughtEl` may point at a different tab by the time
  // this timer fires - closing over `el` ensures the idle release lands on
  // the same element that scheduled it.
  var el = currentThoughtEl;
  thoughtIdleTimer = setTimeout(function () {
    thoughtIdleTimer = null;
    if (el) el.setAttribute('data-active', 'false');
  }, thoughtIdleMs);
}

function appendThoughtChunk(text) {
  if (!text) return;
  ensureThoughtEl();
  if (currentThoughtBodyEl) {
    currentThoughtBodyEl.appendChild(document.createTextNode(text));
  }
  thoughtCharCount += text.length;
  if (currentThoughtCountEl) {
    currentThoughtCountEl.textContent = String(thoughtCharCount);
  }
  setThoughtActive(true);
  bumpThoughtIdle();
}

function resetThoughtStream() {
  if (thoughtIdleTimer) { clearTimeout(thoughtIdleTimer); thoughtIdleTimer = null; }
  currentThoughtEl = null;
  currentThoughtBodyEl = null;
  currentThoughtCountEl = null;
  thoughtCharCount = 0;
}
