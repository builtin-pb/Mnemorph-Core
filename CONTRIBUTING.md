# Contributing to Mnemorph-Core

Core improves through use: you change Core in your own instance while working, then offer the change here.

## What belongs in Core

Core is `src/core/`, `tools/`, `integrations/`, `memory-template/` and the root files `AGENTS.md`, `README.md`, `CONTRIBUTING.md`, `LICENSE`, `memory-limits.json`, `.gitattributes` and `.gitignore`. The rest of an instance's `src/`, and `.mnemorph-local/`, belong to that person and never come here.

What you learn from using Mnemorph can: a procedure, criterion, tool or lesson, stated so it serves anyone. Personal particulars never do, in files or commit messages: people, contacts, accounts, paths, times, private projects and verbatim private text. An example must be invented or generic. Methods other people taught you, such as in a shared instance, come here only with their agreement. Commit Core changes apart from memory changes, touching only Core paths.

## Make a change

Change prompts and agent instructions through [Learn](src/core/learn/learn.md), and test a changed instruction with a [lesson test](src/core/memory/lesson-tests.md). Before committing, run:

~~~sh
python3 tools/modules.py check
python3 tools/memory.py sizes --limit 20
python3 -m unittest discover -s tools/tests
~~~

These check structure, links, size budgets and the helpers, not the quality of an agent's work.

## Offer it

From your instance, cherry-pick the Core commits onto a branch of upstream in a separate worktree, and push that branch to your fork of Mnemorph-Core:

~~~sh
git fetch upstream
git worktree add -b offer/<topic> ../mnemorph-offer upstream/main && cd ../mnemorph-offer
git cherry-pick <core commits>
python3 tools/push_guard.py review upstream/main..offer/<topic>
git push <your fork> offer/<topic>
~~~

`review` writes one packet per commit. Give each to a fresh reviewer, an agent without your memory or yourself, who follows [the push review brief](tools/push_review.md) and records a verdict. The push guard refuses any commit that carries a file outside Core, lacks a clean verdict, or trips its scan for home paths, email addresses and the private terms you list in `.git/mnemorph-scrub/terms.txt`. Then remove the worktree with `git worktree remove ../mnemorph-offer`. Never push your instance's own branches to upstream or to any public remote.

To publish without these steps, where you can push to the target (your fork, or upstream if you maintain it), let [publish_core.py](tools/publish_core.py) do them: every 30 minutes it applies the Core part of your new commits on top of the target branch, has a fresh Codex reviewer judge each against the brief, runs the checks and pushes what passes; a conflict, a blocked commit or a failed check stops it and notifies you once. Start it with `python3 tools/publish_core.py install --from <last commit already offered> --remote <remote> --branch <branch>`; `status` shows what is pending and `uninstall` stops it.
