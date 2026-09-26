---
id: record.user-words
role: reference
summary: The verbatim, append-only record of the messages the user typed to Codex and Claude Code, the authority for checking summaries, rules and memory; never loaded by default.
---

# The user's own words

`YYYY-MM.jsonl` files hold, one JSON object per line, the messages the user typed to Codex or Claude Code. They are the authority against which summaries, rules and memory are checked. Read them when a claim about what the user said, decided or allowed matters; they are not loaded into sessions.

[`tools/record.py append`](../../tools/record.py) adds new messages from the hosts' own transcripts (under `CLAUDE_CONFIG_DIR`, else `~/.claude`, and `CODEX_HOME`, else `~/.codex`), redacting credentials, and prints counts only. Periodic reflection appends first; run it yourself before relying on the current day. [`tools/repeats.py`](../../tools/repeats.py) finds corrections the user had to repeat, and the [record check](../../tools/README.md#record-check) judges removed guidance and personal passages against these messages.

## What enters

Only messages the user sent: typed messages, including those sent while an agent works (`queued`), answers to agent questions (`question_reply`), words given when rejecting a tool use (`rejection_feedback`), annotation, diff and file-attachment comments (only the user's part), thread goals the user set (`goal`), slash commands and shell input. Pasted text counts as sent. Excluded: archived, exec, subagent, automation, `claude -p` and temporary-directory sessions; injected context; task notifications; other agents' messages; tool results; app-generated prompts; replayed copies.

## Fields

`time` (UTC), `host`, `session`, `message`, `source` and `line`, `kind`, `text` (verbatim), `attachments` (names, paths or placeholders, never contents) and `redacted` (credential types replaced). `context` is agent-written, not the user's words: an excerpt of what a terse reply answered. `stance` is a mechanical, conservative reading (`own`, `acceptance`, `question`, `mixed` or `unknown`); an acceptance records the agent's proposal as allowed by the user, not as the user's words.

## Reading

```sh
jq -r 'select(.stance=="own") | "\(.time) \(.text)"' src/record/*.jsonl
python3 tools/record.py check
```

Lines are in append order; sort by `time` when order matters.

## Rules

- Append only. Entries are never condensed, edited or deleted; a correction is a later message.
- `text` is verbatim apart from credentials, replaced before writing with typed placeholders such as `[REDACTED:api_key]`.
- Only the user's later words supersede earlier ones. Agent summaries, rules and memory never do.
- Private like `src/personal/`: never published or offered upstream.
- Not loaded by default; memory may cite entries by `time` and a short quote.
