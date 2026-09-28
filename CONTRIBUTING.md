# Contributing to Mnemorph-Core

Core improves through use: you change Core in your own instance while working, then offer the change here.

## What belongs in Core

Core is `src/core/`, `tools/`, `integrations/`, `memory-template/` and the root files `AGENTS.md`, `README.md`, `CONTRIBUTING.md`, `LICENSE`, `memory-limits.json`, `.gitattributes` and `.gitignore`. The rest of an instance's `src/`, and `.mnemorph-local/`, belong to that person. They never come here, and neither does anything drawn from them.

Commit Core changes apart from memory changes. A Core commit touches only Core paths, quotes no one's memory, names no one, and carries no personal paths, dates or examples.

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
git push <your fork> offer/<topic>
~~~

The push guard refuses the push if any commit would carry a file outside Core. It checks files, not words, so read every commit message and the pull request's whole diff before you open it. Then remove the worktree with `git worktree remove ../mnemorph-offer`. Never push your instance's own branches to upstream or to any public remote.
