/* ===== Hyperagent: Per-Tab Load State ===== */

// --- Per-tab loading state ---
// A tab that is starting or loading a session shows a loading block as its own
// content, so it hides and shows with its tab and other tabs stay usable.
// Rendering history replaces it; a failed load replaces it with the error and
// a Retry action.
var HA_FLAG_SVG = '<svg class="ha-splash-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 35.02 35.02" fill="currentColor" aria-hidden="true"><path d="M13.58,20.85c.19,0,.28-.23.15-.36,0,0-5.08-5.08-6.1-6.11-.11-.11-.27-.14-.42-.09L.34,17.01c-.45.18-.45.82,0,1l7.05,2.79c.11.04.22.06.33.06.98,0,5.85,0,5.85,0Z"/><path d="M20.85,27.42v-6.3c0-.12-.1-.21-.21-.21h-6.26c-.12,0-.21.1-.21.21v6.3s0,.03-.01.05l2.85,7.21c.18.45.82.45,1,0l2.85-7.21s-.01-.03-.01-.05Z"/><path d="M34.68,17.01l-7.87-3.11c-2.6-1.03-4.66-3.08-5.68-5.68L18.01.34c-.18-.45-.82-.45-1,0l-2.85,7.21s.01.03.01.05v6.3c0,.12.1.21.21.21h6.09c.27,0,.52.11.71.3,1.14,1.14,5.26,5.26,6.2,6.2.12.12.29.15.44.09l6.85-2.71c.45-.18.45-.82,0-1Z"/></svg>';

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
