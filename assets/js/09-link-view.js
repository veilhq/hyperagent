// Link view: clicking an http(s) link in the chat opens a full-page overlay (same shell as the command view)
// with the URL, a copy button and an "open in external browser" button. Closes with Esc, x, or a click outside.
var linkViewEl = null;

function linkViewCopy(url, btn) {
  function done() {
    btn.textContent = 'Copied';
    btn.classList.add('copied');
    setTimeout(function() { btn.textContent = 'Copy link'; btn.classList.remove('copied'); }, 1200);
  }
  function fallback() { navigator.clipboard.writeText(url).then(done); }
  if (window.pywebview && pywebview.api && pywebview.api.copy_to_clipboard) {
    pywebview.api.copy_to_clipboard(url).then(function(ok) { if (ok) done(); else fallback(); });
  } else {
    fallback();
  }
}

function linkViewKey(e) {
  if (e.key === 'Escape') { closeLinkView(); e.stopPropagation(); }
}

function openLinkView(url) {
  closeLinkView();
  var overlay = workflowEl('div', 'modal-overlay workflow-detail-overlay link-view-overlay');
  overlay.setAttribute('data-testid', 'link-view');
  var panel = workflowEl('div', 'modal-panel workflow-detail-panel link-view-panel');

  var header = workflowEl('div', 'workflow-detail-header');
  var kicker = workflowEl('div', 'workflow-detail-kicker');
  kicker.appendChild(workflowEl('span', '', 'Link'));
  var actions = workflowEl('span', 'link-view-actions');
  var copy = workflowEl('button', 'link-view-btn', 'Copy link');
  copy.type = 'button';
  copy.setAttribute('data-testid', 'link-view-copy');
  copy.addEventListener('click', function() { linkViewCopy(url, copy); });
  var ext = workflowEl('button', 'link-view-btn', 'Open in external browser');
  ext.type = 'button';
  ext.setAttribute('data-testid', 'link-view-external');
  ext.addEventListener('click', function() {
    if (window.pywebview && pywebview.api && pywebview.api.open_external) pywebview.api.open_external(url);
    else window.open(url, '_blank', 'noopener');
  });
  actions.appendChild(copy);
  actions.appendChild(ext);
  kicker.appendChild(actions);
  var close = workflowEl('button', 'workflow-detail-close link-view-close', '×');
  close.type = 'button';
  close.title = 'Close (Esc)';
  close.setAttribute('data-testid', 'link-view-close');
  close.addEventListener('click', closeLinkView);
  kicker.appendChild(close);
  header.appendChild(kicker);
  panel.appendChild(header);

  var body = workflowEl('div', 'workflow-detail-body link-view-body');
  var frame = workflowEl('iframe', 'link-view-frame');
  frame.src = url;
  frame.setAttribute('data-testid', 'link-view-frame');
  frame.setAttribute('referrerpolicy', 'no-referrer');
  body.appendChild(frame);
  panel.appendChild(body);
  var hint = workflowEl('div', 'link-view-url', url + '  -  a blank page means the site blocks embedding; use Open in external browser');
  panel.appendChild(hint);

  overlay.appendChild(panel);
  overlay.addEventListener('mousedown', function(e) { if (e.target === overlay) closeLinkView(); });
  document.body.appendChild(overlay);
  linkViewEl = overlay;
  document.addEventListener('keydown', linkViewKey, true);
  requestAnimationFrame(function() { overlay.classList.add('visible'); });
}

function closeLinkView() {
  document.removeEventListener('keydown', linkViewKey, true);
  if (linkViewEl) linkViewEl.remove();
  linkViewEl = null;
}

// Delegated so links in every tab's message container are caught; anchors with an explicit target keep their own behavior.
document.addEventListener('click', function(e) {
  var a = e.target.closest ? e.target.closest('a[href]') : null;
  if (!a || a.getAttribute('target') || a.closest('.link-view-overlay')) return;
  if (!/^https?:\/\//i.test(a.href)) return;
  e.preventDefault();
  openLinkView(a.href);
});
