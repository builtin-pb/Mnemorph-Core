---
name: taste
description: Activate an independent session Taste Guard beyond the worker's immediate task. Use only when the user explicitly asks.
---

Locate `mnemorph` inside `CODEX_HOME` (default `~/.codex`) and resolve its link to the framework root. If the framework or a required source cannot be read, report its exact path as a setup blocker. Then follow `<framework root>/integrations/skills/taste.md`.

Launch the Guard with `fork_turns: "none"`. `read_thread` may omit the active turn.

Codex caps a session's open agents ("N available concurrency slots … including you") and reclaims a finished agent's slot only when no message to it is unread; a finished agent never reads `send_message` (openai/codex#32353), so re-task it only with `followup_task`. While a Guard is active, count five slots as taken by you, the Guard and its three delegates, and run your own subagents only in the rest. Never give a Guard's or delegate's thread a worker task; start a fresh subagent. At turn end, send the closing notice while the Guard runs and wait for its final reply, sending nothing after it; if it does not end, interrupt its running delegates (see `list_agents`), then the Guard.
