---
name: taste
description: Activate an independent session Taste Guard beyond the worker's immediate task. Use only when the user explicitly asks.
---

Locate `mnemorph` inside `CODEX_HOME` (default `~/.codex`) and resolve its link to the framework root. If the framework or a required source cannot be read, report its exact path as a setup blocker. Then follow `<framework root>/integrations/skills/taste.md`.

Launch the Guard with `fork_turns: "none"`, and give it the path of `<framework root>/src/core/taste/guard/waves.md` beside the charter's. `read_thread` may omit the active turn.

Codex caps a session's open agents ("N available concurrency slots … including you") and reclaims a finished agent's slot only when no message to it is unread; a finished agent never reads `send_message` (openai/codex#32353), so re-task it only with `followup_task`. While a Guard is active, count five slots as taken by you, the Guard and its three delegates, and run your own subagents only in the rest. The Guard acts when a message reaches it, so cue it with a `followup_task` saying only "Read the session since your last read" at each new human message and each result, change of plan, commitment or claim you report to the user; its turn-ending status lines between cues are not final. Never give a Guard's or delegate's thread a worker task; start a fresh subagent. At turn end, send the closing notice while the Guard runs and wait for its final reply, sending nothing after it; if it does not end, interrupt its running delegates (see `list_agents`), then the Guard.
