/* === Hyperagent: Changelog Dropdown === */
/* Badge-anchored dropdown that shows the running kiro-cli release notes.
   Anchored to the #cli-version badge in the topbar status cluster; always
   clickable once a version is known.

   Backend contract:
   - pywebview.api.get_changelog(version) -> {version, date, entries:[...]} | null
     entries[i] = {type: "Added"|"Changed"|"Fixed"|"Security"|"Deprecated",
                   tag: "V3"|null, text: "..."}
   - The CLI version itself arrives via __acpCliVersion (09-activity.js), which
     populates window._kiroCliVersion and the #cli-version badge text.
*/
(function () {
  "use strict";

  // Cache parsed changelog per version so re-opening the dropdown is instant
  // and doesn't re-hit the bridge. Changelog content is immutable per release.
  var cache = {};
  var lastRenderedVersion = null;

  // Category display order — mirrors kiro-cli's own changelog grouping.
  var TYPE_ORDER = ["Added", "Changed", "Fixed", "Security", "Deprecated"];

  // --- Rendering ---

  function renderEntries(data) {
    var list = document.getElementById("cli-version-list");
    var meta = document.getElementById("cli-version-header-meta");
    if (!list) return;
    list.innerHTML = "";

    if (meta) {
      var metaText = data && data.version ? "v" + data.version : "";
      if (data && data.date) metaText += metaText ? " \u00b7 " + data.date : data.date;
      meta.textContent = metaText;
    }

    if (!data || !Array.isArray(data.entries) || !data.entries.length) {
      var empty = document.createElement("div");
      empty.className = "cli-version-empty";
      empty.textContent = "No release notes available.";
      list.appendChild(empty);
      return;
    }

    // Bucket entries by category, preserving source order within a category.
    var buckets = {};
    data.entries.forEach(function (e) {
      (buckets[e.type] = buckets[e.type] || []).push(e);
    });

    TYPE_ORDER.forEach(function (type) {
      var items = buckets[type];
      if (!items || !items.length) return;
      var hdr = document.createElement("div");
      hdr.className = "cli-version-group-header";
      hdr.dataset.type = type;
      hdr.textContent = type;
      list.appendChild(hdr);
      items.forEach(function (e) { list.appendChild(renderItem(e)); });
    });
  }

  function renderItem(entry) {
    var row = document.createElement("div");
    row.className = "cli-version-item";
    row.dataset.type = entry.type;

    if (entry.tag) {
      var tag = document.createElement("span");
      tag.className = "cli-version-item-tag";
      tag.textContent = entry.tag;
      row.appendChild(tag);
    }

    var text = document.createElement("span");
    text.className = "cli-version-item-text";
    text.textContent = entry.text || "";
    row.appendChild(text);
    return row;
  }

  function renderLoading() {
    var list = document.getElementById("cli-version-list");
    if (!list) return;
    list.innerHTML = "";
    var loading = document.createElement("div");
    loading.className = "cli-version-empty";
    loading.textContent = "Loading release notes\u2026";
    list.appendChild(loading);
  }

  // --- Fetch ---

  function loadChangelog() {
    var version = window._kiroCliVersion || "";
    // Serve from cache if we already fetched this version's notes.
    if (version && cache[version]) {
      renderEntries(cache[version]);
      lastRenderedVersion = version;
      return;
    }
    // Avoid a redundant re-render/refetch when reopening on the same version.
    if (version && lastRenderedVersion === version && cache[version]) return;

    renderLoading();
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_changelog) {
      renderEntries(null);
      return;
    }
    window.pywebview.api.get_changelog(version || null).then(function (data) {
      var key = (data && data.version) || version;
      if (key) cache[key] = data;
      lastRenderedVersion = key || null;
      renderEntries(data);
    }).catch(function (err) {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.debug_log) {
        window.pywebview.api.debug_log("get_changelog failed: " + err);
      }
      renderEntries(null);
    });
  }

  // --- Dropdown open/close (behavior via HvDisclosure) ---

  var changelog = HvDisclosure.register({
    trigger: "cli-version",
    panel: "cli-version-dropdown",
    host: "cli-version-picker",
    triggerOpenClass: "cli-version-open",
    onOpen: function (els) {
      // Opening acknowledges the release notes - drop the "what's new" cue.
      // Python persists the acknowledgment when get_changelog runs.
      if (els.trigger) els.trigger.classList.remove("has-new");
      loadChangelog();
    }
  });

  window.toggleChangelog = function (e) {
    if (e && e.stopPropagation) e.stopPropagation();
    // No version yet means kiro-cli hasn't reported one - nothing to show.
    if (!window._kiroCliVersion) return;
    changelog.toggle();
  };
})();
