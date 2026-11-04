/* === Hyperagent: Skills Picker === */
/* One topbar chip counting the active tab's loaded skills; its dropdown lists
   each skill with its description. Reuses the model picker's chip and dropdown
   styling. The chip is hidden until a skill loads.

   Data: `activeSkills` (02-handlers.js), name -> {name, desc}, per tab. The
   chip is redrawn through _syncSkillsToActiveTab(pulse); pulse=true flashes
   the chip border to mark a fresh activation. */
(function () {
  "use strict";

  function render() {
    var list = document.getElementById("skills-picker-list");
    if (!list) return;
    list.innerHTML = "";
    Object.keys(activeSkills).forEach(function (key) {
      var skill = activeSkills[key];
      var row = document.createElement("div");
      row.className = "skills-picker-item";
      row.setAttribute("data-testid", "skill-item-" + skill.name);
      var name = document.createElement("span");
      name.className = "model-picker-item-name";
      name.textContent = skill.name;
      row.appendChild(name);
      if (skill.desc) {
        var desc = document.createElement("span");
        desc.className = "model-picker-item-desc";
        desc.textContent = skill.desc;
        row.appendChild(desc);
      }
      list.appendChild(row);
    });
  }

  var picker = HvDisclosure.register({
    trigger: "skills-picker-btn",
    panel: "skills-picker-dropdown",
    host: "skills-picker",
    triggerOpenClass: "model-picker-open",
    onOpen: function () { render(); }
  });

  window.toggleSkillsPicker = function (e) {
    if (e && e.stopPropagation) e.stopPropagation();
    picker.toggle();
  };

  window._syncSkillsToActiveTab = function (pulse) {
    var count = Object.keys(activeSkills).length;
    var host = document.getElementById("skills-picker");
    if (host) host.hidden = count === 0;
    var label = document.getElementById("skills-picker-label");
    if (label) label.textContent = count + (count === 1 ? " skill" : " skills");
    if (count === 0 && picker.isOpen()) picker.close();
    if (picker.isOpen()) render();
    var btn = document.getElementById("skills-picker-btn");
    if (pulse && btn) {
      btn.classList.remove("skills-picker-pulse");
      void btn.offsetWidth;
      btn.classList.add("skills-picker-pulse");
    }
  };
})();
