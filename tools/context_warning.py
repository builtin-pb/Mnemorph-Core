#!/usr/bin/env python3
"""Warn once when a Claude Code session's context passes a size.

Long sessions run deep before Claude Code compacts, and one instance's records
showed repeated corrections about twice as often past 250k tokens. Run as a
UserPromptSubmit hook: it reads how much context the session's last reply used
from the transcript and, the first time that reaches --tokens, shows the user a
warning and asks the agent to offer a handoff to a fresh session. Later prompts
in the same session stay quiet; any error stays quiet too, so a prompt is never
held up.

    {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
      "command": "python3 <framework root>/tools/context_warning.py --tokens 500000"}]}]}}

`--transcript PATH` prints the context size of a transcript's last reply, to
check it. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

STATE = Path(tempfile.gettempdir()) / "claude-context-warning"  # one empty file per warned session
CHUNK = 1 << 20
USAGE = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def context_tokens(path: Path) -> int | None:
    """Tokens in the context of the main thread's last reply, reading from the end."""
    with path.open("rb") as handle:
        size = handle.seek(0, 2)
        start = size
        while start > 0:
            start = max(0, start - CHUNK)
            handle.seek(start)
            lines = handle.read(size - start).splitlines()
            if start > 0:
                lines = lines[1:]  # may begin mid-record
            for raw in reversed(lines):
                try:
                    record = json.loads(raw)
                except ValueError:
                    continue
                usage = (record.get("message") or {}).get("usage") if record.get("type") == "assistant" else None
                if usage and not record.get("isSidechain"):
                    return sum(int(usage.get(k) or 0) for k in USAGE)
    return None


def warn(event: dict, limit: int) -> dict | None:
    transcript = event.get("transcript_path")
    if not transcript:
        return None
    marker = STATE / (event.get("session_id") or Path(transcript).stem)
    if marker.exists():
        return None
    tokens = context_tokens(Path(transcript))
    if tokens is None or tokens < limit:
        return None
    STATE.mkdir(parents=True, exist_ok=True)
    marker.touch()
    used, cap = round(tokens / 1000), round(limit / 1000)
    return {
        "systemMessage": f"This session's context is about {used}k tokens, past {cap}k. "
                         "Consider continuing in a fresh session with a short handoff.",
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"This session's context has reached about {used}k tokens, past the user's {cap}k limit "
                "for one session. After answering this message, say so in one line and offer to write a "
                "short handoff (open decisions, running work, where the files are) for a fresh session, "
                "and when you write one, also leave a one-click task that starts the fresh session from it "
                "if your host offers one."),
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tokens", type=int, default=500000, help="warn once context reaches this many tokens")
    parser.add_argument("--transcript", type=Path, help="print the context size of this transcript's last reply")
    args = parser.parse_args(argv)
    if args.transcript:
        print(context_tokens(args.transcript))
        return 0
    try:
        out = warn(json.load(sys.stdin), args.tokens)
    except Exception:  # a broken warning must never block the user's prompt
        return 0
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
