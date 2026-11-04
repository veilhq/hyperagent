"""Checks ClaudeCodeBackend default-model read/write against a temp CLAUDE_CONFIG_DIR."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backends  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CLAUDE_CONFIG_DIR"] = tmp
        backend = backends.ClaudeCodeBackend()
        settings = Path(tmp) / "settings.json"

        # No settings file: the account default, and writing creates the file.
        assert backend.read_default_model() == "default"
        assert backend.write_default_model("opus")
        assert json.loads(settings.read_text()) == {"model": "opus"}
        assert backend.read_default_model() == "opus"

        # Other keys survive a write; choosing the account default removes the key.
        settings.write_text(json.dumps({"model": "opus", "permissions": {"allow": ["Bash"]}}))
        assert backend.write_default_model("sonnet")
        assert json.loads(settings.read_text()) == {"model": "sonnet", "permissions": {"allow": ["Bash"]}}
        assert backend.write_default_model("default")
        assert json.loads(settings.read_text()) == {"permissions": {"allow": ["Bash"]}}
        assert backend.read_default_model() == "default"

        # An unparsable file is never overwritten.
        settings.write_text("{ not json")
        assert backend.read_default_model() is None
        assert backend.write_default_model("opus") is False
        assert settings.read_text() == "{ not json"
        assert not list(Path(tmp).glob("*.tmp"))
    print("claude-code default model: ok")


if __name__ == "__main__":
    main()
