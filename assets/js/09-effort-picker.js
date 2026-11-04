/* === Hyperagent: Effort Picker === */
/* Chip-row dropdown beside the model picker that sets the session's effort
   level via pywebview.api.set_effort. Reuses the model picker's chip and
   dropdown styling.

   Backend contract:
   - Python pushes __acpEffort({current, options, _tabId}) after session
     new/load, model switches, effort changes, config updates, and tab switches.
   - options[i] = {value, name}; an empty list means the current agent/model has
     no effort setting, which hides the picker. */
(function () {
  "use strict";

  var latest = { current: null, options: [], ultracode: { supported: false, on: false, mode: "once", budget: 150000, agentCap: 5, threshold: 70 } };

  // Values each ultracode limit row cycles through on click.
  var LIMIT_PRESETS = {
    budget: [50000, 100000, 150000, 300000, 500000],
    agentCap: [2, 3, 5, 10, 20],
    threshold: [50, 60, 70, 80, 90, 100]
  };

  function api() {
    return window.pywebview && window.pywebview.api;
  }

  function nameFor(value) {
    var opt = latest.options.find(function (o) { return o.value === value; });
    return (opt && opt.name) || value || "effort";
  }

  // Window-level ultracode marks: the app root, the status chip, and (set per
  // tab in __acpEffort) the sidebar tab. All follow the active tab's state.
  function applyWindowState() {
    var on = !!latest.ultracode.on;
    var app = document.getElementById("app");
    if (app) app.classList.toggle("ultracode", on);
    var chip = document.getElementById("status-ultracode");
    if (chip) chip.hidden = !on;
  }

  function updateChip() {
    var host = document.getElementById("effort-picker");
    if (host) host.hidden = !latest.options.length;
    var label = document.getElementById("effort-picker-label");
    if (label) {
      var text = latest.current ? nameFor(latest.current) : "effort";
      var suffix = latest.ultracode.mode === "once" ? " + ultracode x1" : " + ultracode";
      label.textContent = latest.ultracode.on ? text + suffix : text;
    }
    applyWindowState();
  }

  function render() {
    var list = document.getElementById("effort-picker-list");
    if (!list) return;
    list.innerHTML = "";
    latest.options.forEach(function (opt) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "model-picker-item";
      row.setAttribute("data-testid", "effort-option-" + opt.value);
      if (opt.value === latest.current) row.classList.add("is-current");
      var body = document.createElement("div");
      body.className = "model-picker-item-body";
      var name = document.createElement("span");
      name.className = "model-picker-item-name";
      name.textContent = opt.name;
      body.appendChild(name);
      row.appendChild(body);
      row.addEventListener("click", function () { onPick(opt.value); });
      list.appendChild(row);
    });
    if (latest.ultracode.supported) {
      list.appendChild(ultracodeRow());
      limitRows().forEach(function (row) { list.appendChild(row); });
    }
  }

  function pickerRow(testId, nameText, descText, onClick) {
    var row = document.createElement("button");
    row.type = "button";
    row.className = "model-picker-item";
    row.setAttribute("data-testid", testId);
    var body = document.createElement("div");
    body.className = "model-picker-item-body";
    var name = document.createElement("span");
    name.className = "model-picker-item-name";
    name.textContent = nameText;
    var desc = document.createElement("span");
    desc.className = "model-picker-item-desc";
    desc.textContent = descText;
    body.appendChild(name);
    body.appendChild(desc);
    row.appendChild(body);
    row.addEventListener("click", onClick);
    return row;
  }

  function formatTokens(n) {
    return "+" + Math.round(n / 1000) + "k";
  }

  // Click-to-cycle rows for the ultracode limits; each change is saved as the default for new tabs.
  function limitRows() {
    var uc = latest.ultracode;
    return [
      pickerRow("effort-ultracode-mode", uc.mode === "once" ? "arms one prompt" : "stays armed",
        "click to switch; one prompt disarms after sending",
        function () { setLimit({ mode: uc.mode === "once" ? "stay" : "once" }); }),
      pickerRow("effort-ultracode-budget", "budget " + formatTokens(uc.budget) + " tokens",
        "token target sent with the prompt; click to change",
        function () { setLimit({ budget: nextPreset("budget", uc.budget) }); }),
      pickerRow("effort-ultracode-cap", "agent cap " + uc.agentCap,
        "most agents a workflow may launch; click to change",
        function () { setLimit({ agentCap: nextPreset("agentCap", uc.agentCap) }); }),
      pickerRow("effort-ultracode-guard", "guard at " + uc.threshold + "% plan usage",
        "ultracode will not arm above this; click to change",
        function () { setLimit({ threshold: nextPreset("threshold", uc.threshold) }); })
    ];
  }

  function nextPreset(key, current) {
    var list = LIMIT_PRESETS[key];
    var next = list.find(function (v) { return v > current; });
    return next === undefined ? list[0] : next;
  }

  function setLimit(change) {
    var a = api();
    if (!a || !a.set_ultracode_limits) return;
    // Optimistic: Python re-pushes the authoritative limits.
    Object.keys(change).forEach(function (k) { latest.ultracode[k] = change[k]; });
    updateChip();
    if (picker.isOpen()) render();
    a.set_ultracode_limits(change.mode || null, change.budget || null, change.agentCap || null, change.threshold || null)
      .catch(function (err) {
        if (window.HvToast) window.HvToast.error("Ultracode limit change failed: " + err);
      });
  }

  // Toggle row under the effort levels: ultracode is independent of the level.
  function ultracodeRow() {
    var row = document.createElement("button");
    row.type = "button";
    row.className = "model-picker-item effort-ultracode-row";
    row.setAttribute("data-testid", "effort-option-ultracode");
    row.setAttribute("aria-pressed", latest.ultracode.on ? "true" : "false");
    if (latest.ultracode.on) row.classList.add("is-current");
    var body = document.createElement("div");
    body.className = "model-picker-item-body";
    var name = document.createElement("span");
    name.className = "model-picker-item-name";
    name.textContent = "ultracode " + (latest.ultracode.on ? "on" : "off");
    var desc = document.createElement("span");
    desc.className = "model-picker-item-desc";
    desc.textContent = "multi-agent orchestration, sent with each prompt in this tab";
    body.appendChild(name);
    body.appendChild(desc);
    row.appendChild(body);
    row.addEventListener("click", function () { onToggleUltracode(!latest.ultracode.on); });
    return row;
  }

  function onToggleUltracode(on) {
    picker.close();
    var a = api();
    if (!a || !a.set_ultracode) return;
    // Optimistic: Python re-pushes the authoritative state.
    latest.ultracode.on = on;
    updateChip();
    // A refused change (usage guard) shows its own notice and the pushed state reverts the chip.
    a.set_ultracode(on).catch(function (err) {
      if (window.HvToast) window.HvToast.error("Ultracode change failed: " + err);
    });
  }

  // !: flip ultracode for the active tab when not typing (no-op where unsupported).
  document.addEventListener("keydown", function (e) {
    if (e.key !== "!" || e.ctrlKey || e.altKey || e.metaKey) return;
    var ae = document.activeElement;
    if (ae && (ae.tagName === "TEXTAREA" || ae.tagName === "INPUT" || ae.isContentEditable)) return;
    e.preventDefault();
    if (latest.ultracode.supported) onToggleUltracode(!latest.ultracode.on);
  });

  function onPick(value) {
    picker.close();
    var a = api();
    if (!a || !a.set_effort || value === latest.current) return;
    // Optimistic: Python re-pushes the authoritative state (or an error toast).
    latest.current = value;
    updateChip();
    a.set_effort(value).then(function (ok) {
      if (!ok && window.HvToast) window.HvToast.error("Effort change failed");
    }).catch(function (err) {
      if (window.HvToast) window.HvToast.error("Effort change failed: " + err);
    });
  }

  var picker = HvDisclosure.register({
    trigger: "effort-picker-btn",
    panel: "effort-picker-dropdown",
    host: "effort-picker",
    triggerOpenClass: "model-picker-open",
    onOpen: function () { render(); }
  });

  window.toggleEffortPicker = function (e) {
    if (e && e.stopPropagation) e.stopPropagation();
    picker.toggle();
  };

  window.__acpEffort = function (data) {
    if (!data || typeof data !== "object") return;
    var uc = data.ultracode || {};
    // The sidebar tab marks ultracode whichever tab is active.
    if (data._tabId && typeof tabs !== "undefined" && tabs[data._tabId] && tabs[data._tabId].el) {
      tabs[data._tabId].el.classList.toggle("ultracode", !!uc.on);
    }
    // Each tab has its own effort; only the active tab's state drives the chip.
    if (data._tabId && typeof activeTabId !== "undefined" && activeTabId && data._tabId !== activeTabId) return;
    latest.current = data.current || null;
    latest.options = Array.isArray(data.options) ? data.options : [];
    latest.ultracode = {
      supported: !!uc.supported, on: !!uc.on, mode: uc.mode || "once",
      budget: uc.budget || latest.ultracode.budget, agentCap: uc.agentCap || latest.ultracode.agentCap,
      threshold: uc.threshold || latest.ultracode.threshold
    };
    updateChip();
    if (picker.isOpen()) render();
  };

  function seed() {
    api().get_effort().then(function (state) {
      if (state) window.__acpEffort(state);
    }).catch(function () { /* bridge not ready; chip stays hidden */ });
  }

  function start() {
    updateChip();
    var tries = 0;
    var iv = setInterval(function () {
      tries++;
      var a = api();
      if (a && a.get_effort) {
        clearInterval(iv);
        seed();
      } else if (tries > 40) {
        clearInterval(iv);
      }
    }, 250);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
