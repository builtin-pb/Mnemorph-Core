#!/usr/bin/env python3
"""Refuse a commit that leaves a governed memory file over its character limit.

`install` writes a small `pre-commit` hook into the repository's common git
directory (so linked worktrees are covered) that runs this checkout's own
copy of this script. Git then runs `hook` before every commit: for each file
the commit adds or changes that memory-limits.json governs, it measures the
staged text, as memory.py's size check does for the working tree, and
refuses the commit when that text is over its limit and longer than the
version in HEAD. Shrinking a file that is already over is allowed, so a
partial fix can land. `src/inbox.md` is exempt: it is a same-day queue that
nightly reflection empties. A `git commit -- <paths>` commit checks only its
own paths. If the guard itself fails, it warns and lets the commit through.
Standard library only.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory  # noqa: E402

MARKER = "Mnemorph size guard"
EXEMPT = {"src/inbox.md"}
HOOK = f"""#!/bin/sh
# {MARKER}: installed by tools/size_guard.py; runs this checkout's own copy.
guard="$(git rev-parse --show-toplevel)/tools/size_guard.py"
[ -f "$guard" ] || exit 0
exec python3 "$guard" hook
"""


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True)


def text_at(root: Path, spec: str) -> str | None:
    r = git(root, "show", spec)
    if r.returncode != 0:
        return None
    try:
        body = r.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in body else body


def limit_for(config: dict, name: str, body: str) -> int | None:
    """The file's limit, or None when memory-limits.json does not govern it."""
    direct = any(memory.glob_matches(name, p) for p in config["include"])
    indexed = config["indexed_src"] and name.startswith("src/") and name.endswith(".md")
    if not direct and not (indexed and memory.indexed_src_markdown(name, body)):
        return None
    limit = config["default"]
    for rule in config["rules"]:
        if memory.glob_matches(name, rule["pattern"]):
            limit = rule["limit"]
    return config["files"].get(name, limit)


def violations(root: Path) -> list[str]:
    config = memory.load_limits(root)
    staged = git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").stdout
    found = []
    for name in sorted(p for p in staged.decode("utf-8").split("\x00") if p):
        if name in EXEMPT:
            continue
        body = text_at(root, f":{name}")
        if body is None:
            continue
        limit = limit_for(config, name, body)
        if limit is None or len(body) <= limit:
            continue
        before = text_at(root, f"HEAD:{name}")
        if before is not None and len(body) <= len(before):
            continue
        found.append(f"{name}: {len(body)} characters, limit {limit}"
                     + (f", HEAD had {len(before)}" if before is not None else ""))
    return found


def cmd_hook(_args) -> int:
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").stdout.decode().strip() or ".")
    try:
        found = violations(root)
    except Exception as exc:  # a broken guard must not block everyone's commits
        print(f"size_guard: not checked ({exc})", file=sys.stderr)
        return 0
    if not found:
        return 0
    print("size_guard: this commit would leave memory over its size limit:", file=sys.stderr)
    for line in found:
        print(f"  {line}", file=sys.stderr)
    print("Reconstruct the file to fit (src/core/memory/memory.md, \"Reading budgets\") "
          "or adopt a reviewed exact allowance in memory-limits.json, then commit again.",
          file=sys.stderr)
    return 1


def cmd_install(_args) -> int:
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").stdout.decode().strip() or ".")
    common = Path(git(root, "rev-parse", "--git-common-dir").stdout.decode().strip())
    hooks = (common if common.is_absolute() else root / common) / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    path = hooks / "pre-commit"
    if path.exists() and MARKER not in path.read_text(encoding="utf-8", errors="replace"):
        raise SystemExit(f"refusing to install: {path} exists and is not a {MARKER}")
    path.write_text(HOOK, encoding="utf-8")
    path.chmod(0o755)
    print(f"installed {path}; undo: delete it")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="install the pre-commit hook").set_defaults(fn=cmd_install)
    sub.add_parser("hook", help="run the check (as Git's pre-commit hook)").set_defaults(fn=cmd_hook)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
