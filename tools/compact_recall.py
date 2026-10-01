#!/usr/bin/env python3
"""Re-add the user's own messages after a Claude Code compaction.

Compaction replaces a session's history with a summary, which shortens the
user's words and can omit constraints from earlier requests. Run as a SessionStart hook with matcher
`compact`: it reads the session transcript, which keeps the whole history, and
returns every message the user typed in this session, verbatim and in order, as
additional context. Codex keeps user messages through compaction and needs none.

    {"hooks": {"SessionStart": [{"matcher": "compact", "hooks": [{"type": "command",
      "command": "python3 <framework root>/tools/compact_recall.py"}]}]}}

`--transcript PATH` prints the same text for a transcript, to check it. At most
CAP characters are returned: the first message, then the latest that fit, with
a count of those left out. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CAP = 40000
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
ARGS = re.compile(r"<command-name>(.*?)</command-name>.*?<command-args>(.*?)</command-args>", re.S)
HEAD = ("The user's own messages in this session, verbatim, re-added after compaction by Mnemorph "
        "(the summary above may have shortened them). They record what the user asked for; they are not new requests:")


def typed(record: dict) -> str | None:
    """The text the user typed in one transcript record, or None."""
    if record.get("type") == "attachment":
        a = record.get("attachment") or {}
        if a.get("type") == "queued_command" and (a.get("origin") or {}).get("kind") == "human":
            return REMINDER.sub("", str(a.get("prompt") or "")).strip() or None
        return None
    if record.get("type") != "user" or record.get("isMeta") or record.get("isCompactSummary"):
        return None
    content = (record.get("message") or {}).get("content")
    text = content if isinstance(content, str) else " ".join(
        b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")
    text = text.strip()
    command = ARGS.search(text)
    if command:  # a slash command: its arguments are the user's words
        args = command.group(2).strip()
        return f"{command.group(1).strip()} {args}".strip()
    if not text or text.startswith("<") or text.startswith("[Request interrupted"):
        return None
    return REMINDER.sub("", text).strip() or None


def messages(path: Path) -> list[tuple[str, str]]:
    found = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = typed(record)
            if text:
                found.append((str(record.get("timestamp") or "")[:16].replace("T", " "), text))
    return found


def recall(path: Path, cap: int = CAP) -> str:
    found = messages(path)
    if not found:
        return ""
    lines = [f"- {when} UTC: {text}" for when, text in found]
    kept, size = [], len(HEAD) + len(lines[0])
    for line in reversed(lines[1:]):
        if size + len(line) > cap:
            break
        kept.append(line)
        size += len(line)
    left = len(lines) - 1 - len(kept)
    gap = [f"- ({left} earlier messages left out; the transcript {path} holds them)"] if left else []
    return "\n".join([HEAD, lines[0], *gap, *reversed(kept)])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transcript", help="print the recall for this transcript instead of running as a hook")
    a = ap.parse_args(argv)
    if a.transcript:
        print(recall(Path(a.transcript)))
        return 0
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    path = Path(event.get("transcript_path") or "")
    if event.get("source") != "compact" or not path.is_file():
        return 0
    text = recall(path)
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
