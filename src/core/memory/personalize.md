---
id: core.memory.personalize
summary: Start or extend the user's personal memory from sources they offer (agent histories, host memories, documents, chat and mail exports), reading recent material in full, auditing coverage and asking for sources an agent cannot reach.
---

# Personalize

Use this when the user asks Mnemorph to learn about them from their materials, such as on a first install or when an export arrives. The result is a personal account in `src/personal/` that spares the user repeating themselves, a source map of what was read and what remains, and the few sources only the user can supply, each with its next step. Use [Reflect](reflect.md) to compose the account and [Memory](memory.md) to adopt it. `src/personal/` is private: it is never published or copied into a public repository.

## Agree on sources

When `src/personal/` exists, start from its source map: a later run covers new sources and gaps and revises the account. Then find what is reachable and ask the user once, in one message, about what they have not already settled:

- **Reachable now:** the user's typed words to Codex and Claude Code (`python3 tools/record.py append` collects them into the record, `src/record/`); host memories and instruction files, such as Claude Code's `CLAUDE.md` and `memory/` folders and Codex's `AGENTS.md` and memories; files the user names, such as a CV, notes, a diary or a photo library.
- **Needing the user's action:** data exports that arrive later, such as ChatGPT, Claude or Google Takeout (Gmail, Gemini); messages the system protects, such as iMessage, which needs the user to grant disk access; apps without a plain export, such as WeChat, where only a route the user accepts may be used and never one that risks their account; accounts a connector could reach once the user enables it. Give each route as currently documented, where to put the result (in place, or extracted under `.mnemorph-local/`), and what it would add.
- **Limits:** what the user wants kept out. Credentials, recovery codes and other secrets always stay out.

Sources the user named when invoking this are already authorized. Send the questions as soon as you know what is reachable, so slow exports can start, and keep reading the named sources meanwhile. Never change system settings or run third-party extractors on protected app data yourself.

## Read by what a helper needs

Inventory each source's structure, size and date span before reading, and keep coverage as you go. Read in this order:

1. The user's own statements to agents and existing host memories: standing preferences and corrections.
2. The latest self-descriptions (CV, bio, homepage), dated against later sources.
3. The most recent year of the user's own chronological writing, such as a diary, in full. In mail and chats, read their own messages for that year in full and relevant incoming exchanges about relationships, shared events and ordinary life. Close people's circumstances can matter in their own right; read enough surrounding conversation to establish who experienced what and whether a plan happened. Recent events decide what a helper needs now, and sampling misses them.
4. Older material by landmark and theme (annual reviews, turning points, recurring people). Sampling is acceptable here; record what it skipped. New identities or shared contexts can change earlier interpretation: follow their aliases and connected conversations, including relationships that recent message counts underrepresent.

Split large reading by source or period, among read-only subagents when available. Collect every part's dated facts and sources in one notes file under `.mnemorph-local/`; write the account from it, not from recall. Distinguish the sender from whose words or experience a message contains: templates, forwarded text and other people's documents are not the user's self-description. Preserve relevant facts about their close people with that attribution. A preview or media placeholder is not full-content coverage; reopen recoverable originals when an important fact turns on their contents. Copy no raw files or photos.

## Write the account

Follow `src/personal/README.md` when it exists. Otherwise create it with identity and current situation first, an index to a few files by life domain (studies and work, admin, everyday, background), and how the user likes help; grouping by domain keeps personal facts out of unrelated tasks. Date what can change, attribute sources, mark inferences and let later direct statements win. Keep a source map with each source's location, what was read, coverage, gaps and pending sources.

## Audit before finishing

Give a fresh subagent the account and access to the sources, without your notes (if none can start, reread the recent sources yourself), and ask what consequential facts it misses, misstates or misattributes, especially from the last year, and what is irrelevant to understanding the user and their close relationships. Check it yourself against what a helper would need: current role and place, commitments and deadlines, recent major events, health or constraints that change advice, key people and how the user likes help. Repair what the audit supports and recheck the repaired passages.

## Close with the user

Report briefly, by domain, what was recorded, then coverage and gaps, and ask what is wrong or missing. Apply their corrections and note them in `src/inbox.md`. Repeat the route for each unreached source the user has not declined, and say what reads it: periodic reflection reads the exports its invocation names, such as ChatGPT's; another run of this method takes the rest. Commit following Memory's adoption and the shared-checkout rules in [periodic reflection](periodic-reflection.md#shared-checkout). Push personal material only to a remote you have confirmed is private; a clone of the public Mnemorph repository is not.

## Shaping experience

In one instance's import, an agent sampled a large personal archive and called the import finished, although Taste and Reflect already required a review. An audit found three consequential recent events missing; the repair read the latest self-description and the whole recent year with two source auditors, then a fresh reviewer checked it. This method makes that route the default. Replayed three times on that archive, the same agent read the whole recent year with it (8–47% without) but recorded all three missed events in no run; with per-chunk fact notes it did in all three. The replays used one archive and one model; their records are not included here.
