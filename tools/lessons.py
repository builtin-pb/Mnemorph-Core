#!/usr/bin/env python3
"""Record lesson tests and list changed instructions that no test covers.

A lesson carried into an instruction stays a candidate until a replay shows
the instruction changes behavior (src/core/memory/lesson-tests.md). This tool
keeps the replay results in src/research/lesson-tests.jsonl and compares
them with the guidance files a commit range changed. Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("MNEMORPH", Path(__file__).resolve().parents[1]))
LEDGER = Path("src/research/lesson-tests.jsonl")
MODELS = Path("src/lesson-models.json")  # optional: models every lesson must pass on
KINDS = ("concrete", "judgment")
RESULTS = ("pass", "fail", "unverified", "parked")
# Guidance lives under src/ unless it declares role: reference; these never count.
EXCLUDED = (re.compile(r"^src/personal/"), re.compile(r"^src/record/"),
            re.compile(r"^src/inbox\.md$"))


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], text=True,
                       capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def read_ledger(root: Path) -> list[dict]:
    path = root / LEDGER
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{LEDGER}:{n}: {e}")
    return out


def is_guidance(root: Path, rev: str, path: str) -> bool:
    """Instructions: AGENTS.md, integration skill text, and indexed src Markdown
    (an `id` header) that does not declare `role: reference`."""
    if path == "AGENTS.md" or (path.startswith("integrations/") and path.endswith(".md")):
        return True
    if not path.startswith("src/") or not path.endswith(".md"):
        return False
    if any(p.search(path) for p in EXCLUDED):
        return False
    r = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{path}"],
                       text=True, capture_output=True)
    if r.returncode != 0 or not r.stdout.startswith("---"):
        return False  # deleted, or headerless supporting material
    head = r.stdout.split("\n---", 1)[0]
    if not re.search(r"^id:\s*\S", head, re.M):
        return False
    return not re.search(r"^role:\s*reference\s*$", head, re.M)


def outside_reads(root: Path, evidence: str | None) -> list[str]:
    """Replay directories under the evidence path whose contaminated.json is non-empty."""
    if not evidence:
        return []
    base = (root / evidence.split()[0]).resolve()
    base = base if base.is_dir() else base.parent
    hits = []
    for f in sorted(base.glob("**/contaminated.json")):
        try:
            if json.loads(f.read_text()):
                hits.append(f.parent.name)
        except (OSError, json.JSONDecodeError):
            hits.append(f.parent.name)
    return hits


def cmd_record(a) -> int:
    root = Path(a.root)
    commit = git(root, "rev-parse", "--verify", f"{a.commit}^{{commit}}").strip()
    entry = {
        "time": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lesson": a.lesson.strip(),
        "file": a.file,
        "commit": commit,
        "kind": a.kind,
        "case": a.case.strip(),
        "check": a.check.strip(),
        "result": a.result,
        "evidence": a.evidence or "",
    }
    if a.judge:
        entry["judge"] = a.judge.strip()
    if a.result == "parked":
        # A lesson that failed its replays leaves active memory; its text and
        # the commit that removed it stay here for later Learn work.
        if not (a.removed_text and a.parked_by):
            raise SystemExit("parked needs --removed-text and --parked-by")
        text = Path(a.removed_text)
        entry["removed"] = (text.read_text(encoding="utf-8") if text.is_file()
                            else a.removed_text).strip()
        entry["parked_by"] = git(root, "rev-parse", "--verify",
                                 f"{a.parked_by}^{{commit}}").strip()
    for key, val in (("failed_on", a.failed_on), ("replayed_on", a.replayed_on)):
        if val:
            entry[key] = val.strip()
    leaked = outside_reads(root, a.evidence) if a.result == "pass" else []
    if leaked and entry["result"] == "pass":
        # A replay that read evaluation material or state from after the replayed
        # request may have seen the answer; it cannot show the lesson carried.
        entry["result"] = "unverified"
        entry["note"] = (f"{len(leaked)} replay(s) contaminated ({', '.join(leaked[:3])})"
                         + (f"; {a.note.strip()}" if a.note else ""))
    if a.note and "note" not in entry:
        entry["note"] = a.note.strip()
    path = root / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"recorded {entry['result']} for {a.file} @ {commit[:7]} in {LEDGER}")
    return 0


def cmd_list(a) -> int:
    rows = read_ledger(Path(a.root))
    if a.file:
        rows = [r for r in rows if r.get("file") == a.file]
    if a.result:
        rows = [r for r in rows if r.get("result") == a.result]
    for r in rows[-a.limit:]:
        print(f"{r['time']}  {r['result']:<10} {r['kind']:<8} "
              f"{r['file']} @ {r['commit'][:7]}  {r['lesson'][:80]}")
    if not rows:
        print("(no lesson tests recorded)")
    return 0


def mainstream(root: Path) -> list[str]:
    """The models every lesson must pass on, from src/lesson-models.json."""
    try:
        return list(json.loads((root / MODELS).read_text(encoding="utf-8"))["models"])
    except (OSError, ValueError, KeyError):
        return []


def model_key(label: str) -> str:
    """One name per model however a run labels it: claude/opus-5.5,
    claude-code/claude-opus-5-5 and claude-opus-5-5 are the same model."""
    name = (label or "").strip().lower().rsplit("/", 1)[-1].replace(".", "-")
    return name[len("claude-"):] if name.startswith("claude-") else name


def status(rows: list[dict], required: list[str]) -> str:
    """'pass' once the model that missed the lesson passes and every other
    required model does no worse (both arms passing counts). A lesson fixing
    one model's habit cannot gain on models that never had it. Later rows
    for a model supersede earlier ones."""
    if not rows:
        return "untested"
    unknown = {"", "unknown"}
    failing = {model_key(r["failed_on"]) for r in rows if r.get("failed_on")} - unknown
    others = {model_key(m) for m in required} - failing
    latest: dict[str, str] = {}
    for r in rows:
        latest[model_key(r.get("replayed_on", ""))] = r.get("result", "")
    gained = sorted(m for m in latest if latest[m] == "pass")
    if not failing and not gained:
        return rows[-1].get("result", "untested")
    missing = sorted(m for m in failing if m not in latest)
    missing += sorted(m for m in others if m not in latest)
    failed = sorted(m for m in failing if m in latest and latest[m] != "pass")
    failed += sorted(m for m in others if latest.get(m) == "fail")
    if not missing and not failed:
        return "pass"
    parts = [f"passed on {', '.join(gained)}"] if gained else []
    if failed:
        parts.append("not passed on " + ", ".join(failed))
    if missing:
        parts.append("missing " + ", ".join(missing))
    return "; ".join(parts)


def cmd_candidates(a) -> int:
    """Per guidance file changed in RANGE, the changes not yet passed on every
    required model."""
    root = Path(a.root)
    commits = git(root, "rev-list", "--reverse", a.range).split()
    required = mainstream(root)
    tests: dict[tuple[str, str], list[dict]] = {}
    removals: set[str] = set()
    for r in read_ledger(root):
        tests.setdefault((r.get("file"), r.get("commit", "")), []).append(r)
        if r.get("parked_by"):
            removals.add(r["parked_by"])
    by_file: dict[str, list[tuple[str, str, str]]] = {}
    for c in commits:
        files = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r",
                    c).split()
        for f in files:
            if not is_guidance(root, c, f):
                continue
            rows = tests.get((f, c), [])
            if c in removals or any(r.get("result") == "parked" for r in rows):
                continue  # parked lessons and the commits that removed them
            result = status(rows, required)
            if result == "pass":
                continue
            subject = git(root, "log", "-1", "--format=%s", c).strip()
            by_file.setdefault(f, []).append((c[:7], subject, result))
    for f, rows in by_file.items():
        c, subject, result = rows[-1]
        more = f" and {len(rows) - 1} earlier" if len(rows) > 1 else ""
        print(f"candidate  {f}: {result} {c} {subject[:60]}{more}")
        if a.verbose:
            for c, subject, result in rows[:-1]:
                print(f"           {result} {c} {subject[:60]}")
    if not by_file:
        print("no untested guidance changes in range")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("record", help="append one replay result")
    p.add_argument("--lesson", required=True, help="the lesson in one line")
    p.add_argument("--file", required=True, help="changed instruction file")
    p.add_argument("--commit", required=True, help="commit that changed it")
    p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--case", required=True, help="task replayed")
    p.add_argument("--check", required=True,
                   help="the mechanical check, or the judge brief")
    p.add_argument("--result", required=True, choices=RESULTS)
    p.add_argument("--evidence", help="path to outputs and verdicts")
    p.add_argument("--judge", help="who judged: script, model, or the person")
    p.add_argument("--failed-on", help="host/model where the miss happened, "
                   "e.g. codex/gpt-6-astra")
    p.add_argument("--replayed-on", help="host/model the replay ran on; record "
                   "one replay per model")
    p.add_argument("--removed-text", help="for parked: the removed instruction, "
                   "or a file holding it")
    p.add_argument("--parked-by", help="for parked: the commit that removed it")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("list", help="show recorded tests")
    p.add_argument("--file")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--result", choices=RESULTS, help="only rows with this result")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("candidates",
                       help="guidance changes in a commit range with no test")
    p.add_argument("range", help="e.g. BASE..HEAD")
    p.add_argument("--verbose", action="store_true", help="list every change")
    p.set_defaults(fn=cmd_candidates)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
