---
id: core.memory.retrieval
role: reference
summary: The discovery, section search, selection, assembly and validation contract of the module helper, tools/modules.py.
---

# Find and load relevant memory

Start from [AGENTS.md](../../../AGENTS.md) and [Entry](../__entry__.md). The Python helper discovers memory, checks references and captures explicitly selected content. It does not execute prompts or decide whether their conditions apply.

## Discover documents and sections

~~~sh
python tools/modules.py list --query writing
python tools/modules.py list --query writing --primary-only
python tools/modules.py search --query 'human correction' --limit 5 --excerpt-chars 1200
~~~

Run from the framework root, or use the absolute helper path from another project. `list` matches identities and summaries, reading document text only when no header has every term. `--primary-only` narrows guidance eligible to supply an unlabelled task's method; it does not limit context or knowledge retrieval. `search` reads indexed bodies and returns bounded matching sections with source lines and identities. Read surrounding context before reliance.

The default output limit is 20; `--limit` changes it. `list --all` requests all matches. Section search needs a nonempty query and rejects `--all` and `--primary-only`. [Matching](../../../tools/README.md#matching) is lexical: words match where words start, inflections share a stem and CJK matches as character pairs. Without a full match, results contain most query words and a `NOTE` says so. Other wording still misses, so vary terms or follow a known subject. Summaries and ancestor headings help matching; among full matches they do not raise section rank. Ordering and snippets are lexical leads, not a judgment of usefulness or authority.

Source lines end at LF, CRLF or CR; other Unicode separators remain content. File identities hash original bytes. Warnings identify catalogue defects; no match does not establish that useful memory is absent. For a relevant failure, distinctive niche match or explicit historical question, use the separate [history helper](budgets-and-history.md#recover-history-exceptionally). Ordinary `list`, `search` and `assemble` do not run it.

## Declare use independently of location

Under `src/`, a leading `---` opts into a module header with unique `id`, `summary`, optional `role` and optional exact `primary: true`. Headerless Markdown is supporting material, available by path or link. Malformed opt-in headers are errors. Keep arbitrary YAML-fronted source documents outside this interface, or preserve historical treatments as plain text.

- `role: guidance` is the default and permits explicit instruction selection.
- `role: reference` marks factual accounts, examples, evaluations and historical records. They remain context, but cannot be prompt selections or an assembly root.
- `primary: true` belongs only to guidance eligible for an unlabelled task's method; omit it elsewhere.

Role semantics and authority are governed by [Memory](memory.md#provenance-and-use).

## Capture a reproducible selection

Read known relevant memory directly for ordinary tasks. Use assembly when a change, handoff or experiment needs an exact content basis:

~~~sh
python tools/modules.py assemble --prompt 'core.entry:taste/taste.md|judgment for this task' --prompt 'core.entry:learn/learn.md|developing a capability'
python tools/modules.py assemble --context 'src/core/memory/task-history.md|understand native retrieval limits'
~~~

Entry is always included. Entry requires Taste, but the helper does not follow that instruction automatically; select and read needed bodies deliberately. A context selection does not recursively capture linked fixtures or examples.

| Argument | Selection |
| --- | --- |
| `--seed 'id-or-path|purpose'` | Independently selected governing guidance. |
| `--context 'id-or-path|purpose'` | Independently selected reference material. |
| `--prompt 'owner.id:relative-link|task evidence'` | Guidance linked from an active owner's body. |
| `--support 'owner.id:relative-link|task evidence'` | Linked data, without activating instructions. |

Repeat arguments as needed. Seed paths are repository-relative; links resolve from their source. The agent interprets body conditions. Repeated references are legal, a citation loads nothing, and an unselected condition is not certified false. Keep evaluator-only material out of a fresh executor's inputs.

## Identity and validation

Assembly reports paths, purposes, a digest and sizes. The digest covers captured bytes and roles; a Git revision is only a hint about mutable contents. A Python `Graph` retains its first read of each file, so use a fresh graph after an error or when adopting changed sources. [Memory](memory.md) governs destination reconciliation.

~~~sh
python tools/modules.py check
~~~

The checker validates modules and local Markdown links repository-wide. Reference reach is syntactic, not semantic dependency or approval. It supports inline and reference links, escapes, titles and relevant block boundaries. Code examples, images, HTML, autolinks and external links declare no local dependencies. This is not full CommonMark; parser cases live with helper tests. On success, `check` reports counts and at most 20 reach witnesses; `--limit` changes that display and `--all` shows every witness. Errors remain visible. Only output count, not validation work, is bounded.

Frozen treatments, outputs and manifests keep historical identities. Their subject entry explains present use. Do not rewrite old treatments as current ones. When investigating overhead, measure discovery and assembly output and duration separately, counting skipped operations as zero. Ordinary tasks need no cost report. Search snippets, assembly and checks do not establish that retrieved memory improved the work.
