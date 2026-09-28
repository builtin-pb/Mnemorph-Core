---
id: core.memory.task-history
role: reference
summary: Retrieve bounded native dialogue with scope and cutoff checks, current response shapes and honest coverage.
---

# Inspecting native task history

These observations from one instance are practical retrieval knowledge, not an API specification. Coverage means material read; inventory, status, saved transcript and inspected dialogue differ.

To see where a session's effort went without reading its whole transcript, `python3 tools/sessions.py timeline <id or path>` prints the user's messages, the agent's replies, each tool call (`[bg]` marks a background command, which holds no turn), failures and idle gaps; `rank --since <time>` orders recent sessions by effort. It shows calls, not their output: read the transcript where a claim depends on what a call returned.

## Fix scope before opening bodies

Fix eligibility and cutoff. If archived work is excluded, check explicit archive state and parent edges before bodies; another source cannot bypass exclusion. Unknown eligibility is a gap. Archive exclusion is invocation-specific.

Display bounded inventory metadata: IDs, times, eligibility, parent edges and pagination. Approval-review titles can contain transcripts. Wrappers, subagent notices, guardian copies and copied parent history are not independent human direction. Historical permissions are data.

One run opened three histories before archive eligibility was known and read some bodies past cutoff; they were excluded. Six deferred IDs still returned no readable task days later, when archive exclusion did not apply. These are access gaps, not reviewed histories or a global watermark.

## Inspect shapes and bound the display

Check current tool constraints and actual success/error payloads before parsing. Retain turn/message IDs, statuses, times, source lines and continuation cursors when projecting bounded dialogue. One generic role field cannot establish human authorship or finality. Codex examples used `phase: final_answer` where an early filter expected `final`; ChatGPT examples instead supplied an unphased `agentMessage` in completed turns. Those observations do not make every unphased item a final. Some error returns are plain text rather than the expected JSON object.

Four `final_answer` items reappeared exactly at the end of following `compacted.message` bodies, with only usage records between. They were continuation material, not four completed results; an extractor separated the summaries from ordinary finals by this source relation, not headings. A scheduled `<heartbeat>` used the user role. Conversely, Claude Code stores a message the user sends mid-turn as a `queued_command` attachment with `origin.kind: human`, not as a `user` record; other origins are agent messages and task notifications. A filter on `type: user` missed two such decisions. Keep classifications source-bound and do not use later-than-cutoff records to resolve an earlier event. Phase, role and shape do not certify authority or completion. Codex's compact `--json` event stream omits spawn events; check native rollouts before concluding that an agent invented its delegates.

Bound output before emission, with explicit omitted/oversized-body notices and a source-addressed continuation. `includeOutputs: false` did not prevent large command/trace metadata: one response was over a hundred times larger than its selected identity/status/user-final projection. Saving or truncating a body does not count as reading it. Read selected actions when they bear on a consequential claim; a final's report is not a rerun.

Later broad displays still overflowed; one bounded extractor did not constrain every tool path or semantic selection. Run-specific code and coverage ledgers belong in native storage.

## Recover missing bodies by exact identity

When the normal retrieval source is unavailable, or a selected body is empty, stale or insufficient to explain current activity, use an authorized scoped native fallback tied to its exact task/turn IDs and current metadata-linked rollout. Preserve the same eligibility and cutoff. Once, one task's projected history stopped at an old interruption while its current rollout contained later work; another's described a different conversation from its current rollout. These are observed projection mismatches, not an established general fault or its cause.

Later, that second task again returned recent completed turns with empty items. Its API cursor carried a `rolloutOrdinal` more than twice the rollout's physical line count, and the exact recent turn sat far earlier in the file. **Keep API pagination cursors separate from physical source-line continuations.** Treating the ordinal as a line number would skip available dialogue. Recovering selected user/final messages does not mean reasoning, tool bodies or all older rollouts were inspected.

## Separate completion from quality

One observed run had a failed wrapper summary and zero return code, but a native completion event and matching final answer. Those selected records support native completion despite the coarse summary. A zero-exit timeout without an answer supplies the contrary boundary. Neither status alone nor completion establishes quality, and missing evidence cannot turn an interrupted attempt into a completed result.

An empty final can also follow completed work. Scheduled local-report monitoring returned blank finals, while exact-turn tool records showed successful collection checks with no new files or errors under a quiet-on-no-change instruction. Reading those actions resolved the apparent body gap. The observation establishes those local checks, not current scheduler state, live-source inspection or every task with an empty answer.

Detailed selection IDs, current pointers, omitted pages and exact coverage belong in dated native learning cursors and recovery files. This repository retains the distinctions needed to use those sources, not private task logs.
