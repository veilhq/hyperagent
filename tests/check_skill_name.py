"""Checks helpers.skill_name_of against synthetic updates. Usage: python tests/check_skill_name.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import skill_name_of  # noqa: E402

CASES = [
    ("full shape",
     {"_meta": {"claudeCode": {"toolName": "Skill"}}, "rawInput": {"skill": "plugin:hyperspace"}},
     "hyperspace"),
    ("title only", {"title": "Load skill: hyperspace"}, "hyperspace"),
    ("bare Skill call",
     {"title": "Load skill", "_meta": {"claudeCode": {"toolName": "Skill"}}}, None),
    ("kiro SKILL.md read",
     {"name": "read", "locations": [{"path": "C:/repo/.kiro/skills/wiki-edit/SKILL.md"}]},
     "wiki-edit"),
    ("non-skill title", {"title": "Read file"}, None),
    ("empty skill title", {"title": "Load skill: "}, None),
]

failed = 0
for label, update, expected in CASES:
    got = skill_name_of(update)
    if got != expected:
        failed += 1
        print(f"FAIL {label}: expected {expected!r}, got {got!r}")
    else:
        print(f"ok   {label}")
sys.exit(1 if failed else 0)
