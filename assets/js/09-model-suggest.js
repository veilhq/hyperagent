/* === Hyperagent: Skill-based model-switch suggestion === */
/* When a skill loads whose mapped tier differs from the active model (Claude
   backend only), Python pushes __acpModelSuggestion. This surfaces a sticky
   toast with a one-click switch. It never switches on its own - a mid-session
   model change forces a one-turn prompt-cache rebuild, so the user decides. */
(function () {
  "use strict";

  window.__acpModelSuggestion = function (data) {
    if (!data || !data.targetModelId || !window.HvToast) return;

    // Only the active tab prompts - a background tab's suggestion would switch
    // a session the user isn't looking at.
    if (data._tabId && typeof activeTabId !== "undefined" && data._tabId !== activeTabId) {
      return;
    }

    var api = window.pywebview && window.pywebview.api;
    if (!api || !api.set_model) return;

    var target = data.targetModelId;
    var targetName = data.targetName || target;
    var skill = data.skill || "this skill";
    var current = data.currentName || "the current model";

    window.HvToast.show({
      variant: "info",
      message: skill + " works best on " + targetName + " (now on " + current + ")",
      duration: "sticky",
      dedupeKey: "model-suggest-" + target,
      action: {
        label: "Switch to " + targetName,
        onClick: function () {
          api.set_model(target).catch(function () {
            if (window.HvToast) {
              window.HvToast.show({ variant: "error", message: "Model switch failed" });
            }
          });
        }
      }
    });
  };
})();
