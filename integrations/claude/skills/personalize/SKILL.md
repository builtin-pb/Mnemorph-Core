---
name: personalize
description: Learn about the user from sources they offer (agent histories, host memories, documents, chat and mail exports) into their private Mnemorph memory, and ask for the sources an agent cannot reach.
---

Locate `mnemorph` inside the Claude config directory (`CLAUDE_CONFIG_DIR`, default `~/.claude`) and resolve its link to the framework root. If the framework or a required source cannot be read, report its exact path as a setup blocker. Then follow `<framework root>/integrations/skills/personalize.md`.

Claude Code's own memory folders are under `<config dir>/projects/*/memory/`.
