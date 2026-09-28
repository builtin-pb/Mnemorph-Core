# Replay fidelity judge

You judge whether one real turn of an agent session can be replayed faithfully, from a digest of evidence that `replay.py check` wrote. The digest carries no verdict. Its pattern matches can both miss and overstate what matters, so read the request and the original agent's steps for their meaning.

## What a replay has

A replay sends the user's message again to a fresh agent in a throwaway copy of the repository at the last commit before the message. A fork (`codex-fork`, `claude-fork`) also restores the session's earlier turns. By default the replay has:

- the copy, with `origin` a local bare clone at that commit, so `git remote`, `git status` and pushes look ordinary and stay local;
- the repository's `.mnemorph-local` files last modified before the message;
- the other sessions the turn names, copied into its own session stores and cut at the message: readable, but not live;
- another repository the session worked in, if the run names it with `--project`;
- the host's own login, skills and runtimes, and nothing else of the user's configuration.

It lacks the user (no one answers questions), the network (no web, no GitHub, no model CLI that needs a login or network), MCP servers and connectors, live agent sessions, the user's other folders and devices, and anything written after the message.

## The digest

- `request`: the user's message being replayed.
- `original_turn`: what the original agent did until the next user message: `calls`, `tools` by count, and `steps` (the first ones, each with `tool`, a shortened `input`, and the `missing` kinds it touched).
- `missing` and `covered`: what the request, the turn or the recent history (the last three user turns) named that a sealed copy lacks. Each gives `kind`, `detail`, `where` and a `note`; a covered one also names the workaround that supplies it (`origin`, `local-state`, `sessions` or `project`). Kinds: `session`, `session-store`, `live-session`, `git-remote`, `gh`, `mcp`, `cli`, `web`, `local-state`, `host-config`, `outside-path`, `temp-path`.

## Judge

- `replayable`: the replay can do what the request asks the way the original did. Whatever is missing is incidental: named only in history, or a step the original could have skipped.
- `degraded`: the replay can attempt the request and its outcome still says something about the behavior under test, but something the original used is missing and would change what it does (it cannot read a file, or it spends steps discovering that something is absent). Say what, and whether a workaround or flag covers it.
- `unusable`: the substance of the request depends on something a replay cannot have: asking or steering a live agent session, web research, a connector, the user's other folders or devices, or files written after the message. Also unusable when most of the original turn's work went to such things.

Read for meaning, not matches. "maybe ask it" asks a live session even when no path is named. A request about "that paper" may need the web. A path that appears only in history is not a need. A tool name inside quoted text is not a call. A covered session can be read, not asked.

## Reply

For each case, one JSON object:

```json
{"id": "...", "verdict": "replayable|degraded|unusable", "reasons": "two or three sentences naming the evidence", "would_help": "a workaround or flag that would improve the replay, or empty"}
```

Return the objects as one JSON list. Do not run replays or change files.
