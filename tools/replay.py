#!/usr/bin/env python3
"""Replay agent tasks for lesson tests (src/core/memory/lesson-tests.md).

usage: replay.py SUBCOMMAND [OPTIONS]

  codex        one `codex exec` from a sealed copy of --root (codex_replay.py)
  codex-fork   one turn of a real Codex session at its real distance (codex_fork_replay.py)
  claude       one `claude -p` from a sealed copy of --root (claude_replay.py)
  claude-fork  one turn of a real Claude Code session at its real distance (claude_fork_replay.py)
  batch        a canary job, then bounded parallel replays (replay_batch.py)
  check        evidence for a model judge: what a turn used that a replay lacks (replay_check.py)
  install DEST copy the replay files into DEST, e.g. ~/.local/share/mnemorph/replay

`replay.py SUBCOMMAND --help` lists each one's options. --root is the
repository to replay; it defaults to the repository these files live in, or for
an installed copy the one it was installed from (another instance works too).
Each run writes events.jsonl, last.md, changes.diff, outside-reads.json,
contaminated.json and manifest.json (host, model, effort, verbosity if set, CLI
version, flags, commit, token usage) to --out. The forks take the message by
--line or by its record id (--record codex:<message> or claude:<uuid>).

An installed copy, run from outside a Git checkout under a standing allow rule,
refuses --no-seal, --prune-trust, a fork's --cwd, a batch's --runner, `install`,
any --copy source outside --root, and `check` output outside --root. Each run
applies the seal workarounds (a local `origin`, the `.mnemorph-local` of the
time, the sessions the turn names, plain folder names; --no-* turns one off) and
saves its fidelity digest as check.json. Standard library only.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import shutil
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import replay_common as common  # noqa: E402

MODULES = {"codex": "codex_replay", "codex-fork": "codex_fork_replay",
           "claude": "claude_replay", "claude-fork": "claude_fork_replay", "batch": "replay_batch",
           "check": "replay_check"}


def install(args: list[str]) -> int:
    if len(args) != 1 or args[0].startswith("-"):
        print("usage: replay.py install DEST", file=sys.stderr)
        return 2
    repo = common.tool_repo()
    if repo is None:
        print("replay.py: an installed copy refuses `install`; install from a checkout", file=sys.stderr)
        return 2
    dest = Path(args[0]).expanduser().resolve()
    if dest == TOOLS:
        print("replay.py: DEST is this checkout's tools directory", file=sys.stderr)
        return 2
    dest.mkdir(parents=True, exist_ok=True)
    for name in common.FILES:
        target = dest / name
        if target.exists():
            target.unlink()  # earlier installs are read-only
        shutil.copyfile(TOOLS / name, target)
        os.chmod(target, 0o555 if name == "replay.py" else 0o444)  # the judge's brief comes along
    marker = dest / common.MARKER.name
    if marker.exists():
        marker.unlink()
    marker.write_text(json.dumps({
        "root": str(repo), "commit": common.git_out(repo, "rev-parse", "HEAD"),
        "installed": dt.datetime.now().astimezone().isoformat(timespec="seconds")}, indent=1) + "\n",
        encoding="utf-8")
    os.chmod(marker, 0o444)
    print(f"installed {len(common.FILES)} files to {dest}; default --root {repo}")
    print(f"run: python3 {dest / 'replay.py'} SUBCOMMAND ...")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    sub, rest = argv[0], argv[1:]
    if sub == "install":
        return install(rest)
    if sub not in MODULES:
        print(f"replay.py: unknown subcommand {sub!r}; choose from "
              f"{', '.join([*MODULES, 'install'])}", file=sys.stderr)
        return 2
    return importlib.import_module(MODULES[sub]).main(rest, prog=f"replay.py {sub}")


if __name__ == "__main__":
    sys.exit(main())
