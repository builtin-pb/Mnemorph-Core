#!/usr/bin/env python3
"""Replay a task on Codex from a throwaway copy of this repository.

Lesson tests replay a miss on the model that made it
(src/core/memory/lesson-tests.md). This runs one non-interactive `codex exec`
from a fresh clone at a given commit, with an optional patch applied and
committed and optional files copied in (for example gitignored working state),
then writes the event log and final reply to an output directory.

The run cannot act outside its copy: connectors, plugins, browsers and computer
use are off, shell writes are sandboxed to the copy, and escalation requests are
refused (approval policy "never"). The copy has no Git remote. Standard library
only; needs the `codex` CLI.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DISABLED = ("apps", "plugins", "computer_use", "browser_use",
            "browser_use_external", "in_app_browser")


def run(cmd: list[str], **kw) -> None:
    r = subprocess.run(cmd, text=True, capture_output=True, **kw)
    if r.returncode != 0:
        raise SystemExit(f"{' '.join(cmd[:3])}…: {r.stderr.strip() or r.stdout.strip()}")


def codex_command(copy: Path, out: Path, a) -> list[str]:
    cmd = ["codex", "exec", "-C", str(copy), "--ephemeral", "--json",
           "-o", str(out / "last.md"), "-s", "workspace-write",
           "-c", 'approval_policy="never"']
    for feature in DISABLED:
        cmd += ["--disable", feature]
    if a.model:
        cmd += ["-m", a.model]
    if a.effort:
        cmd += ["-c", f'model_reasoning_effort="{a.effort}"']
    for image in a.image or []:
        cmd += ["-i", str(Path(image).resolve())]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--commit", required=True, help="tree the replay starts from")
    ap.add_argument("--prompt-file", required=True, help="the user's request, verbatim")
    ap.add_argument("--out", required=True, help="directory for events.jsonl and last.md")
    ap.add_argument("--patch", help="patch applied and committed before the run")
    ap.add_argument("--copy", action="append", metavar="SRC=DEST",
                    help="file copied into the copy at DEST (relative), e.g. gitignored state")
    ap.add_argument("--image", action="append", help="image attached to the request")
    ap.add_argument("--model", help="Codex model; default from Codex config")
    ap.add_argument("--effort", help="reasoning effort; default from Codex config")
    ap.add_argument("--keep", action="store_true", help="keep the copy for inspection")
    ap.add_argument("--dry-run", action="store_true", help="print the codex command only")
    a = ap.parse_args()

    out = Path(a.out).resolve()
    work = Path(tempfile.mkdtemp(prefix="mnemorph-replay-"))
    copy = work / "Mnemorph"
    cmd = codex_command(copy, out, a)
    if a.dry_run:
        print(" ".join(cmd), "<", a.prompt_file)
        shutil.rmtree(work)
        return 0
    out.mkdir(parents=True, exist_ok=True)
    try:
        run(["git", "clone", "-q", str(ROOT), str(copy)])
        run(["git", "-C", str(copy), "checkout", "-q", "-B", "main", a.commit])
        run(["git", "-C", str(copy), "remote", "remove", "origin"])
        if a.patch:
            run(["git", "-C", str(copy), "apply", str(Path(a.patch).resolve())])
            run(["git", "-C", str(copy), "add", "-A"])
            run(["git", "-C", str(copy), "commit", "-q", "-m", "Replay variant"])
        for spec in a.copy or []:
            src, dest = spec.split("=", 1)
            target = copy / dest
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
        with open(a.prompt_file, encoding="utf-8") as stdin, \
                open(out / "events.jsonl", "w") as events, \
                open(out / "stderr.txt", "w") as err:
            code = subprocess.run(cmd, stdin=stdin, stdout=events, stderr=err).returncode
        print(f"{out}: codex exit {code}")
        return code
    finally:
        if a.keep:
            print(f"copy kept at {copy}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
