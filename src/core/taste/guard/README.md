---
id: core.taste.guard
role: reference
summary: Find the session Taste Guard's charter and the worker's protocol for running it.
---

# Taste Guard

The Taste Guard is an independent companion that questions a worker session at least one level above the current work and delegates the investigation. The user starts it with `$taste` in Codex or `/taste` in Claude Code.

- [Charter](charter.md) is the Guard's whole instruction. The worker gives the Guard only this path, and the Guard does not load the Core entry or the worker's methods.
- [Worker protocol](worker.md) tells the worker how to launch, answer, prioritize and close a Guard without steering it. Host skill files add only launch mechanics.
