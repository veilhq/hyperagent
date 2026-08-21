/* === Hyperagent: Model Picker === */
/* Topbar dropdown that switches the active kiro-cli model via
   pywebview.api.set_model, and can pin a global default via
   pywebview.api.set_default_model.

   Backend contract:
   - Python pushes __acpModels({currentModelId, availableModels, defaultModelId})
     after session/new, session/load, session/set_model.
   - availableModels[i] = {modelId, name, description}
   - defaultModelId = value of kiro-cli's chat.defaultModel setting (global).
*/
(function () {
  "use strict";

  // Latest snapshot pushed from Python. Kept at module scope so the dropdown
  // can re-render on demand without another RPC round-trip.
  var latest = { currentModelId: null, availableModels: [], defaultModelId: null };

  // --- Grouping ---
  // Group models by family for scannability. The registry names are stable
  // enough to key off simple prefix matches. New families should fall into
  // "Other" until we add an explicit rule.
  function groupFor(id) {
    if (!id) return "Other";
    if (id === "auto") return "Auto";
    if (id.indexOf("claude-opus") === 0) return "Claude Opus";
    if (id.indexOf("claude-sonnet") === 0) return "Claude Sonnet";
    if (id.indexOf("claude-haiku") === 0) return "Claude Haiku";
    if (id.indexOf("gpt-") === 0) return "GPT";
    if (id.indexOf("deepseek") === 0) return "DeepSeek";
    if (id.indexOf("minimax") === 0) return "MiniMax";
    if (id.indexOf("glm") === 0) return "GLM";
    if (id.indexOf("qwen") === 0) return "Qwen";
    return "Other";
  }

  var GROUP_ORDER = [
    "Auto", "Claude Opus", "Claude Sonnet", "Claude Haiku",
    "GPT", "DeepSeek", "MiniMax", "GLM", "Qwen", "Other",
  ];

  // --- Rendering ---

  function shortName(modelId) {
    // The registry returns modelId and name; we display name if present, else id.
    var m = (latest.availableModels || []).find(function (x) { return x.modelId === modelId; });
    return (m && m.name) || modelId || "model";
  }

  function updateButtonLabel() {
    var btn = document.getElementById("model-picker-label");
    if (btn) btn.textContent = latest.currentModelId ? shortName(latest.currentModelId) : "model";
    var defEl = document.getElementById("model-picker-default");
    if (defEl) defEl.textContent = latest.defaultModelId || "—";
  }

  function renderDropdown() {
    var list = document.getElementById("model-picker-list");
    if (!list) return;
    list.innerHTML = "";

    var models = latest.availableModels || [];
    if (!models.length) {
      var empty = document.createElement("div");
      empty.className = "model-picker-group-header";
      empty.textContent = "no models available";
      list.appendChild(empty);
      return;
    }

    // Bucket by group
    var buckets = {};
    models.forEach(function (m) {
      var g = groupFor(m.modelId);
      (buckets[g] = buckets[g] || []).push(m);
    });

    GROUP_ORDER.forEach(function (g) {
      var items = buckets[g];
      if (!items || !items.length) return;
      var hdr = document.createElement("div");
      hdr.className = "model-picker-group-header";
      hdr.textContent = g;
      list.appendChild(hdr);
      items.forEach(function (m) { list.appendChild(renderItem(m)); });
    });
  }

  function renderItem(model) {
    var row = document.createElement("button");
    row.className = "model-picker-item";
    row.type = "button";
    row.dataset.modelId = model.modelId;
    if (model.modelId === latest.currentModelId) row.classList.add("is-current");

    var body = document.createElement("div");
    body.className = "model-picker-item-body";

    var name = document.createElement("span");
    name.className = "model-picker-item-name";
    name.textContent = model.name || model.modelId;
    body.appendChild(name);

    if (model.description) {
      var desc = document.createElement("span");
      desc.className = "model-picker-item-desc";
      desc.textContent = model.description;
      body.appendChild(desc);
    }

    row.appendChild(body);

    var meta = document.createElement("div");
    meta.className = "model-picker-item-meta";

    if (typeof model.rateMultiplier === "number") {
      var rate = document.createElement("span");
      rate.className = "model-picker-item-rate";
      var rm = model.rateMultiplier;
      // Tier the rate visually so expensive models stand out:
      //   <0.5   cheap    (accent — green by default)
      //   0.5-1.5 normal   (muted)
      //   1.5-2.0 warm     (amber)
      //   >=2.0  premium  (comp — red by default)
      var tier = "normal";
      if (rm < 0.5) tier = "cheap";
      else if (rm >= 2.0) tier = "premium";
      else if (rm >= 1.5) tier = "warm";
      rate.dataset.tier = tier;
      // ×2.2, ×0.25 etc. Format: strip trailing zeros without over-trimming.
      var rmText = (rm === Math.floor(rm)) ? rm.toString() : rm.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
      rate.textContent = "\u00d7" + rmText;
      rate.title = "Credit rate multiplier (" + tier + ")";
      meta.appendChild(rate);
    }

    if (model.modelId === latest.defaultModelId) {
      var badge = document.createElement("span");
      badge.className = "model-picker-item-default-badge";
      badge.textContent = "default";
      meta.appendChild(badge);
    } else {
      var setDefault = document.createElement("button");
      setDefault.className = "model-picker-set-default";
      setDefault.type = "button";
      setDefault.textContent = "set default";
      setDefault.title = "Make this the kiro-cli global default (chat.defaultModel)";
      setDefault.addEventListener("click", function (e) {
        e.stopPropagation();
        onSetDefault(model.modelId, setDefault);
      });
      meta.appendChild(setDefault);
    }

    row.appendChild(meta);

    row.addEventListener("click", function () { onPickModel(model.modelId); });
    return row;
  }

  // --- Actions ---

  function onPickModel(modelId) {
    if (!modelId || modelId === latest.currentModelId) {
      closeDropdown();
      return;
    }
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.set_model) {
      closeDropdown();
      return;
    }
    // Optimistic: update the button label immediately, Python will re-push
    // authoritative state (or an error toast) shortly.
    latest.currentModelId = modelId;
    updateButtonLabel();
    renderDropdown();
    closeDropdown();
    window.pywebview.api.set_model(modelId).catch(function (err) {
      if (window.HvToast) window.HvToast.error("Model switch failed: " + err);
    });
  }

  function onSetDefault(modelId, btnEl) {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.set_default_model) return;
    if (btnEl) { btnEl.disabled = true; btnEl.textContent = "..."; }
    window.pywebview.api.set_default_model(modelId).then(function (ok) {
      if (ok) {
        if (window.HvToast) window.HvToast.success("Default model set to " + shortName(modelId));
        // Backend broadcasts __acpModels with updated defaultModelId — no manual refresh needed.
      } else {
        if (window.HvToast) window.HvToast.error("Failed to update kiro-cli default");
        if (btnEl) { btnEl.disabled = false; btnEl.textContent = "set default"; }
      }
    }).catch(function (err) {
      if (window.HvToast) window.HvToast.error("Failed to update kiro-cli default: " + err);
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = "set default"; }
    });
  }

  // --- Dropdown open/close ---

  function isOpen() {
    var dd = document.getElementById("model-picker-dropdown");
    return dd && dd.classList.contains("open");
  }

  function openDropdown() {
    var dd = document.getElementById("model-picker-dropdown");
    var btn = document.getElementById("model-picker-btn");
    if (!dd || !btn) return;
    renderDropdown();
    dd.classList.add("open");
    btn.classList.add("model-picker-open");
    // Delay attaching the outside-click closer until after this click bubbles.
    setTimeout(function () {
      document.addEventListener("click", onDocClick, true);
      document.addEventListener("keydown", onKeydown, true);
    }, 0);
  }

  function closeDropdown() {
    var dd = document.getElementById("model-picker-dropdown");
    var btn = document.getElementById("model-picker-btn");
    if (dd) dd.classList.remove("open");
    if (btn) btn.classList.remove("model-picker-open");
    document.removeEventListener("click", onDocClick, true);
    document.removeEventListener("keydown", onKeydown, true);
  }

  function onDocClick(e) {
    var host = document.getElementById("model-picker");
    if (host && !host.contains(e.target)) closeDropdown();
  }

  function onKeydown(e) {
    if (e.key === "Escape") { closeDropdown(); }
  }

  window.toggleModelPicker = function (e) {
    if (e && e.stopPropagation) e.stopPropagation();
    if (isOpen()) { closeDropdown(); } else { openDropdown(); }
  };

  // --- Backend event handler ---

  window.__acpModels = function (data) {
    if (!data || typeof data !== "object") return;
    latest.currentModelId = data.currentModelId || latest.currentModelId;
    if (Array.isArray(data.availableModels) && data.availableModels.length) {
      latest.availableModels = data.availableModels;
    }
    // defaultModelId may be null if kiro-cli settings read failed — preserve prior value in that case.
    if (data.defaultModelId != null) latest.defaultModelId = data.defaultModelId;
    updateButtonLabel();
    if (isOpen()) renderDropdown();
  };

  // --- Initial seed ---
  // If the module mounts after Python has already pushed __acpModels for the
  // active tab (e.g. after a live-reload of the frontend), pull the latest
  // state from the bridge so the button doesn't sit on "model" placeholder.
  function seed() {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_models) return;
    window.pywebview.api.get_models().then(function (state) {
      if (state) window.__acpModels(state);
    }).catch(function () { /* ignore */ });
  }

  // Wait for both DOM and pywebview bridge before seeding. The bridge attaches
  // asynchronously — the shared 00-core.js pattern is to poll for pywebview.api.
  function whenReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  whenReady(function () {
    updateButtonLabel();
    // Poll briefly for pywebview.api (attaches after webview ready)
    var tries = 0;
    var iv = setInterval(function () {
      tries++;
      if (window.pywebview && window.pywebview.api && window.pywebview.api.get_models) {
        clearInterval(iv);
        seed();
      } else if (tries > 40) {
        clearInterval(iv);
      }
    }, 250);
  });
})();
