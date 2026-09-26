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
    if a.note:
        entry["note"] = a.note.strip()
    path = root / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"recorded {a.result} for {a.file} @ {commit[:7]} in {LEDGER}")
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


def cmd_candidates(a) -> int:
    """Per guidance file changed in RANGE, the changes with no recorded pass."""
    root = Path(a.root)
    commits = git(root, "rev-list", "--reverse", a.range).split()
    latest: dict[tuple[str, str], str] = {}
    for r in read_ledger(root):
        latest[(r.get("file"), r.get("commit", ""))] = r.get("result", "")
    by_file: dict[str, list[tuple[str, str, str]]] = {}
    for c in commits:
        files = git(root, "diff-tree", "--no-commit-id", "--name-only", "-r",
                    c).split()
        for f in files:
            if not is_guidance(root, c, f):
                continue
            result = latest.get((f, c), "")
            if result == "pass":
                continue
            subject = git(root, "log", "-1", "--format=%s", c).strip()
            by_file.setdefault(f, []).append((c[:7], subject, result or "untested"))
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
