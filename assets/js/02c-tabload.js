/* ===== Hyperagent: Per-Tab Load State ===== */

// --- Per-tab loading state ---
// A tab that is starting or loading a session shows a loading block as its own
// content, so it hides and shows with its tab and other tabs stay usable.
// Rendering history replaces it; a failed load replaces it with the error and
// a Retry action.
var HA_FLAG_SVG = '<svg class="ha-splash-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 37.03 35.95" fill="currentColor" aria-hidden="true"><path d="M19.97.02c-.5-.03-.92.39-.92.9v7.95c0,.47.37.84.84.89,4.16.42,7.42,3.95,7.42,8.23s-3.26,7.8-7.42,8.23c-.47.05-.84.41-.84.89v7.95c0,.5.42.92.92.9,9.49-.48,17.06-8.35,17.06-17.96S29.46.5,19.97.02Z"/><path d="M16.85,0H1.13C.51,0,0,.51,0,1.13v7.47c0,.62.51,1.13,1.13,1.13h15.72c.62,0,1.13-.51,1.13-1.13V1.13c0-.62-.51-1.13-1.13-1.13ZM7.51,6.72c0,.38-.3.68-.68.68h-3.7c-.37,0-.68-.3-.68-.68v-3.7c0-.37.3-.68.68-.68h3.7c.38,0,.68.3.68.68v3.7Z"/><path d="M17.21,26.22c-4.2-.39-7.5-3.93-7.5-8.23,0-2.51,1.13-4.77,2.91-6.28.32-.28.14-.8-.28-.8H2.25c-.47,0-.91.28-1.07.72-.75,1.98-1.17,4.12-1.17,6.36,0,9.61,7.57,17.48,17.06,17.96.5.03.92-.39.92-.9v-7.95c0-.45-.33-.84-.77-.88Z"/></svg>';

// The messages container of the tab an event belongs to.
function tabContainerFor(data) {
  var tabId = data && data._tabId;
  if (tabId && typeof tabs !== 'undefined' && tabs[tabId]) return tabs[tabId].msgsEl;
  return msgs;
}

// True when the container holds nothing but a loading or load-failed block.
function tabHasNoContent(container) {
  for (var i = 0; i < container.children.length; i++) {
    var cls = container.children[i].classList;
    if (!cls.contains('tab-loading') && !cls.contains('tab-load-failed')) return false;
  }
  return true;
}

// The tab's current loading block, ignoring one that is already fading out.
function activeTabLoading(container) {
  return container ? container.querySelector(':scope > .tab-loading:not(.tab-loading-exit)') : null;
}

// Empty the tab. With keepLoading, a current loading block survives so it does
// not re-run its entrance animation.
function clearTabContent(container, keepLoading) {
  var keep = keepLoading ? activeTabLoading(container) : null;
  container.classList.remove('tab-connecting');
  container.innerHTML = '';
  if (keep) container.appendChild(keep);
}

function showTabLoading(container, text) {
  if (!container) return;
  clearTabContent(container);
  var block = document.createElement('div');
  block.className = 'tab-loading';
  block.setAttribute('role', 'status');
  block.setAttribute('data-testid', 'tab-loading');
  block.innerHTML = '<div class="ha-splash-flag">' + HA_FLAG_SVG + '</div>'
    + '<div class="ha-splash-loading tab-loading-text"></div><div class="ha-splash-blink"></div>';
  block.querySelector('.tab-loading-text').textContent = text;
  container.appendChild(block);
}

// Update the loading text in place; false when the container is not loading.
function setTabLoadingText(container, text) {
  var block = activeTabLoading(container);
  var el = block && block.querySelector('.tab-loading-text');
  if (!el) return false;
  el.textContent = text;
  return true;
}

// Fade the loading block out, then remove it and reveal any history rendered
// behind it. A block removed by other content mid-fade is left alone.
function clearTabLoading(container) {
  if (!container) return;
  var block = activeTabLoading(container);
  if (!block) {
    if (container.classList.contains('tab-connecting') && !container.querySelector(':scope > .tab-loading')) {
      container.classList.remove('tab-connecting');
      container.scrollTop = container.scrollHeight;
    }
    return;
  }
  var finished = false;
  function finish() {
    if (finished) return;
    finished = true;
    if (block.parentNode !== container) return;
    container.removeChild(block);
    if (container.classList.contains('tab-connecting')) {
      container.classList.remove('tab-connecting');
      container.scrollTop = container.scrollHeight;
    }
  }
  block.classList.add('tab-loading-exit');
  // Reduced motion zeroes the transition, so transitionend never fires; the timer covers it.
  var fadeMs = parseFloat(getComputedStyle(block).transitionDuration) * 1000 || 0;
  block.addEventListener('transitionend', function(e) { if (e.target === block) finish(); });
  setTimeout(finish, fadeMs + 50);
}

function showTabLoadFailed(container, error, sessionId) {
  if (!container) return;
  clearTabContent(container);
  var block = document.createElement('div');
  block.className = 'tab-load-failed';
  block.setAttribute('role', 'alert');
  block.innerHTML = '<div class="ha-splash-flag">' + HA_FLAG_SVG + '</div>'
    + '<div class="ha-splash-loading">Session did not load</div>'
    + '<div class="tab-load-error"></div>'
    + '<button type="button" class="action-button" data-testid="retry-session-load-button">Retry</button>';
  block.querySelector('.tab-load-error').textContent = error || '';
  block.querySelector('button').addEventListener('click', function() {
    if (sessionId && typeof loadSession === 'function') loadSession(sessionId);
  });
  container.appendChild(block);
}
