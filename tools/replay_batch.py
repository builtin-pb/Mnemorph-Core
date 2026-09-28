#!/usr/bin/env python3
"""Run a batch of agent replays safely: one canary first, then bounded fan-out.

Each line of the jobs file is `ID ARGS...`: the arguments for one
`replay.py TOOL` run (--tool codex, codex-fork, claude or claude-fork; default codex) without
--out, which becomes OUT/ID. Each job goes through replay.py, so an installed
copy's refusals apply to it; --runner (another script) works only from a
checkout. The batch:

- skips IDs whose OUT/ID/last.md already exists (resumable);
- runs the first remaining job alone and stops unless it exits 0 with a
  non-empty reply and an empty contaminated.json (so a broken or leaking
  harness costs one run);
- then runs the rest with --parallel workers, stopping new launches after
  --max-failures consecutive failures;
- appends one JSON line per finished job to OUT/status.jsonl (id, exit,
  seconds, words, contaminating reads, ok) and a heartbeat line every --heartbeat
  seconds while jobs run, so a watcher wakes even when nothing finishes;
- ends with a line whose "event" is "done", with counts.

Watch it with `tail -n 0 -F OUT/status.jsonl`. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import replay_common as common  # noqa: E402

LOCK = threading.Lock()
TOOL_NAMES = ("codex", "codex-fork", "claude", "claude-fork")


def log(status: Path, record: dict) -> None:
    record["time"] = time.strftime("%H:%M:%S")
    with LOCK, status.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False), flush=True)


def run_job(runner: list[str], out: Path, jid: str, args: list[str]) -> dict:
    d = out / jid
    t0 = time.time()
    r = subprocess.run([*runner, *args, "--out", str(d)], text=True, capture_output=True)
    last = d / "last.md"
    words = len(last.read_text(encoding="utf-8").split()) if last.exists() else 0
    bad_file = d / "contaminated.json"
    reads = json.loads(bad_file.read_text()) if bad_file.exists() else []
    ok = r.returncode == 0 and words > 0
    return {"event": "job", "id": jid, "exit": r.returncode, "seconds": int(time.time() - t0),
            "words": words, "contaminated": len(reads), "ok": ok,
            "error": "" if ok else (r.stderr or r.stdout)[-300:]}


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog=prog, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("jobs", help="file of lines: ID ARGS... (replay tool arguments without --out)")
    ap.add_argument("--out", required=True, help="output directory; each job writes OUT/ID")
    ap.add_argument("--tool", choices=TOOL_NAMES, default="codex",
                    help="replay.py subcommand each job runs (default codex)")
    ap.add_argument("--runner", help="another replay script instead of replay.py TOOL (checkout only)")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--max-failures", type=int, default=3,
                    help="stop launching after this many consecutive failures")
    ap.add_argument("--heartbeat", type=int, default=600, help="seconds between heartbeat lines")
    a = ap.parse_args(argv)
    common.guard(ap, a, None)

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    status = out / "status.jsonl"
    runner = ([sys.executable, str(Path(a.runner).resolve())] if a.runner
              else [sys.executable, str(TOOLS / "replay.py"), a.tool])
    jobs = []
    for line in Path(a.jobs).read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            jid, *args = shlex.split(line)
            if not (out / jid / "last.md").exists():
                jobs.append((jid, args))
    counts = {"ok": 0, "failed": 0, "contaminated": 0, "skipped": 0}
    if not jobs:
        log(status, {"event": "done", **counts, "note": "nothing to run"})
        return 0

    def record(res: dict) -> None:
        counts["ok" if res["ok"] else "failed"] += 1
        counts["contaminated"] += res["contaminated"] > 0
        log(status, res)

    canary = run_job(runner, out, *jobs[0])
    record(canary)
    if not canary["ok"] or canary["contaminated"]:
        counts["skipped"] = len(jobs) - 1
        log(status, {"event": "done", **counts, "note": "canary failed or was contaminated; batch stopped"})
        return 1

    running = {"n": 0}
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(a.heartbeat):
            log(status, {"event": "heartbeat", "running": running["n"], **counts})

    threading.Thread(target=beat, daemon=True).start()
    streak = 0
    pending = list(jobs[1:])
    with ThreadPoolExecutor(max_workers=a.parallel) as pool:
        futures = {}
        while pending or futures:
            while pending and len(futures) < a.parallel and streak < a.max_failures:
                jid, args = pending.pop(0)
                futures[pool.submit(run_job, runner, out, jid, args)] = jid
                running["n"] = len(futures)
            if not futures:
                break
            done = next(as_completed(futures))
            del futures[done]
            running["n"] = len(futures)
            res = done.result()
            record(res)
            streak = 0 if res["ok"] else streak + 1
    stop.set()
    counts["skipped"] = len(pending)
    note = f"stopped after {a.max_failures} consecutive failures" if pending else ""
    log(status, {"event": "done", **counts, "note": note})
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
