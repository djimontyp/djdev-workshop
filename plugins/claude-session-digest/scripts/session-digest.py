#!/usr/bin/env python3
"""
claude-session-digest — stub (Phase 0)

Reads SessionEnd JSON from stdin, logs it to stderr, exits 0.
Full implementation follows in subsequent phases.
"""

import json
import sys


def main() -> None:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        data = {}

    session_id = data.get("session_id", "unknown")
    print(f"[session-digest] stub — session_id={session_id}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
