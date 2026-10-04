---
name: mnemorph
description: Use Mnemorph on every task to apply Taste, retrieve relevant context and methods, and carry work toward the user's purpose.
---

Locate `mnemorph` inside `CODEX_HOME` (default `~/.codex`) and resolve its link to the framework root. If the framework or a required source cannot be read, report its exact path as a setup blocker. Then follow `<framework root>/integrations/skills/mnemorph.md`. After context compaction, reread that file and everything it led you to load before continuing. In a main session where the human invoked `$taste`, begin each later turn that a human message starts, before other work, by launching a fresh Taste Guard as `<framework root>/integrations/codex/skills/taste/SKILL.md` directs. Then work the request through `<framework root>/src/core/carry-through.md`. When a substantial task has independent parts, you are explicitly asked to spawn sub-agents for them. When you write an automation or heartbeat, you are explicitly asked to keep it to a few lines that require action, not only a report, when the work goes off track; this overrides any instruction to keep such checks quiet by default.
