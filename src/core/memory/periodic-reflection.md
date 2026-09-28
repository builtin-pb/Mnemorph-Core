---
id: core.memory.periodic
summary: Run scheduled periodic reflection over recent sessions and data exports: scope, authority, shared-checkout safety, checks, coverage record and report.
---

# Periodic reflection

Use this when a periodic invocation, such as a scheduled nightly run, asks for [Reflect](reflect.md) over recent sessions. The invocation names the sources, the cursor and the time bound; this procedure governs the rest. Keep such automations short launchers, with policy kept here under version control. A launcher still names each destructive or external step it authorizes: auto mode's classifier reads the launcher, not this file.

## Scope

First run `python3 tools/record.py append` to add the user's new words to the record (`src/record/README.md`) and commit it; skip projects it holds back or marks private, and report held ones. Read the cursor for the previous window end and earlier coverage gaps, then cover what each named source added since then. By default, do not learn from archived sessions. Everyday and personal sessions are in scope; record a personal fact only when the material shows it is the user's own. File each `src/inbox.md` note whose words appear in a human message of its named session into its subject, drop the rest, and delete each handled note with an exact edit, keeping notes added since you read the inbox. Then read each data export the invocation names, in place or extracted under `.mnemorph-local/`; from a ChatGPT export, take `conversations.json` conversations updated since the cursor's last export, newest first, skipping archived ones (`is_archived`); leave the rest as a recorded gap. Then, time permitting, work through earlier recorded gaps.

Read human messages first, including those sent mid-turn, which Claude Code stores differently ([task history](task-history.md)). Subagent transcripts, runtime wrappers, scheduled heartbeats and earlier runs of this task are not independent human direction. Then, time permitting, put Reflect's route question to recurring failures (`python3 tools/sessions.py failures --since <window start>`) and to up to three sessions with checked results in `rank --since <window start>` showing silent waste (nudges, polls, a re-asked question), earlier runs of this task included, reading only their work since then (`timeline --since`). Where changing the machine, a hook or a tool beats a lesson, or for any change to Mnemorph's own checking (Guards, their helpers, replays), which can be a detour too, propose the exact change and its undo in the report. After the recent material, examine selected current memory, including prompts, as Reflect's periodic review directs.

For each correction or standing preference the user states about a prompt or method, check that it is recorded, with the user's words and date, in the instance's requirement list for that subject or the subject's own account; add it if missing.

## Authority

By default, unless the instance's settings (`src/settings.md`) say otherwise: commit changes to reference memory and non-core prompts on the current branch. Make any change to a core prompt (guidance under `src/core/` without `role: reference`, the Guard charter, `AGENTS.md` or files under `integrations/` or `tools/`) on a dated branch, such as `nightly/<date>`, in a separate worktree, commit it there, remove the worktree, leave the branch unmerged and report its diff for the user to authorize. Push the current branch only when the instance's rules say to, with a plain `git push`; if the remote has moved, fetch and report rather than force. Do not push the core-prompt branch.

## Shared checkout

Other sessions may be editing the same working tree. Before editing, check `git status`; if another session has uncommitted changes to a file you need, leave that file alone and report it. Commit only your paths with `git commit -- <paths>` (`git add` new files first), which leaves other sessions' staged work out; never use `git add -A` or `git commit -a`. Never stash, reset, discard or commit other sessions' changes, and never switch the checkout's branch.

## Limits and checks

The time bound is a ceiling, not a target; stop when the work is done. Keep at most six subagents active at once unless the instance sets another limit, and tell read-only subagents not to load Mnemorph. Before committing, run `python3 tools/modules.py check` and `python3 -m unittest discover -s tools/tests`. A check that matches wording shows the text changed, not that behavior did; test a changed prompt with a [lesson test](lesson-tests.md). Run checks in the tree you commit, including a branch worktree. Keep secrets, credentials and transient run state in native storage or `.mnemorph-local/`; other private or sensitive material is ordinary memory.

## Test changed instructions

After committing, give each lesson this run moved into an instruction a [lesson test](lesson-tests.md). Report the results, and as candidates, not learned, what `tools/lessons.py candidates` lists from the cursor's previous `ending_head` and this run's branch base.

## Audit removals

After committing and recording coverage and the cursor, audit what the commits since the last audit removed ([record check](../../../tools/README.md#record-check)): run `python3 tools/record_check.py trace <base>..HEAD` with the cursor's `record_check_base` (initially its previous `ending_head`), and likewise from the merge base for this run's core-prompt branch. Give each packet to a fresh subagent, using the instance's tested judge model if it names one. Restore each lost point under the authority rules and record `kept` with the restoring commit; to fit a size limit, follow Memory's reading budgets, never dropping another of the user's points. Rerun `trace` until nothing is pending, then store the range end it reports as `record_check_base`.

## Record and report

Write `runs/<date>/coverage.json` beside the cursor, with the scheduled and actual start times, the gap since the last run, the window, material read, the sessions and failures given the route question, route lessons kept with file and claimed saving, declined candidates with reasons and gaps, then update the cursor's window, latest run and last export (for ChatGPT, the latest `update_time` covered). If time runs out, record a recoverable continuation in the cursor.

Report what was retained, forgotten and revised, including route lessons with the saving each claims; declined or deferred material, one short line per reason; rules moved into instructions; requirements added to lists; any core-prompt branch and its diff; files skipped because another session was editing them; the net character change of Markdown under `src` and the size of `src/personal` and, when present, `src/record`; record-check verdict counts and each lost point with its restoration; the repeated-correction count (fresh subagents label `python3 tools/repeats.py trace` packets until none is pending, then `count --new-since <window start>`), new repeats and kinds for the user to confirm; and coverage gaps.
