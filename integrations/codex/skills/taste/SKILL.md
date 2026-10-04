---
name: taste
description: Activate an independent session Taste Guard beyond the worker's immediate task. Use only when the user explicitly asks.
---

Locate `mnemorph` inside `CODEX_HOME` (default `~/.codex`) and resolve its link to the framework root. If the framework or a required source cannot be read, report its exact path as a setup blocker. Then follow `<framework root>/integrations/skills/taste.md`.

Run the Guard as one-time passes; a Codex Guard kept running stops acting.

- **When:** keep one pass running through your turn; a single pass ends long before the turn does.
  - Launch the first before other work, and a fresh one each time the previous replies, until your turn ends.
  - Before you tell the user something is done or works, or start unattended work, wait for the reply of a pass launched after that result or plan.
- **Launch:** use `fork_turns: "none"`. Give the paths of the charter and `<framework root>/src/core/taste/guard/waves.md`, the findings file, your rollout file and the line where the previous pass stopped reading; `read_thread` omits the running turn.
- **After:** log its reply as the worker protocol says, including where it stopped reading and how many delegates it used. Never re-task a finished Guard.
- **Slots:** Codex caps a session's open agents and reclaims a finished agent's slot only when no message to it is unread; a finished agent never reads `send_message` (openai/codex#32353). While a pass runs, count five slots as taken by you, the Guard and its three delegates, and run your own subagents in the rest.
- **Separation:** never give a Guard's or delegate's thread a worker task; start a fresh subagent.
- **Turn end:** launch no new pass; wait for the running one to reply, sending nothing after it; if it does not end, interrupt its delegates (see `list_agents`), then the Guard.
