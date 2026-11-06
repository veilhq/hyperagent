"""Checks ACPClient keeps its full method set across the mixin split, with no method defined twice."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import acp_client  # noqa: E402
from acp_models import ModelEffortMixin  # noqa: E402
from acp_ultracode import UltracodeMixin  # noqa: E402

EXPECTED = [
    "__init__", "_tab_ctx", "set_window", "state",
    "start_process", "connect", "start", "_is_self_update_exit",
    "_auto_recover", "_handle_update_restart", "_capture_changelog_safe", "_reload_session_after_update",
    "stop", "_kill_process_tree", "_next_id", "_send",
    "_request", "_expire_request", "_notify", "request_sync",
    "_initialize", "_notice_slow_start", "_on_start_timeout", "_new_session",
    "_set_session_id", "_compaction_transcript", "_baseline_compaction", "_check_compaction",
    "_schedule_compaction_check", "_recheck_compaction", "_reap_scratch_session", "_on_session",
    "_build_prompt_blocks", "prompt", "_on_prompt_done", "_persist_turn_end",
    "_attempt_session_reload", "cancel", "new_session", "_read_stdout",
    "_drain_stderr", "_run_callback", "_dispatch", "_handle_server_request",
    "_hold_workflow_plan", "answer_workflow_plan", "_cancel_workflow_plans", "_detect_skill_activation",
    "_parse_questions", "_hold_question", "answer_question", "_cancel_questions",
    "_normalize_commands", "_capture_commands", "_push_update_available",
    "_push_skill_activation", "_maybe_suggest_model", "_resolve_model_tier", "_model_display_name",
    "_track_file_change", "_push_js", "_push_js_throttled", "_emit_file_change_event",
    "_push_state", "_push_cli_version_badge", "_load_prefs", "_load_session_id",
    "_save_session_id", "_register_owned_session", "_unregister_owned_session", "_clear_session_id",
    "_capture_and_push_models", "_capture_config_options", "_capture_context_usage", "_capture_rate_limit",
    "_warn_unhandled_update", "_capture_agent_title", "_capture_modes", "_set_mode",
    "_push_mode_event", "_merge_prompt_usage", "_capture_effort", "_push_effort_event",
    "_ultracode_limits", "set_ultracode_limits", "_capture_async_task", "_push_workflow_card",
    "_log_async_task", "_watch_async_task", "stop_async_task", "_plan_readings",
    "_ultracode_guard", "_push_guard_notice", "set_ultracode", "_apply_preferred_effort",
    "_request_set_effort", "set_effort", "_models_from_config_options", "_apply_session_mode",
    "_push_models_event", "_apply_preferred_model", "_request_set_model", "set_model",
]


def own_methods(cls):
    return {k for k, v in vars(cls).items() if callable(v) or isinstance(v, (staticmethod, classmethod))}


def main():
    cls = acp_client.ACPClient
    assert cls.__mro__[1:3] == (ModelEffortMixin, UltracodeMixin), cls.__mro__
    missing = [n for n in EXPECTED if not hasattr(cls, n)]
    assert not missing, "missing methods: %s" % missing
    sets = [own_methods(c) - {"__init__"} for c in (cls, ModelEffortMixin, UltracodeMixin)]
    for i in range(3):
        for j in range(i + 1, 3):
            dup = sets[i] & sets[j]
            assert not dup, "defined in two classes: %s" % sorted(dup)
    extra = (sets[0] | sets[1] | sets[2]) - set(EXPECTED)
    assert not extra, "unexpected methods: %s" % sorted(extra)
    print("acp methods: ok (%d)" % len(EXPECTED))


main()
