# Memory tools

[modules.py](modules.py) discovers indexed memory, captures selected inputs and checks structure and size. [memory.py](memory.py) checks Markdown budgets, which [size_guard.py](size_guard.py) enforces at commit, and recovers bounded Git history. [record.py](record.py) appends the user's typed Claude Code and Codex messages from allowed projects (`projects`, `allow`) to `src/record/` ([template](../memory-template/record/README.md)), redacting credentials; `append` and `check` print counts, never message text. [repeats.py](repeats.py) labels repeated corrections and [lessons.py](lessons.py) logs lesson tests, with ledgers in the instance's `src/research/`; [replay.py](replay.py) replays one on Codex or Claude Code in a sealed copy. [sessions.py](sessions.py) shows where sessions' effort went (`rank`, `timeline`, `failures`). [push_guard.py](push_guard.py) sends a remote not pinned private only Core files a reviewer cleared ([brief](push_review.md)). From another project, run them by absolute path; they default to this repository.

~~~sh
python tools/modules.py list --query writing --limit 5
python tools/modules.py search --query "human feedback" --limit 4 --excerpt-chars 600
python tools/modules.py check
python tools/memory.py sizes --limit 20
python tools/memory.py history --path src/core/memory/reflect.md --limit 5
python tools/record.py append --dry-run
python tools/record.py check
python tools/repeats.py count
python -m unittest discover -s tools/tests
~~~

The [retrieval contract](../src/core/memory/retrieval.md) covers discovery, section search (600-character excerpts by default; `--limit` and `--excerpt-chars` change only the display), roles, assembly and repository validation, and [budgets and history](../src/core/memory/budgets-and-history.md) covers size checks and exceptional Git recovery.

## Budgets

[memory-limits.json](../memory-limits.json) is the shared size policy and names only Core files. An instance keeps allowances for its own memory in an optional `src/memory-limits.json` with `rules` and `files` in the same form; its rules apply after the shared ones and its file limits replace shared ones. [size_guard.py](size_guard.py) `install` adds a pre-commit hook refusing to grow a governed file past its limit (inbox exempt).

## Record check

[record_check.py](record_check.py): `trace BASE..HEAD` finds each passage the net change removes or rewrites in `src/core`, `src/personal`, `integrations/` or root Markdown, retrieves the user's best-matching messages, plus the best two from each date the passage cites, and writes packets for passages not yet judged to `.git/mnemorph-record-check/packets/`. A fresh subagent follows each packet's brief and records `kept`, `superseded`, `not-from-user` or `lost` with `verdict`; rerunning `trace` lists what is lost or left. Periodic reflection runs this audit.

## Matching

`list` and `search` match casefolded query words lexically:

- Whitespace and CJK or fullwidth punctuation separate words. Wrapping quotes, brackets and clause punctuation are dropped, and words without letters or digits are skipped.
- A word matches where a word starts, so `gh` misses `might`, `vision` misses `revision` and `agent` misses `subagent`. A word of one or two characters must be whole. Letters and digits start separate words, so `79` finds `item79`.
- A common English suffix (-s, -es, -ies, -ied, -ed, -ing, -ion, -ation, -ment) is stripped, and the stem then matches when an inflection or nothing completes a word: `reflection` finds `reflected`, `queries` finds `query` and `tests` finds `testing`. Stems keep five letters, four before -s and three before -es, -ies or -ied; -s stays after ss, us and is, and -es is stripped only after s, x, z, ch or sh. So `press`, `species`, `container` and `experiment` miss `present`, `specific`, `contains` and `experience`, and `testing` misses `test`.
- Chinese, Japanese and Korean text splits from adjacent text. A run longer than two characters becomes overlapping pairs: `买咖啡机` is `买咖`, `咖啡` and `啡机`. These pairs, and words starting with punctuation such as `.md`, match anywhere.
- `list` reads identities and summaries. Only when no header contains every term does it read document text after the metadata header; it then reports `FILE_READS`, and a `NOTE` labels text matches.
- When nothing contains every term, the results are the documents or sections containing the most query words, if that is at least two and more than half. A CJK run is one word, present when more than half its pairs are; a query of a single run can match on most of it. A `NOTE` gives the count and names words found nowhere, even when nothing is returned.
- Ranking: partial results put rarer matched terms first, counting summaries and ancestor headings. `list` then orders header matches by whole words, then words as typed, and text matches by header terms, then words as typed; identity breaks ties. `search` then ranks a section's own terms, words as typed and whole words, with path and position breaking ties. Excerpts anchor on the first whole word, else the word as typed, else a stem.

Other wording still misses: `buy` does not find `purchase`.

## Tests

The [module suite](tests/test_modules.py) exercises discovery, query matching, source identity, selection, Markdown references and validation, and checks this repository itself. Its Markdown cases, including LF/CRLF/CR source lines, test the subset the retrieval contract defines; [CommonMark](https://spec.commonmark.org/0.31.2/) is the syntax basis, not a conformance claim. The [memory suite](tests/test_memory.py) covers budget reports, instance allowances and bounded recovery. The [record suite](tests/test_record.py) uses synthetic transcripts of both hosts for inclusion, exclusion, deduplication, idempotent appends, redaction and stance; the [sessions suite](tests/test_sessions.py) uses them too. The [record-check](tests/test_record_check.py), [repeats](tests/test_repeats.py) and [lessons](tests/test_lessons.py) suites use synthetic records and Git histories; the [push-guard suite](tests/test_push_guard.py) pushes to temporary bare repositories.

Passing these tests and checks establishes representation, links and helper behaviour, not semantic recall, prompt quality or good work from the retrieved memory.
