#!/usr/bin/env python3
"""Audit prompt and memory text that commits removed or rewrote against the user's own words.

`trace BASE..HEAD` finds each passage the net change between two commits
removes or rewrites in Markdown under src/core, src/personal, integrations/ or
the repository root, retrieves the user's best-matching
messages from src/record, and writes packets for passages not yet judged. A
fresh agent judges each packet and records decisions with `verdict`. A
scheduled reflection, if configured, can run the audit over each day's commits.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
VERDICTS = ("kept", "superseded", "not-from-user", "lost")
TOP_K = 6
PACKET_UNITS = 15
MESSAGE_CHARS, CONTEXT_CHARS = 700, 240

# --- Scope and passages ----------------------------------------------------


def in_scope(path: str) -> bool:
    """Guidance and the user's personal account. The inbox is left out: reflection empties it."""
    return path.endswith(".md") and (path.startswith(("src/core/", "src/personal/", "integrations/")) or "/" not in path)


_ITEM = re.compile(r"([-*+]|\d+[.)])\s")
_TABLE_RULE = re.compile(r"^\|?[\s:|-]+\|?$")


def passages(text: str) -> list[str]:
    """Paragraphs, list items and table rows outside front matter, headings and code blocks."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), 0)
        lines = lines[end + 1:]
    found, block, fence = [], [], None

    def flush():
        if block:
            joined = " ".join(block)
            if len(re.findall(r"\w+", joined)) >= 3:
                found.append(joined)
            block.clear()

    for line in lines:
        stripped = line.strip()
        marker = stripped[:3]
        if fence:
            if marker == fence:
                fence = None
            continue
        if marker in ("```", "~~~"):
            flush()
            fence = marker
        elif not stripped or stripped.startswith("#") or _TABLE_RULE.match(stripped):
            flush()
        elif _ITEM.match(stripped) or stripped.startswith("|"):
            flush()
            block.append(stripped)
        else:
            block.append(stripped)
    flush()
    return found


_STOP = set("""a an and or of to in on for is are be it that this with as by at from not no can should would will
do does we you i my your our their its if but so than then there these those have has had was were been being about
into over under more most less very just also only any all each other such what which who whom when where why how
they them he she his her me us let get got use used""".split())
_WORD = re.compile(r"[a-z0-9]+|[㐀-鿿]+")
_SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "ions", "ion", "ies", "ied", "ed", "es", "ly", "s")


def _stem(word: str) -> str:
    if len(word) > 6 and word.startswith("un"):
        word = word[2:]
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)]
    return word


def terms(text: str) -> list[str]:
    found = []
    for word in _WORD.findall(text.casefold()):
        if word[0] >= "㐀":
            found += [word[i:i + 2] for i in range(len(word) - 1)] or [word]
        elif len(word) > 1 and word not in _STOP:
            found.append(_stem(word))
    return found


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def passage_id(path: str, text: str) -> str:
    return hashlib.sha256(f"{path}\0{_normal(text)}".encode()).hexdigest()[:12]


# --- Git -------------------------------------------------------------------


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def _blobs(root: Path, rev: str) -> dict[str, str]:
    """In-scope path -> blob id at a revision."""
    rows = (line.split("\t", 1) for line in git(root, "ls-tree", "-r", "-z", rev).split("\0") if line)
    return {path: meta.split()[2] for meta, path in rows if in_scope(path)}


def _read(root: Path, blob_ids) -> dict[str, str]:
    blob_ids = sorted(set(blob_ids))
    if not blob_ids:
        return {}
    out = subprocess.run(["git", "-C", str(root), "cat-file", "--batch"], input="\n".join(blob_ids).encode() + b"\n",
                         check=True, capture_output=True).stdout
    texts, position = {}, 0
    for blob in blob_ids:
        header_end = out.index(b"\n", position)
        size = int(out[position:header_end].split()[2])
        texts[blob] = out[header_end + 1:header_end + 1 + size].decode("utf-8", errors="replace")
        position = header_end + 1 + size + 1
    return texts


def removed_passages(root: Path, before: str, after: str) -> list[dict]:
    """Passages at `before` that no longer appear anywhere in scope at `after`."""
    old_blobs, new_blobs = _blobs(root, before), _blobs(root, after)
    changed = sorted(p for p in old_blobs if new_blobs.get(p) != old_blobs[p])
    if not changed:
        return []
    texts = _read(root, [old_blobs[p] for p in changed] + list(new_blobs.values()))
    new_passages = {path: passages(texts[blob]) for path, blob in new_blobs.items()}
    everything = "\n".join(_normal(p) for found in new_passages.values() for p in found)
    touched = [p for p in new_passages if p not in old_blobs or old_blobs[p] != new_blobs[p]]
    removed = []
    for path in changed:
        for text in passages(texts[old_blobs[path]]):
            if _normal(text) in everything:
                continue
            words = set(terms(text))
            best, best_share = "", 0.0
            for candidate_path in ([path] if path in new_blobs else touched):
                for candidate in new_passages.get(candidate_path, []):
                    share = len(words & set(terms(candidate))) / max(1, len(words))
                    if share > best_share:
                        best, best_share = candidate, share
            removed.append({"id": passage_id(path, text), "path": path, "old": text, "new": best,
                            "overlap": round(best_share, 2), "file_deleted": path not in new_blobs})
    return removed


# --- The user's words ------------------------------------------------------


def load_words(root: Path, cutoff: str | None = None) -> list[dict]:
    entries = []
    for path in sorted((root / "src" / "record").glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            entries += [json.loads(raw) for raw in handle if raw.strip()]
    if cutoff:
        entries = [e for e in entries if e["time"] < cutoff]
    return [e for e in entries if (e.get("text") or "").strip()]


class Index:
    """BM25 over the user's messages."""

    def __init__(self, entries: list[dict]):
        self.entries = entries
        self.docs = [collections.Counter(terms(e["text"])) for e in entries]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.average = sum(self.lengths) / max(1, len(self.docs))
        self.df = collections.Counter(t for d in self.docs for t in d)

    def top(self, text: str, k: int = TOP_K, days: set[str] | None = None) -> list[tuple[float, dict]]:
        query, n, scored = set(terms(text)), len(self.docs), []
        for doc, length, entry in zip(self.docs, self.lengths, self.entries):
            if days is not None and entry["time"][:10] not in days:
                continue
            score = 0.0
            for term in query & doc.keys():
                idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                tf = doc[term]
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / self.average))
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda pair: -pair[0])
        return scored[:k]


_MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                         "september", "october", "november", "december"), 1)}
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(" + "|".join(_MONTHS) + r"|[a-z]{3})\.? (\d{1,2})(?:[–-](\d{1,2}))?\b",
                   re.I)


def mentioned_days(text: str, year: str) -> list[set[str]]:
    """For each date a passage cites, such as 'September 15' or 'September 18–19', that UTC day and the next."""
    days = []
    for match in _DATE.finditer(text):
        if match[1]:
            stamps = [(int(match[1]), int(match[2]), int(match[3]))]
        else:
            name = match[4].lower()
            month = _MONTHS.get(name) or next((v for k, v in _MONTHS.items() if k[:3] == name), None)
            if not month:
                continue
            stamps = [(int(year), month, int(d)) for d in (match[5], match[6]) if d]
        for y, m, d in stamps:
            try:
                day = datetime(y, m, d, tzinfo=timezone.utc)
            except ValueError:
                continue
            pair = {day.strftime("%Y-%m-%d"),
                    datetime.fromtimestamp(day.timestamp() + 86400, timezone.utc).strftime("%Y-%m-%d")}
            if pair not in days:
                days.append(pair)
    return days


def candidates(index: Index, text: str, year: str) -> list[dict]:
    """The best matches overall, plus the best two from each date the passage cites, oldest first."""
    found = {e["id"]: e for _, e in index.top(text)}
    for days in mentioned_days(text, year)[:3]:
        found.update({e["id"]: e for _, e in index.top(text, k=2, days=days)})
    return sorted(found.values(), key=lambda e: e["time"])


# --- Packets and verdicts --------------------------------------------------

BRIEF = """An edit to Mnemorph removes or rewrites the passages below. Each is shown with the closest new text and \
the user's own messages (verbatim, from `src/record`) that best match it by wording, oldest first. Retrieval is by \
wording only: the true source may be missing, and most candidates may be unrelated.

For each passage, list its distinct points: each rule, condition, contrast, emphasis and list item. A point is the \
user's when a message states or asks for it; before calling a point the agent's, search `src/record` for its key \
terms and for the dates the passage cites. A question or tentative idea of the user's that became a rule counts as \
their request unless they later withdrew it. For each of the user's points, find where current guidance or the \
subject's current account still says it with the same force; history, evidence, examples and `src/record` do not \
count. Then decide the passage:

- `kept`: every point of the user's is still said (say where).
- `superseded`: the user's later words withdraw or change each point of theirs that is not kept (quote them).
- `not-from-user`: none of its points is the user's.
- `lost`: a point of the user's is dropped, narrowed or weakened; name it and its source message.

Record each decision:

    python3 tools/record_check.py verdict <id> <decision> '<reason, with a quote or location>'

Then return the lost passages. Do not load Mnemorph for this check. The agent running the audit restores each lost \
point and records `kept` with the restoring commit, or reports it to the user; if the user agrees to the loss, it \
records `superseded` with their words."""


def _clip(text: str, limit: int) -> str:
    text = _normal(text)
    return text if len(text) <= limit else text[:limit] + " […]"


def _quote(text: str) -> str:
    return "\n".join("> " + line for line in text.splitlines() or [""])


def packet(items: list[dict], index: Index, title: str, year: str, after: str) -> str:
    parts = [f"# Record check {title}", "", BRIEF, "",
             f"The edit's result is commit `{after}`. Other sessions may have uncommitted changes, so read the "
             f"repository as of that commit: `git show {after}:<path>`, `git grep <term> {after}`."]
    for item in items:
        parts += ["", f"## `{item['id']}` · {item['path']}", "",
                  "Removed or rewritten:" if not item["file_deleted"] else "Removed with its file:", "",
                  _quote(item["old"]), ""]
        if item["new"]:
            parts += [f"Closest new text ({int(item['overlap'] * 100)}% of its terms):", "", _quote(item["new"]), ""]
        else:
            parts += ["No similar new text.", ""]
        matches = candidates(index, item["old"], year)
        parts.append("The user's best-matching messages:" if matches else "No matching messages from the user.")
        for entry in matches:
            label = f"{entry['time'][:16].replace('T', ' ')} UTC · `{entry['id']}` · {entry['kind']}, {entry['stance']}"
            parts += ["", f"- {label}", "", "  " + _quote(_clip(entry["text"], MESSAGE_CHARS)).replace("\n", "\n  ")]
            answered = (entry.get("context") or {}).get("excerpt")
            if answered:
                parts += ["", f"  Answering the agent: {_clip(answered, CONTEXT_CHARS)}"]
    return "\n".join(parts) + "\n"


def _store(root: Path) -> Path:
    common = Path(git(root, "rev-parse", "--git-common-dir").strip())
    return (common if common.is_absolute() else root / common) / "mnemorph-record-check"


def verdicts(root: Path) -> dict[str, dict]:
    path = _store(root) / "verdicts.jsonl"
    latest = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.strip():
                row = json.loads(raw)
                latest[row["id"]] = row
    return latest


def write_packets(root: Path, items: list[dict], index: Index, year: str, after: str) -> list[Path]:
    key = hashlib.sha256("".join(sorted(i["id"] for i in items)).encode()).hexdigest()[:8]
    directory = _store(root) / "packets"
    directory.mkdir(parents=True, exist_ok=True)
    chunks = [items[i:i + PACKET_UNITS] for i in range(0, len(items), PACKET_UNITS)]
    paths = []
    for number, chunk in enumerate(chunks, 1):
        title = key if len(chunks) == 1 else f"{key} ({number} of {len(chunks)})"
        path = directory / (f"{key}.md" if len(chunks) == 1 else f"{key}-{number}.md")
        path.write_text(packet(chunk, index, title, year, after), encoding="utf-8")
        paths.append(path)
    return paths


# --- Commands ----------------------------------------------------------------


def cmd_verdict(args) -> int:
    if args.decision not in VERDICTS:
        print(f"error: decision must be one of {', '.join(VERDICTS)}", file=sys.stderr)
        return 2
    store = _store(args.root)
    store.mkdir(parents=True, exist_ok=True)
    row = {"id": args.id, "verdict": args.decision, "reason": args.reason,
           "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    with (store / "verdicts.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False))
    return 0


def cmd_trace(args) -> int:
    before, _, after = args.range.partition("..")
    before, after = (git(args.root, "rev-parse", "--short=12", rev or "HEAD").strip() for rev in (before, after))
    items = removed_passages(args.root, before, after)
    judged = verdicts(args.root)
    pending = [i for i in items if i["id"] not in judged]
    counts = collections.Counter(judged[i["id"]]["verdict"] for i in items if i["id"] in judged)
    index = Index(load_words(args.root, args.cutoff))
    year = (args.cutoff or datetime.now(timezone.utc).isoformat())[:4]
    paths = write_packets(args.root, pending, index, year, after) if pending else []
    lost = [{"id": i["id"], "path": i["path"], "old": i["old"], "reason": judged[i["id"]]["reason"]}
            for i in items if judged.get(i["id"], {}).get("verdict") == "lost"]
    print(json.dumps({"range": f"{before}..{after}", "passages": len(items), "pending": len(pending),
                      "judged": dict(counts), "lost": lost, "packets": [str(p) for p in paths]},
                     ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--root", type=Path, default=ROOT, help="repository or worktree root")
    commands = result.add_subparsers(dest="command", required=True)
    trace = commands.add_parser("trace", help="write packets for unjudged passages a commit range removes or rewrites")
    trace.add_argument("range", help="BEFORE..AFTER; AFTER defaults to HEAD")
    trace.add_argument("--cutoff", help="use only the user's words before this ISO time, for replaying past edits")
    judge = commands.add_parser("verdict", help="record a judgment of one passage")
    judge.add_argument("id")
    judge.add_argument("decision", help="|".join(VERDICTS))
    judge.add_argument("reason")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    args.root = args.root.resolve()
    try:
        return {"trace": cmd_trace, "verdict": cmd_verdict}[args.command](args)
    except (subprocess.CalledProcessError, OSError, ValueError) as error:
        detail = getattr(error, "stderr", "") or error
        print(f"record check failed: {detail}".strip(), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
