"""Model and effort-level methods for ACPClient."""

from helpers import logger, update_prefs


class ModelEffortMixin:
    """Model picker and effort picker behavior; relies on ACPClient state."""

    def _capture_effort(self, config_options):
        """Track the effort option ({current, options}) from configOptions and push it.

        A configOptions list without the option means the current model has no
        effort setting, which hides the picker.
        """
        option_id = self._backend.effort_config_option
        if not option_id or not isinstance(config_options, list):
            return
        option = next((o for o in config_options if isinstance(o, dict) and o.get("id") == option_id), None)
        if option:
            self._effort = {
                "current": option.get("currentValue"),
                "options": [{"value": o.get("value"), "name": o.get("name") or o.get("value")}
                            for o in option.get("options") or [] if isinstance(o, dict) and o.get("value")],
            }
        else:
            self._effort = None
        self._push_effort_event()

    def _push_effort_event(self):
        """Push the current effort state; empty options hide the picker."""
        effort = self._effort or {"current": None, "options": []}
        self._push_js("__acpEffort", {
            "current": effort["current"],
            "options": effort["options"],
            "ultracode": {"supported": bool(self._backend.ultracode_keyword), "on": self._ultracode,
                          **self._ultracode_limits()},
        })

    def _apply_preferred_effort(self):
        """After session setup, apply the last effort level picked under this backend."""
        if not self._effort or not self._session_id:
            return
        preferred = self._load_prefs().get(self._backend.effort_pref_key)
        if not preferred or preferred == self._effort["current"]:
            return
        if not any(o["value"] == preferred for o in self._effort["options"]):
            logger.debug("%spreferred effort %s not offered for this model, skipping", self._tab_ctx(), preferred)
            return
        logger.info("%sapplying preferred effort: %s (was %s)", self._tab_ctx(), preferred, self._effort["current"])
        self._request_set_effort(preferred, remember=False)

    def _request_set_effort(self, value, remember=True):
        """Send session/set_config_option for effort and refresh state from the reply."""
        def on_set(result):
            if isinstance(result, dict) and "error" in result:
                err = result["error"]
                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                logger.warning("%sset_effort(%s) failed: %s", self._tab_ctx(), value, err_msg)
                self._push_js("__acpError", {"error": f"Effort change failed: {err_msg}"})
                self._push_effort_event()
                return
            logger.info("%seffort set: %s", self._tab_ctx(), value)
            if remember:
                update_prefs(lambda prefs: prefs.__setitem__(self._backend.effort_pref_key, value))
            if isinstance(result, dict) and isinstance(result.get("configOptions"), list):
                self._capture_config_options(result["configOptions"])
            elif self._effort:
                self._effort["current"] = value
                self._push_effort_event()

        self._request("session/set_config_option", {
            "sessionId": self._session_id,
            "configId": self._backend.effort_config_option,
            "value": value,
        }, on_set)

    def set_effort(self, value):
        """Public entry point from the bridge API."""
        if not value or not self._backend.effort_config_option:
            return False
        if not self._session_id:
            logger.warning("%sset_effort called with no active session", self._tab_ctx())
            return False
        self._request_set_effort(value, remember=True)
        return True

    def _models_from_config_options(self, config_options):
        """Map a select-type model config option to the {currentModelId,
        availableModels} shape the model picker consumes."""
        option_id = self._backend.model_config_option
        if not option_id or not isinstance(config_options, list):
            return None
        option = next((o for o in config_options
                       if isinstance(o, dict) and o.get("id") == option_id), None)
        if not option:
            return None
        available = [
            {"modelId": o.get("value"), "name": o.get("name") or o.get("value"),
             "description": o.get("description") or ""}
            for o in option.get("options") or [] if isinstance(o, dict) and o.get("value")
        ]
        return {"currentModelId": option.get("currentValue"), "availableModels": available}

    def _push_models_event(self):
        """Push the current model state to the frontend."""
        rates = self._backend.model_rates()
        enriched = []
        for m in (self._available_models or []):
            mid = m.get("modelId") if isinstance(m, dict) else None
            if mid and mid in rates:
                m2 = dict(m)
                m2["rateMultiplier"] = rates[mid]
                enriched.append(m2)
            else:
                enriched.append(m)
        self._push_js("__acpModels", {
            "currentModelId": self._current_model_id,
            "availableModels": enriched,
            "defaultModelId": self._backend.read_default_model(),
        })

    def _apply_preferred_model(self):
        """After session setup, apply stored hyperagent-level defaults (effort, then model) if they differ."""
        self._apply_preferred_effort()
        preferred = self._load_prefs().get(self._backend.model_pref_key)
        if not preferred or preferred == self._current_model_id:
            return
        if not self._session_id:
            return
        if not any(m.get("modelId") == preferred for m in (self._available_models or [])):
            logger.debug("%spreferred model %s not in available list, skipping", self._tab_ctx(), preferred)
            return
        logger.info("%sapplying preferred model: %s (was %s)", self._tab_ctx(), preferred, self._current_model_id)
        self._request_set_model(preferred, remember=False)

    def _request_set_model(self, model_id, remember=True):
        """Send session/set_model and update local state on success."""
        if not self._session_id:
            return

        def on_set(result):
            if isinstance(result, dict) and "error" in result:
                err = result["error"]
                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                logger.warning("%sset_model(%s) failed: %s", self._tab_ctx(), model_id, err_msg)
                self._push_js("__acpError", {"error": f"Model switch failed: {err_msg}"})
                self._push_models_event()
                return
            logger.info("%smodel switched: %s", self._tab_ctx(), model_id)
            self._current_model_id = model_id
            if remember:
                update_prefs(lambda prefs: prefs.__setitem__(self._backend.model_pref_key, model_id))
            self._push_models_event()
            # A model switch can change which effort levels are offered.
            if isinstance(result, dict):
                self._capture_effort(result.get("configOptions"))

        option_id = self._backend.model_config_option
        if option_id:
            self._request("session/set_config_option", {
                "sessionId": self._session_id,
                "configId": option_id,
                "value": model_id,
            }, on_set)
            return
        self._request("session/set_model", {
            "sessionId": self._session_id,
            "modelId": model_id,
        }, on_set)

    def set_model(self, model_id):
        """Public entry point from the bridge API."""
        if not model_id:
            return False
        if not self._session_id:
            logger.warning("%sset_model called with no active session", self._tab_ctx())
            return False
        self._request_set_model(model_id, remember=True)
        return True

    def _resolve_model_tier(self, tier):
        """Resolve a tier alias (e.g. 'opus') to an available model id.

        A value that already matches an available model id is used as-is;
        otherwise the alias is substring-matched against the ids.
        """
        ids = [m.get("modelId") for m in (self._available_models or []) if m.get("modelId")]
        if tier in ids:
            return tier
        tier_lower = tier.lower()
        matches = [mid for mid in ids if tier_lower in mid.lower()]
        # Prefer the newest version when several ids match (claude-opus-4-8 < claude-opus-5).
        return max(matches) if matches else None

    def _model_display_name(self, model_id):
        if not model_id:
            return "current model"
        for m in self._available_models or []:
            if m.get("modelId") == model_id:
                return m.get("name") or model_id
        return model_id
