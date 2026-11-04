"""Fail when a top-level function/let/const/var name is declared in more than one JS module.

Modules share one global scope (one <script> block each, no wrapper), so a
repeated top-level name silently overwrites or throws. Usage: python tests/check_js_globals.py
"""
import re
import sys
from pathlib import Path

JS_DIR = Path(__file__).resolve().parent.parent / "assets" / "js"
DECL = re.compile(r"^(?:async\s+function|function|let|const|var)\s+([A-Za-z_$][\w$]*)")


def top_level_names(text):
    """Yield (name, line) for column-0 declarations outside block comments."""
    in_comment = False
    for number, line in enumerate(text.splitlines(), 1):
        if in_comment:
            in_comment = "*/" not in line
            continue
        if line.startswith("/*") and "*/" not in line:
            in_comment = True
            continue
        match = DECL.match(line)
        if match:
            yield match.group(1), number


def main():
    seen = {}
    total = 0
    for path in sorted(JS_DIR.glob("*.js")):
        for name, number in top_level_names(path.read_text(encoding="utf-8")):
            total += 1
            seen.setdefault(name, []).append(f"{path.name}:{number}")
    dupes = {n: where for n, where in seen.items() if len(where) > 1}
    print(f"{total} top-level declarations, {len(seen)} unique names, {len(dupes)} duplicated")
    for name, where in sorted(dupes.items()):
        print(f"FAIL {name}: {', '.join(where)}")
    return 1 if dupes else 0


if __name__ == "__main__":
    sys.exit(main())
