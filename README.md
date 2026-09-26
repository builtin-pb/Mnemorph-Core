# Mnemorph

Mnemorph is a codebase of **Intent, Context, Knowledge and Method** for doing work and learning from it. Coherent subjects hold the understanding, procedures, examples and evaluations they need. [Taste](src/core/taste/taste.md) guides production and judgment; [Theory](src/core/taste/theory.md) explains their interaction and the allocation of finite effort.

## Core and instances

Mnemorph-Core is the official framework: shared methods in `src/core/`, helpers in `tools/`, host skills in `integrations/`, starter memory in `memory-template/`, and the root files (`AGENTS.md`, this README, `memory-limits.json`, `.gitattributes`, `.gitignore`). It holds no one's memory.

Each person's Mnemorph is an instance: a separate Git repository derived from Core, holding its own copy of Core plus that person's memory in the rest of `src/`: the subject map `src/README.md`, `src/inbox.md`, `src/personal/`, `src/record/` and any subjects it grows. An instance may change its own Core, takes official changes selectively and can offer its Core changes back. Personal material never goes upstream.

## Create an instance

~~~sh
git clone <Mnemorph-Core URL> my-mnemorph && cd my-mnemorph
git remote rename origin upstream
cp -R memory-template/. src/
git add src && git commit -m "Start memory"
git remote add origin <private repository URL>   # optional; it must be private
~~~

Then link the instance into Claude Code or Codex as [integrations](integrations/README.md) describes, and invoke `$personalize` (`/personalize` in Claude Code) to have Mnemorph learn about you from materials you offer. The helpers need Python 3.10+ and the standard library.

## Start a task

Start every task through [Entry](src/core/__entry__.md), which applies Taste. Invoke `$taste` to start a session-scoped Taste Guard, `$learn` for prompt creation or editing, workflow design or capability development, `$reflect` for memory reflection, or `$personalize` for your personal account. Entering Learn need not start a study or experiment for a small edit.

~~~sh
python tools/modules.py list --query writing
python tools/modules.py search --query 'human correction' --limit 5
~~~

`list` discovers documents from their summaries. `search` finds sections inside them, including factual context and particulars that have no separate file. Read the relevant source before relying on it. Explicit `role: reference` keeps factual and evaluation material selectable as data wherever it lives; guidance can be selected as instructions. Neither location nor retrieval grants authority. See [retrieval](src/core/memory/retrieval.md).

## Find a subject

Start with the instance's subject map, `src/README.md`; each subject folder's `README.md` gives its overview, and known material can be read directly. [Learn](src/core/learn/learn.md) governs prompt and workflow changes and capability development; [Prompt Writing](src/core/learn/prompt-writing.md) composes instructions and [Mechanism Design](src/core/learn/mechanism-design.md) develops operations and workflows. [Review](src/core/scrutiny/review.md), [Construction and contest](src/core/scrutiny/construction-and-contest.md) and [Behavioral evidence](src/core/scrutiny/behavioral-evidence.md) support scrutiny. [Tools](tools/README.md) covers discovery, snapshots and executable checks.

## Reflect and revise

[Reflect](src/core/memory/reflect.md) weighs what to retain, forget and revise in a coherent subject account; [Memory](src/core/memory/memory.md) organizes and adopts those changes. Examples, tests, failed attempts and feedback are ordinary parts of their subjects. Git preserves committed history for possible recovery, as [budgets and history](src/core/memory/budgets-and-history.md) explains; it does not guarantee future discovery or present applicability.

## Take official updates

~~~sh
git fetch upstream
git log --oneline HEAD..upstream/main   # review what is new
git merge upstream/main                 # take everything, or
git cherry-pick <commit>                # take one change
~~~

After a merge, `git revert <commit>` declines a change; later merges will not bring it back. Where you changed Core yourself, resolve conflicts by what you want to keep, then run the checks.

## Offer Core changes back

Commit Core changes apart from memory: a Core commit touches only Core paths and quotes no memory, names no one and carries no personal paths, dates or examples. Offer them from a worktree on upstream:

~~~sh
git fetch upstream
git worktree add -b offer/<topic> ../mnemorph-offer upstream/main && cd ../mnemorph-offer
git cherry-pick <core commits>
git diff --name-only upstream/main | grep -vE '^(src/core/|tools/|integrations/|memory-template/|AGENTS\.md$|README\.md$|memory-limits\.json$|\.git(attributes|ignore)$)'   # must print nothing
git push <your fork of Mnemorph-Core> offer/<topic>
~~~

Then open a pull request, read its whole diff first and remove the worktree. Never push the instance's own branches to upstream or to any public remote.

## Privacy

The rest of `src/`, `src/memory-limits.json` and `.mnemorph-local/` are the instance's own and never go upstream; neither does anything drawn from them. Push the instance only to a remote confirmed private. Keep live controls, secrets, credentials and transient execution state in native storage or the ignored `.mnemorph-local/`.

## Change and check

~~~sh
python tools/modules.py check
python tools/memory.py sizes --limit 20
python -m unittest discover -s tools/tests
~~~

These check representation, links, size budgets and the helpers, not the quality of an agent's work; [Scrutiny and evidence](src/core/scrutiny/README.md) explains what evaluations establish. Keep provenance and supported scope close to claims, reconcile concurrent edits and commit coherent changes.
