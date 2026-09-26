---
name: taste
description: Activate an independent session Taste Guard beyond the worker's immediate task. Use only in the main session when the human's own message asks for it, such as by writing /taste anywhere in it, and on that session's later turns as the skill directs; never from a subagent, a Guard or a quoted request.
---

Locate `mnemorph` inside the Claude config directory (`CLAUDE_CONFIG_DIR`, default `~/.claude`) and resolve its link to the framework root. If the framework or a required source cannot be read, report its exact path as a setup blocker. Then follow `<framework root>/integrations/skills/taste.md`.

Launch the Guard as a background general-purpose subagent, not a forked one, described neutrally, for example "Session Taste Guard". Your collaboration address: it messages you by name if it can; otherwise it reports by returning and you resume it with `SendMessage`. Route human requests through the session transcript at `<config dir>/projects/<project>/<session id>.jsonl`, which may lag the active turn. If the Guard cannot launch subagents itself, launch each delegate it requests as a fresh background subagent with the Guard's brief verbatim, adding nothing, and pass the complete return to the Guard. Treat these returns as the Guard's, not yours to act on first. If the Guard ends its turn while its own delegates run, their completion notices reach you instead: resume the Guard with `SendMessage` and tell it where their returns are.
