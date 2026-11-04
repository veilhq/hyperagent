// Exercises the skills chip in 09-skills-picker.js against a stubbed DOM.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const els = {};
function el(id) {
  if (!els[id]) {
    const cls = new Set();
    els[id] = {
      id, textContent: '', hidden: false, children: [], offsetWidth: 0, className: '',
      classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c) },
      setAttribute() {}, appendChild(c) { this.children.push(c); },
      set innerHTML(v) { this.children = []; }, get innerHTML() { return ''; },
    };
  }
  return els[id];
}
let open = false;
const sandbox = {
  console,
  document: { getElementById: el, createElement: () => el('c' + Math.random()) },
  HvDisclosure: { register: (cfg) => ({
    toggle() { open = !open; if (open && cfg.onOpen) cfg.onOpen(); },
    close() { open = false; }, isOpen: () => open,
  }) },
  activeSkills: {},
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'assets', 'js', '09-skills-picker.js'), 'utf8'), sandbox);

function assert(cond, msg) { if (!cond) { console.error('FAIL: ' + msg); process.exit(1); } }
const host = el('skills-picker');
const label = el('skills-picker-label');
const btn = el('skills-picker-btn');

sandbox._syncSkillsToActiveTab();
assert(host.hidden === true, 'hidden with no skills');

sandbox.activeSkills = { 'code-changes': { name: 'code-changes', desc: 'Read before writing code' } };
sandbox._syncSkillsToActiveTab(true);
assert(host.hidden === false && label.textContent === '1 skill', 'one skill: ' + label.textContent);
assert(btn.classList.contains('skills-picker-pulse'), 'pulse on fresh activation');

sandbox.activeSkills['debugging'] = { name: 'debugging', desc: '' };
sandbox._syncSkillsToActiveTab();
assert(label.textContent === '2 skills', 'two skills: ' + label.textContent);

sandbox.toggleSkillsPicker();
const rows = el('skills-picker-list').children;
assert(rows.length === 2, 'dropdown lists both skills, got ' + rows.length);
assert(rows[0].children.length === 2 && rows[1].children.length === 1, 'description only when present');

// Tab switch to a tab with no skills hides the chip and closes the dropdown.
sandbox.activeSkills = {};
sandbox._syncSkillsToActiveTab();
assert(host.hidden === true && open === false, 'cleared on empty tab');

console.log('skills chip: ok');
