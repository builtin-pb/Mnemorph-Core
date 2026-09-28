# Host integrations

These skills connect Claude Code and Codex to a Mnemorph instance. Each host skill finds `mnemorph` in the host's configuration directory, resolves that link to the instance's root and follows the shared text in [skills](skills/). Link your instance, not a bare copy of Mnemorph-Core: the skills read the instance's own Core and memory. Run the commands below from the instance's root; running them again is safe.

## Claude Code

~~~sh
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
mkdir -p "$CLAUDE_DIR/skills"
ln -sfn "$PWD" "$CLAUDE_DIR/mnemorph"
for skill in "$PWD"/integrations/claude/skills/*; do ln -sfn "$skill" "$CLAUDE_DIR/skills/$(basename "$skill")"; done
grep -qx 'Use the mnemorph skill on every task.' "$CLAUDE_DIR/CLAUDE.md" 2>/dev/null || echo 'Use the mnemorph skill on every task.' >> "$CLAUDE_DIR/CLAUDE.md"
~~~

The `CLAUDE.md` line loads Mnemorph on every task. Skills are invoked as `/learn`, `/reflect`, `/personalize` and `/taste`. Claude Code expands a slash command only at the start of a message, so `/taste` stays model-invocable; its description and caller ask that only the main session start a Guard, for the human's own request. Nothing enforces this beyond those instructions.

Claude Code deletes session transcripts after 30 days by default, and reflection and `/personalize` learn from them. Keep them by setting `"cleanupPeriodDays": 36500` in `~/.claude/settings.json`.

## Codex

~~~sh
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
mkdir -p "$CODEX_DIR/skills"
ln -sfn "$PWD" "$CODEX_DIR/mnemorph"
for skill in "$PWD"/integrations/codex/skills/*; do ln -sfn "$skill" "$CODEX_DIR/skills/$(basename "$skill")"; done
~~~

`$mnemorph` is configured for implicit use on every task; `$learn`, `$reflect` and `$personalize` can be selected when applicable, and `$taste` only when invoked. Codex allows four open agents per session by default; for `$taste`, set `max_concurrent_threads_per_session = 8` under `[features.multi_agent_v2]` in the Codex `config.toml` so the worker, a Guard and its three delegates fit.

## Both hosts

`$taste` or `/taste` starts a Guard whose independent inquiry may range beyond the worker's immediate task but ends with the active worker run; the worker launches fresh Guards as needed and stops them before finishing. Reflect defaults to the present session unless the user explicitly requests others. The active project remains the task context; reflection and Personalize write memory in the instance. [record.py](../tools/record.py) reads each host's transcripts from the same configuration directories.

## Nightly reflection

A nightly run reflects on the day's sessions, files the notes agents took of what you said, and revises memory, so Mnemorph keeps learning without being asked. Schedule it once:

- **Claude Code:** in the Claude desktop app, open a session in your instance, paste the launcher below and ask Claude to run it every night, say at 2:00. Use the desktop app: the command line's `/schedule` makes cloud routines, which cannot read your files. Give the task your instance's folder and a permission mode that runs unattended, such as auto; in Manual mode a run waits at its first command for your approval. Auto mode allows the steps the launcher authorizes, so keep that list. Scheduled tasks can run at medium effort even when your sessions don't; reflection is judgment work, so set the model's effort in `~/.claude/settings.json`, for example `"modelSettings": {"claude-opus-5-5": {"effortLevel": "high"}}`.
- **Codex:** create an automation with the same launcher in your instance's folder.

Replace `<instance>` with your instance's path:

~~~text
Run Mnemorph's periodic reflection in <instance>: use the reflect skill and follow `src/core/memory/periodic-reflection.md`.

- Cursor: `.mnemorph-local/nightly-reflection/cursor.json`. If it is missing, create it and cover the past day.
- Sources: Claude Code sessions under `~/.claude/projects/`, Codex sessions under `~/.codex/sessions/`, and any ChatGPT data export in `~/Downloads/` newer than the cursor's last export.
- Time bound: 2 hours.

I authorize these steps on every run, where that procedure calls for them:
- Commit on `main` and push it to `origin` with a plain `git push`. Never push to `upstream`, and never push a nightly branch.
- For a core-prompt change, create the worktree `../mnemorph-nightly-<date>` on branch `nightly/<date>`, commit there, then remove the worktree with `git worktree remove`.
- In `src/inbox.md`, delete exactly the notes this run filed or dropped, leaving any note added after you read the file.
~~~

Each run ends with a report of what it kept, revised and forgot. A change to a core prompt waits on its `nightly/<date>` branch; read its diff and `git merge` it to adopt it.
