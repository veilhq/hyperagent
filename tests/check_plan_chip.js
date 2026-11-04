// Exercises the plan-usage chip logic in 00-core.js against a stubbed DOM.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const els = {};
function el(id) {
  if (!els[id]) {
    els[id] = {
      id, textContent: '', className: '', title: '', style: {}, hidden: false, childNodes: [],
      classList: { add() {}, remove() {}, contains() { return false; }, toggle() {} },
      addEventListener() {}, appendChild(c) { this.childNodes.push(c); }, querySelector() { return null; },
      setAttribute(k, v) { if (k === 'data-tooltip') this.title = v; }, removeAttribute(k) { if (k === 'data-tooltip') this.title = ''; }, getBoundingClientRect() { return { width: 0, height: 0 }; },
    };
  }
  return els[id];
}
const toasts = [];
const bar = { msg: '', action: null, visible: false };
const sandbox = {
  console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval, Date, Math, JSON,
  document: {
    getElementById: el, querySelector: () => el('q'), querySelectorAll: () => [],
    createElement: () => el('c' + Math.random()), addEventListener() {},
    documentElement: { style: { setProperty() {} }, classList: { add() {}, remove() {}, contains() { return false; } } },
    body: el('body'), readyState: 'complete',
  },
  requestAnimationFrame: () => 0, matchMedia: () => ({ matches: false, addEventListener() {} }),
  ResizeObserver: function () { return { observe() {} }; },
  MutationObserver: function () { return { observe() {} }; },
  localStorage: { getItem() { return null; }, setItem() {} },
  navigator: { clipboard: {} },
};
sandbox.window = sandbox;
sandbox.addEventListener = () => {};
sandbox.HvToast = { show: (t) => toasts.push(t), error() {} };
vm.createContext(sandbox);
const src = fs.readFileSync(path.join(__dirname, '..', 'assets', 'js', '00-core.js'), 'utf8');
vm.runInContext(src, sandbox);
sandbox.HaErrorBar.setMessage = (m) => { bar.msg = m; bar.visible = true; };
sandbox.HaErrorBar.setAction = (label, fn) => { bar.action = fn; };
sandbox.HaErrorBar.hide = () => { bar.msg = ''; bar.visible = false; };

function assert(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); process.exit(1); } }
const label = el('plan-credits-label');
const now = Date.now() / 1000;

// Pull: leads with the five-hour window, weekly in the tooltip.
sandbox.applyPlanUsagePull({ windows: [
  { key: 'five_hour', label: 'five-hour', utilization: 25, resetsAt: now + 3600, resetsText: '' },
  { key: 'seven_day', label: 'weekly', utilization: 3, resetsAt: now + 86400 * 5, resetsText: '' },
], fetchedAt: now, stale: false });
assert(label.textContent === 'plan 5h 25% - 60m left' || label.textContent === 'plan 5h 25% - 1h 0m left', 'lead chip: ' + label.textContent);
assert(/5h window: .+ to .+ \(/.test(label.title), 'tooltip window span: ' + label.title);
assert(/weekly: 3%/.test(label.title), 'tooltip weekly: ' + label.title);
assert(!/critical|warn/.test(label.className), 'no warn at 25%');

// Push warning: chip goes warn, one toast per window even when repeated.
const warn = { status: 'allowed_warning', rateLimitType: 'five_hour', utilization: 0.82, resetsAt: now + 3600, receivedAt: now + 1 };
sandbox.__acpRateLimit(warn);
sandbox.__acpRateLimit(warn);
assert(/^plan 5h 82% - /.test(label.textContent), 'push update: ' + label.textContent);
assert(/warn/.test(label.className), 'warn class');
assert(toasts.length === 1, 'one toast, got ' + toasts.length);

// Rejected weekly Opus: banner says model switch helps; clears when allowed again.
sandbox.__acpRateLimit({ status: 'rejected', rateLimitType: 'seven_day_opus', utilization: 1, resetsAt: now + 7200, receivedAt: now + 2 });
assert(bar.visible && /weekly Opus limit reached/.test(bar.msg) && /lets you keep working/.test(bar.msg), 'opus banner: ' + bar.msg);
assert(/critical/.test(label.className), 'critical class');
sandbox.__acpRateLimit({ status: 'allowed', rateLimitType: 'seven_day_opus', utilization: 0.1, resetsAt: now + 7200, receivedAt: now + 3 });
assert(!bar.visible, 'banner cleared');

// Rejected five-hour: model switch does not help.
sandbox.__acpRateLimit({ status: 'rejected', rateLimitType: 'five_hour', resetsAt: now + 600, receivedAt: now + 4 });
assert(/will not help/.test(bar.msg), 'five-hour banner: ' + bar.msg);

// Stale pull: last-known data with its age.
sandbox.applyPlanUsagePull({ windows: [
  { key: 'five_hour', label: 'five-hour', utilization: 40, resetsAt: null, resetsText: 'Oct 26, 5:49 PM CDT' },
], fetchedAt: now - 1500, stale: true, error: 'usage check rate limited until 6pm' });
assert(/\(25 min\)$/.test(label.textContent), 'stale age in chip: ' + label.textContent);
assert(/Last-known usage from 25 min ago \(usage check rate limited until 6pm\)/.test(label.title), 'stale tooltip: ' + label.title);
assert(/resets Oct 26, 5:49 PM CDT/.test(label.title), 'reset text fallback');

// Ended window: the old percentage is dropped and the tooltip says a new one starts on the next prompt.
sandbox.applyPlanUsagePull({ windows: [
  { key: 'five_hour', label: 'five-hour', utilization: 90, resetsAt: now - 60, resetsText: '' },
], fetchedAt: now, stale: false });
assert(label.textContent === 'plan 5h reset', 'ended chip: ' + label.textContent);
assert(/window ended .+ next prompt starts a new one/.test(label.title), 'ended tooltip: ' + label.title);
assert(!/critical|warn/.test(label.className), 'ended window not flagged');

// Mode chip.
sandbox.__acpMode({ id: 'bypassPermissions', name: 'Bypass Permissions' });
assert(el('session-mode').textContent === 'Bypass Permissions', 'mode chip');
sandbox.__acpMode({ id: null, name: null });
assert(el('session-mode').textContent === '', 'mode chip cleared');

console.log('plan chip + mode chip: ok');
