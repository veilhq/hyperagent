/* === Hyperagent: Backend Picker === */
/* Topbar dropdown that selects the agent backend (kiro-cli or Claude Code).
   The backend is resolved once per app process, so a change is saved via
   pywebview.api.set_backend and applied by pywebview.api.restart_app.
   Reuses the model picker's chip and dropdown styling. */
(function () {
  "use strict";

  var info = null;

  function api() {
    return window.pywebview && window.pywebview.api;
  }

  function updateLabel() {
    var label = document.getElementById("backend-picker-label");
    if (!label || !info) return;
    var pending = info.selected !== info.active;
    label.textContent = info.activeName + (pending ? " *" : "");
    window._activeBackendName = info.activeName;
    var ver = document.getElementById("cli-version");
    var vtip = ver && ver.getAttribute("data-tooltip");
    if (vtip) ver.setAttribute("data-tooltip", vtip.replace(/^[^v]*(?= ?v\d)/, info.activeName + " CLI"));
    var btn = document.getElementById("backend-picker-btn");
    if (btn) {
      var tip = pending
        ? "Agent backend - restart to switch to " + nameFor(info.selected)
        : "Agent backend";
      btn.setAttribute("data-tooltip", tip);
      btn.setAttribute("aria-label", tip);
    }
  }

  function nameFor(id) {
    var opt = ((info && info.options) || []).find(function (o) { return o.id === id; });
    return (opt && opt.name) || id;
  }

  function render() {
    var list = document.getElementById("backend-picker-list");
    if (!list || !info) return;
    list.innerHTML = "";
    info.options.forEach(function (opt) {
      var row = document.createElement("button");
      row.type = "button";
      row.className = "model-picker-item";
      row.dataset.backendId = opt.id;
      row.setAttribute("data-testid", "backend-option-" + opt.id);
      if (opt.id === info.selected) row.classList.add("is-current");

      var body = document.createElement("div");
      body.className = "model-picker-item-body";
      var name = document.createElement("span");
      name.className = "model-picker-item-name";
      name.textContent = opt.name;
      body.appendChild(name);
      if (opt.description) {
        var desc = document.createElement("span");
        desc.className = "model-picker-item-desc";
        desc.textContent = opt.description;
        body.appendChild(desc);
      }
      row.appendChild(body);

      var meta = document.createElement("div");
      meta.className = "model-picker-item-meta";
      if (opt.id === info.active) {
        var badge = document.createElement("span");
        badge.className = "label-badge model-picker-item-default-badge";
        badge.textContent = "running";
        meta.appendChild(badge);
      }
      row.appendChild(meta);

      row.addEventListener("click", function () { onPick(opt.id); });
      list.appendChild(row);
    });

    if (info.selected !== info.active) {
      var restart = document.createElement("button");
      restart.type = "button";
      restart.className = "model-picker-item";
      restart.setAttribute("data-testid", "restart-backend-button");
      var rbody = document.createElement("div");
      rbody.className = "model-picker-item-body";
      var rname = document.createElement("span");
      rname.className = "model-picker-item-name";
      rname.textContent = "Restart now to use " + nameFor(info.selected);
      rbody.appendChild(rname);
      restart.appendChild(rbody);
      restart.addEventListener("click", onRestart);
      list.appendChild(restart);
    }
  }

  function onPick(id) {
    var a = api();
    if (!a || !a.set_backend || !info || id === info.selected) return;
    a.set_backend(id).then(function (res) {
      if (!res || !res.ok) {
        if (window.HvToast) window.HvToast.error((res && res.error) || "Backend switch failed");
        return;
      }
      info.selected = id;
      updateLabel();
      render();
    }).catch(function (err) {
      if (window.HvToast) window.HvToast.error("Backend switch failed: " + err);
    });
  }

  function onRestart() {
    var a = api();
    if (a && a.restart_app) a.restart_app();
  }

  var picker = HvDisclosure.register({
    trigger: "backend-picker-btn",
    panel: "backend-picker-dropdown",
    host: "backend-picker",
    triggerOpenClass: "model-picker-open",
    onOpen: function () { render(); }
  });

  window.toggleBackendPicker = function (e) {
    if (e && e.stopPropagation) e.stopPropagation();
    picker.toggle();
  };

  function seed() {
    api().get_backend_info().then(function (data) {
      if (!data) return;
      info = data;
      updateLabel();
    }).catch(function () { /* bridge not ready; label stays as placeholder */ });
  }

  function start() {
    var tries = 0;
    var iv = setInterval(function () {
      tries++;
      var a = api();
      if (a && a.get_backend_info) {
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
