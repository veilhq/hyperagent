"""Checks plan_usage.parse_usage_markdown against the adapter's /usage format."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plan_usage  # noqa: E402

SAMPLE = (
    "## Usage\n\n> Claude max subscription usage\n\n### Limits\n\n"
    "**5-hour limit** \u2014 **42%** \u00b7 Resets Oct 26, 7:00\u202fPM CDT\n\n`bar`\n\n"
    "**Weekly \u00b7 all models** \u2014 **18%** \u00b7 Resets Oct 30, 9:00 AM CDT\n\n`bar`\n\n"
    "**Weekly \u00b7 Opus** \u2014 **0%**\n\n`bar`\n\n---\n\n### This session\n"
)


def main():
    windows = plan_usage.parse_usage_markdown(SAMPLE)
    keys = [w["key"] for w in windows]
    assert keys == ["five_hour", "seven_day", "seven_day_opus"], keys
    assert windows[0]["utilization"] == 42.0
    assert windows[0]["label"] == "five-hour"
    assert windows[2]["resetsAt"] is None and windows[2]["resetsText"] == ""
    reset = datetime.fromtimestamp(windows[0]["resetsAt"])
    assert (reset.month, reset.day, reset.hour, reset.minute) == (10, 26, 19, 0), reset
    assert plan_usage.parse_usage_markdown("Claude Code raw fallback text") == []
    # A date more than a day in the past rolls into next year.
    now = datetime(2026, 12, 31, 12, 0)
    rolled = datetime.fromtimestamp(plan_usage._parse_reset("Jan 2, 8:00 AM CST", now=now))
    assert rolled.year == 2027, rolled
    print("plan_usage parser: ok")


if __name__ == "__main__":
    main()
