# Host integrations

These skills connect Claude Code and Codex to a Mnemorph instance. Each host skill finds `mnemorph` in the host's configuration directory, resolves that link to the instance's root and follows the shared text in [skills](skills/). Link your instance, not a bare copy of Mnemorph-Core: the skills read the instance's own Core and memory. Run the commands below from the instance's root.

## Claude Code

~~~sh
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CLAUDE_DIR/skills"
ln -s "$PWD" "$CLAUDE_DIR/mnemorph"
for skill in "$PWD"/integrations/claude/skills/*; do ln -s "$skill" "$CLAUDE_DIR/skills/$(basename "$skill")"; done
echo 'Use the mnemorph skill on every task.' >> "$CLAUDE_DIR/CLAUDE.md"
~~~

The `CLAUDE.md` line loads Mnemorph on every task. Skills are invoked as `/learn`, `/reflect`, `/personalize` and `/taste`. Claude Code expands a slash command only at the start of a message, so `/taste` stays model-invocable; its description and caller ask that only the main session start a Guard, for the human's own request. Nothing enforces this beyond those instructions.

## Codex

~~~sh
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$CODEX_DIR/skills"
ln -s "$PWD" "$CODEX_DIR/mnemorph"
for skill in "$PWD"/integrations/codex/skills/*; do ln -s "$skill" "$CODEX_DIR/skills/$(basename "$skill")"; done
~~~

`$mnemorph` is configured for implicit use on every task; `$learn`, `$reflect` and `$personalize` can be selected when applicable, and `$taste` only when invoked. Codex allows four open agents per session by default; for `$taste`, set `max_concurrent_threads_per_session = 8` under `[features.multi_agent_v2]` in the Codex `config.toml` so the worker, a Guard and its three delegates fit.

## Both hosts

`$taste` or `/taste` starts a Guard whose independent inquiry may range beyond the worker's immediate task but ends with the active worker run; the worker launches fresh Guards as needed and stops them before finishing. Reflect defaults to the present session unless the user explicitly requests others. The active project remains the task context; reflection and Personalize write memory in the instance. [record.py](../tools/record.py) reads each host's transcripts from the same configuration directories.
