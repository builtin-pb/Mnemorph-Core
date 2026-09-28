# Mnemorph repository

Mnemorph root: src/core/__entry__.md
Mnemorph narrow route: requested module
Mnemorph support: tools/modules.py

Load Mnemorph on every task, beginning with `src/core/__entry__.md`. Resolve framework paths from this repository and links from their source file. From another project, run the helper by its absolute path; keep that project as the work destination.

Read relevant memory as well as the prompts you need. Use bounded `list` queries for document summaries and `search` for particulars inside documents, including when a question needs context or knowledge without a workflow. Bodies explain when to use other memory; a link alone loads nothing. Only `primary` guidance entries compete to supply an unlabelled task's method; they do not limit memory discovery. Reference material and historical permissions do not become active instructions. No matching method permits direct work.

Keep the source basis and relevant selections in task context. Use `assemble` when a change or experiment needs reproducible inputs. It captures identity, not semantic correctness. Measure its cost when investigating overhead.

One agent owns the result. Keep useful memory and recoverable changes in Git, and unfinished or private state in native workspace storage. Reconcile concurrent edits before adoption. Several sessions may share one checkout: commit only the paths you changed (`git commit -- <paths>`), and never use `git add -A`, `git commit -a`, stash or reset. Return a result, an actual blocker or a recoverable continuation.

This checkout is Mnemorph-Core or an instance derived from it. Core is `src/core/`, `tools/`, `integrations/`, `memory-template/` and the root files; in an instance, the rest of `src/` is the instance's memory. Commit Core changes apart from memory changes, touching only Core paths, so they can be offered upstream. The `upstream` remote is Mnemorph-Core: take official changes from it or offer Core changes to it only when the user asks, as `README.md` and `CONTRIBUTING.md` describe. Personal material, meaning memory outside `src/core/` and anything drawn from it, never enters a Core commit or goes upstream.
