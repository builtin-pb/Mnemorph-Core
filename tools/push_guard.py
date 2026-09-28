#!/usr/bin/env python3
# Mnemorph push guard -- managed by tools/push_guard.py; do not hand-edit.
"""Keep personal Mnemorph memory from reaching a public Git remote.

This one file plays two roles.

Run directly, `install [REMOTE ...]` copies this script, byte for byte, into
the repository's *common* git directory as `hooks/pre-push` (so a linked
worktree is covered too) and records each REMOTE as private with the
multi-valued `mnemorph.private` git config key. A private remote is one an
instance already trusts with its full history, such as its own `origin`;
never a public fork of Core.

Installed as `pre-push`, Git runs this script before every push. For any
remote not recorded private, it requires that every commit the push would
add have a full tree containing only Core paths: anything under
`src/core/`, `tools/`, `integrations/`, `memory-template/`, plus the root
files `AGENTS.md`, `README.md`, `CONTRIBUTING.md`, `LICENSE`,
`memory-limits.json`, `.gitattributes` and `.gitignore`. Anything else
refuses the push. Standard library only, so the copied hook needs nothing
installed beyond Python and Git.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

GUARD_MARKER = "Mnemorph push guard"  # identifies a hook file as ours, any version
CONFIG_KEY = "mnemorph.private"
CORE_DIRS = ("src/core/", "tools/", "integrations/", "memory-template/")
CORE_ROOT_FILES = {
    "AGENTS.md", "README.md", "CONTRIBUTING.md", "LICENSE",
    "memory-limits.json", ".gitattributes", ".gitignore",
}


def is_zero(sha: str) -> bool:
    """True for git's all-zero sha, used for both SHA-1 (40) and SHA-256 (64) repos."""
    return bool(sha) and set(sha) <= {"0"}


def is_core_path(path: str) -> bool:
    return path in CORE_ROOT_FILES or path.startswith(CORE_DIRS)


# --- small git helpers ------------------------------------------------------


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], text=True, capture_output=True)


def git_out(*args: str) -> str:
    r = git(*args)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def git_lines(*args: str) -> list[str]:
    return [line for line in git_out(*args).splitlines() if line.strip()]


def config_get_all(key: str) -> list[str]:
    r = git("config", "--get-all", key)
    if r.returncode != 0:
        return []  # unset
    return [line for line in r.stdout.splitlines() if line.strip()]


def remote_names() -> set[str]:
    return set(git_lines("remote"))


# --- install -----------------------------------------------------------


def cmd_install(args: argparse.Namespace) -> int:
    common_dir = Path(git_out("rev-parse", "--git-common-dir").strip())
    if not common_dir.is_absolute():
        common_dir = Path.cwd() / common_dir
    hooks_dir = common_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-push"

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8", errors="replace")
        if GUARD_MARKER not in existing:
            raise SystemExit(
                f"refusing to install: {hook_path} already exists and is not a "
                "Mnemorph push guard. Remove or back it up, then re-run install."
            )

    source = Path(__file__).resolve().read_text(encoding="utf-8")
    hook_path.write_text(source, encoding="utf-8")
    hook_path.chmod(hook_path.stat().st_mode | 0o111)

    private = set(config_get_all(CONFIG_KEY))
    for remote in args.remotes:
        if remote not in private:
            git_out("config", "--add", CONFIG_KEY, remote)
            private.add(remote)

    where = ", ".join(sorted(private)) if private else "(none)"
    print(f"installed pre-push hook at {hook_path}; private remotes: {where}")
    return 0


# --- the hook itself -----------------------------------------------------


def non_core_paths(commit: str) -> list[str]:
    paths = git_lines("ls-tree", "-r", "--name-only", commit)
    return sorted(p for p in paths if not is_core_path(p))


def commits_to_check(remote: str, named: bool, local_sha: str, remote_sha: str) -> list[str]:
    """Commits reachable from local_sha the push would add: excludes commits
    already reachable from the remote's remote-tracking refs (when remote is a
    configured name) and from remote_sha itself (when we already have it)."""
    rev_args = [local_sha, "--not"]
    if named:
        rev_args.append(f"--remotes={remote}")
    if not is_zero(remote_sha) and git("cat-file", "-e", f"{remote_sha}^{{commit}}").returncode == 0:
        rev_args.append(remote_sha)
    return git_lines("rev-list", "--reverse", *rev_args)


def refuse(remote: str, commit: str, bad: list[str]) -> str:
    subject = git_out("log", "-1", "--format=%s", commit).strip()
    short = git_out("rev-parse", "--short", commit).strip()
    shown = "\n".join(f"  {p}" for p in bad[:5])
    more = f"\n  ... and {len(bad) - 5} more" if len(bad) > 5 else ""
    return (
        f"push_guard: refusing push to {remote}: commit {short} \"{subject}\" "
        f"is not Core-only:\n{shown}{more}\n"
        f"If {remote} is meant to hold personal history, mark it private: "
        f"python3 tools/push_guard.py install {remote}"
    )


def run_hook(argv: list[str]) -> int:
    try:
        return _run_hook(argv)
    except Exception as e:  # fail closed: refuse rather than risk a leak
        print(f"push_guard: refusing push: {e}", file=sys.stderr)
        return 1


def _run_hook(argv: list[str]) -> int:
    if len(argv) < 2:
        raise RuntimeError("missing remote name; not invoked as a pre-push hook")
    remote = argv[1]
    private = set(config_get_all(CONFIG_KEY))
    updates = sys.stdin.read().splitlines()
    if remote in private:
        return 0
    named = remote in remote_names()
    for line in updates:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 4:
            raise RuntimeError(f"unexpected pre-push input line: {line!r}")
        _local_ref, local_sha, _remote_ref, remote_sha = parts
        if is_zero(local_sha):
            continue  # a deletion sends nothing
        for commit in commits_to_check(remote, named, local_sha, remote_sha):
            bad = non_core_paths(commit)
            if bad:
                print(refuse(remote, commit, bad), file=sys.stderr)
                return 1
    return 0


# --- entry point -----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if Path(argv[0]).name == "pre-push":
        return run_hook(argv)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("install", help="install this script as the pre-push hook")
    p.add_argument("remotes", nargs="*", metavar="REMOTE",
                   help="remote(s) allowed to receive full history unchecked")
    p.set_defaults(fn=cmd_install)
    args = ap.parse_args(argv[1:])
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
