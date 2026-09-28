# Host particulars

Seen on Codex 0.155 and Claude Code 2.1.28x; recheck after upgrades.

- **Codex delegation.** Every turn Codex tells the model not to spawn sub-agents unless the user or an `AGENTS.md` or skill instruction explicitly asks, so instructions wanting delegation must ask. `multi_agent_mode_hint_text` under `[features.multi_agent_v2]` replaces that text. A finished agent with an unread message keeps its slot (openai/codex#32353).
- **Codex logs.** `codex exec --json` does not record spawn calls, and messages between agents are stored encrypted; sub-agent rollouts sit beside the root's, naming its `parent_thread_id`. A reviewer reading only the log cannot confirm a claimed delegation.
- **Codex headless runs.** With `--ephemeral`, spawned sub-agents fail ("no thread with id"). In the `workspace-write` sandbox `.git` is read-only, so commits need an approval.
- **Claude Code headless runs.** `claude -p` reads `$HOME/.claude/CLAUDE.md` even when `CLAUDE_CONFIG_DIR` is set, and `.claude/CLAUDE.md` in parent directories, so an isolated run needs its own `HOME`.
- **Claude Code sub-agents** receive a frame for reporting to a parent and the parent's `CLAUDE.md` and `AGENTS.md`; they do not stand in for a top-level session in behaviour comparisons.
