---
id: core.memory.budgets-and-history
role: reference
summary: Check hard Markdown reading budgets and recover bounded Git history with the maintenance helper, including exit codes, paging and limits.
---

# Budgets and history recovery

[Memory](memory.md) sets the rules for budgets and exceptional history use; [Reflect](reflect.md) governs reconstruction. This account explains the maintenance helper, `tools/memory.py`.

## Inspect reading budgets

[memory-limits.json](../../../memory-limits.json) sets a hard 6,000 Unicode-character default for indexed Markdown accounts under `src` and named live guides. Subject overviews (`README.md`) inherit 3,500; exact overrides need a content-based reason. New indexed accounts enter scope automatically; give new maintained `src` accounts headers and add nested live guides to the include list. Raw trials and frozen support remain retrievable outside the short-account gate. Ceilings are not targets.

~~~sh
python tools/memory.py sizes --path src/core/memory --limit 8
python tools/memory.py sizes --path src/core/__entry__.md --all
~~~

The helper counts whole text, including headers, examples and code. `sizes` exits 1 for overage, 2 for tool/configuration error, and returns bounded JSON. Check edited paths and the whole governed set before adoption; the repository checker also applies the gate. Binary, unindexed raw Markdown and other out-of-scope files appear as skipped.

[Memory's reading-budget rule](memory.md#reading-budgets) requires another agent's Taste review of a new allowance and its content. A passing count cannot establish quality. Judge the total material a consumer must read; moving material between files does not itself earn an exception.

## Recover history exceptionally

Use Git history for a relevant failure, a distinctive niche match or an explicit historical question, beginning with bounded metadata and then the necessary revision and passage:

~~~sh
python tools/memory.py history --path src/core/memory/reflect.md --limit 5
python tools/memory.py history --query 'distinctive remembered text' --path src/core --limit 5
python tools/memory.py show REVISION
python tools/memory.py show REVISION path/to/old-file.md --start-line 30 --lines 40
~~~

The helper exposes commit and blob identities without changing the checkout. `history --path` follows earlier names for a single file; directory history does not follow a renamed tree wholesale. Commit results show at most 30 relevant paths and mark omissions. Merge text comparisons use the first parent but traversal is not confined to that branch. Git's path simplification and rename detection still limit completeness. This is discovery, not an exhaustive history map; see [Git's options](https://git-scm.com/docs/git-log).

`show` without a path reads the commit message; with a path it reads bounded file text. Line boundaries are LF, preserving stored CRLF and other characters. `--max-chars` counts displayed Unicode characters, excluding JSON metadata and escaping. A non-null `next_offset` identifies the first undisplayed character. Continue with the returned full commit ID, same path and `--offset NEXT`; this also recovers a long line's tail. `--offset` is zero-based in complete text, cannot combine with `--start-line`, and `--lines` still limits each page. Use an exact commit when resuming: `HEAD` may move.

Exact-string history finds changes in occurrence count, not every past appearance. A removed passage may be in the parent of a matching change. Inspect why it changed and later corrections before restoring meaning; rediscovery grants no present authority or mandate to restore the old arrangement. A short recovery cue can be useful without a shadow archive.
