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
RESULTS = ("pass", "fail", "unverified")
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
    for key, val in (("failed_on", a.failed_on), ("replayed_on", a.replayed_on)):
        if val:
            entry[key] = val.strip()
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
    """'pass' once each required model, and the model that missed the lesson,
    has a passing replay; otherwise what is missing. Models differ in habit,
    so a pass on one does not show the lesson carries on another."""
    unknown = {"", "unknown"}
    need = {model_key(m) for m in required} | {
        model_key(r["failed_on"]) for r in rows if r.get("failed_on")} - unknown
    passed = {model_key(r.get("replayed_on", "")) for r in rows if r.get("result") == "pass"}
    if not rows:
        return "untested"
    if not need:
        return "pass" if passed else rows[-1].get("result", "untested")
    missing = sorted(need - passed)
    if not missing:
        return "pass"
    have = sorted(need & passed)
    return (f"passed on {', '.join(have)}; " if have else "") + "missing " + ", ".join(missing)


def cmd_candidates(a) -> int:
    """Per guidance file changed in RANGE, the changes not yet passed on every
    required model."""
    root = Path(a.root)
    commits = git(root, "rev-list", "--reverse", a.range).split()
    required = mainstream(root)
    tests: dict[tuple[str, str], list[dict]] = {}
    for r in read_ledger(root):
        tests.setdefault((r.get("file"), r.get("commit", "")), []).append(r)
    by_file: dict[str, list[tuple[str, str, str]]] = {}
    for c in commits:
        files = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r",
                    c).split()
        for f in files:
            if not is_guidance(root, c, f):
                continue
            result = status(tests.get((f, c), []), required)
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
    p.add_argument("--note")
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("list", help="show recorded tests")
    p.add_argument("--file")
    p.add_argument("--limit", type=int, default=30)
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
