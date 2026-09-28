#!/usr/bin/env python3
"""Replay one turn of a real Codex session at its real distance.

Lesson tests replay a lapse where it happened (src/core/memory/lesson-tests.md):
turns or hours after the instruction it broke, with that session's context. This
copies the session's rollout up to (not including) a given line into a fresh
CODEX_HOME, forks it, and sends the next user message again (optionally with a
restated instruction appended), with a chosen model and effort.

The message is given by --line (the rollout's `response_item` line; a record's
line, one higher on the UserMessage event, or a goal event's line is normalized
to it) or by --record, an id from src/record (`codex:<message>` or
`codex:goal:...`), which also supplies the session file. The history the fork
needs comes along unchanged: the page each `history_base` names (the previous
page of a paginated thread, `rollout-..._<segment>.jsonl`, or the thread it was
forked from) and each `forked_from_id` thread, followed to the first page.

The fork gets its own CODEX_HOME (the user's config copied, auth linked, skills
and the Mnemorph link pointing at a copy of --root, default this repository, at
the last commit before the replayed message, or --commit, with --patch applied).
It works in that copy when the session ran in --root, in a clone of --project
when it ran there, and otherwise in an empty directory; --cwd overrides (refused
by an installed copy). As in codex_replay.py, the sandbox's write access to /tmp
and $TMPDIR is off, and the outputs are events.jsonl, last.md, changes.diff
(memory.diff for the copy when the fork worked elsewhere), outside-reads.json,
contaminated.json, replayed.json and manifest.json; exclude runs whose
contaminated.json is non-empty. Standard library only; needs `codex`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shlex
import shutil
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_replay as replay  # noqa: E402
import replay_common as common  # noqa: E402

WINDOW = 50  # lines searched around a record's line for its message


def parse(raw: str) -> dict:
    try:
        e = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return e if isinstance(e, dict) else {}


def payload(entry: dict) -> dict:
    return entry.get("payload") if isinstance(entry.get("payload"), dict) else {}


def user_text(entry: dict) -> str | None:
    p = payload(entry)
    if entry.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "user":
        return " ".join(c.get("text", "") for c in p.get("content", []) if isinstance(c, dict))
    return None


def event_item(entry: dict) -> dict | None:
    p = payload(entry)
    if entry.get("type") == "event_msg" and p.get("type") == "item_completed" and isinstance(p.get("item"), dict):
        return p["item"]
    return None


def goal_key(entry: dict) -> str | None:
    """The record's id for a goal event (as tools/record.py makes it), else None."""
    p = payload(entry)
    if entry.get("type") != "event_msg" or p.get("type") != "thread_goal_updated":
        return None
    goal = p.get("goal") or {}
    objective = goal.get("objective")
    if not isinstance(objective, str) or not objective.strip():
        return None
    return f"goal:{goal.get('createdAt')}:{hashlib.sha256(objective.encode()).hexdigest()[:12]}"


def locate(lines: list[str], rid: str) -> int:
    """The 1-based line of record `rid`: its UserMessage event, or its goal event."""
    key = rid.split(":", 1)[1]
    needle = "thread_goal_updated" if key.startswith("goal:") else key
    for n, raw in enumerate(lines, 1):
        if needle in raw:
            e = parse(raw)
            item = event_item(e)
            if goal_key(e) == key or (item and item.get("type") == "UserMessage" and item.get("id") == key):
                return n
    raise SystemExit(f"record {rid} is not in the session file")


def normalize(lines: list[str], n: int) -> int:
    """The `response_item` line to fork at: n itself, the message just before a
    UserMessage event (a record's line), or the message a goal event injects next."""
    if not 1 <= n <= len(lines):
        raise SystemExit(f"line {n} is outside the session's {len(lines)} lines")
    e = parse(lines[n - 1])
    if user_text(e) is not None:
        return n
    item = event_item(e)
    if item and item.get("type") == "UserMessage":
        for m in range(n - 1, max(0, n - WINDOW), -1):
            prev = parse(lines[m - 1])
            if user_text(prev) is not None:
                return m
            if payload(prev).get("role") == "assistant" or (event_item(prev) or {}).get("type") == "UserMessage":
                break
    elif goal_key(e):
        for m in range(n + 1, min(len(lines), n + WINDOW) + 1):
            if user_text(parse(lines[m - 1])) is not None:
                return m
    raise SystemExit(f"line {n} is not a user message, a UserMessage event or a goal")


def history_refs(meta: dict) -> list[str]:
    """Rollout ids a session's history depends on: its history_base page and the thread it forked from."""
    p = payload(meta)
    base = p.get("history_base") if isinstance(p.get("history_base"), dict) else {}
    return [i for i in (base.get("thread_id"), p.get("forked_from_id")) if isinstance(i, str) and i]


def copy_ancestors(meta: dict, sessions: Path) -> list[str]:
    """Copy, unchanged, every rollout page the history depends on; returns their names.

    An id names the file ending in `-<id>.jsonl` (a thread's first page) or
    `_<id>.jsonl` (a later page); each copied page's own references are followed.
    """
    real = replay.user_home() / "sessions"
    pending, seen, copied = history_refs(meta), set(), []
    while pending:
        rid = pending.pop()
        if rid in seen:
            continue
        seen.add(rid)
        for f in sorted(real.glob(f"*/*/*/*{rid}.jsonl")):
            dest = sessions / f.relative_to(real)
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dest)
                copied.append(f.name)
            with f.open(encoding="utf-8", errors="ignore") as handle:
                pending += history_refs(parse(handle.readline()))
    return copied


def fork_command(out: Path, sid: str, a) -> list[str]:
    cmd = ["codex", "exec", "fork", "--json", "-o", str(out / "last.md"),
           "-c", 'sandbox_mode="workspace-write"', *replay.isolation_options(),
           "--skip-git-repo-check"]
    return cmd + replay.model_options(a) + [sid, "-"]


def write_fork(lines: list[str], line: int, sid: str, cutoff: float, home: Path, cwd: Path) -> Path:
    """The session up to the replayed message as a new rollout `sid`, set in `cwd`."""
    day = dt.datetime.fromtimestamp(cutoff)
    sdir = home / "sessions" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
    sdir.mkdir(parents=True, exist_ok=True)
    kept = []
    for raw in lines[:line - 1]:
        if not raw.strip():
            continue
        e = json.loads(raw)
        pl = e.get("payload") if isinstance(e.get("payload"), dict) else None
        if pl is not None:
            if e.get("type") == "session_meta":
                pl["id"] = sid
            if "cwd" in pl:  # fork runs where the session says it ran
                pl["cwd"] = str(cwd)
            for key in ("workspace_roots", "runtime_workspace_roots"):
                if key in pl:
                    pl[key] = [str(cwd)]
        kept.append(json.dumps(e, ensure_ascii=False))
    path = sdir / f"rollout-{day:%Y-%m-%dT%H-%M-%S}-{sid}.jsonl"
    path.write_text("\n".join(kept) + "\n")
    return path


def session_cwd(lines: list[str], line: int) -> str | None:
    cwd = None
    for raw in lines[:line - 1]:
        if '"cwd"' in raw:
            value = payload(parse(raw)).get("cwd")
            cwd = value if isinstance(value, str) else cwd
    return cwd


def parser(prog: str | None = None) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=prog, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common.add_fork_args(ap, "codex")
    ap.add_argument("--verbosity")
    return ap


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    ap = parser(prog)
    a = ap.parse_args(argv)
    root = common.resolve_root(a.root, ap)
    common.guard(ap, a, root)
    rid = common.record_id(ap, a, "codex")
    session = common.session_file(ap, a, root, rid)

    lines = session.read_text(encoding="utf-8", errors="ignore").split("\n")
    line = normalize(lines, locate(lines, rid) if rid else a.line)
    target = parse(lines[line - 1])
    prompt = user_text(target).strip() + (("\n\n" + a.append.strip()) if a.append else "")
    cutoff = dt.datetime.fromisoformat(target["timestamp"].replace("Z", "+00:00")).timestamp()
    commit, live_project, pcommit = common.fork_commits(a, root, cutoff)
    import replay_check
    check, turn = replay_check.digest_fork(session, line, root, "codex")

    out = Path(a.out).resolve()
    sid = str(uuid.uuid4())
    if a.dry_run:
        print(shlex.join(fork_command(out, sid, a)), f"< line {line} of {session}")
        print(replay_check.summary(check))
        print(common.fidelity_note(a, check))
        return 0
    out.mkdir(parents=True, exist_ok=True)
    common.save_check(out, check)
    started = time.time()
    work = common.work_dir("codex", plain=not a.no_plain_paths)
    copy, project = common.layout(work, root, a.project)
    try:
        local = common.fork_prepare(a, root, copy, commit, project, live_project, pcommit, cutoff)
        env, _ = replay.seal(copy, work, "", root)
        home = Path(env["CODEX_HOME"])
        cwd = common.fork_workdir(session_cwd(lines, line), [(live_project, project), (root, copy)],
                                  work, a.cwd, plain=not a.no_plain_paths)
        write_fork(lines, line, sid, cutoff, home, cwd)
        pages = copy_ancestors(parse(lines[0]), home / "sessions")  # whole pages first: offsets point into them
        claude_projects = work / "claude-config" / "projects"
        sessions = common.run_sessions(a, check, cutoff, env, claude_projects, home)
        if claude_projects.is_dir():
            env["CLAUDE_CONFIG_DIR"] = str(claude_projects.parent)
        cmd = fork_command(out, sid, a)
        changes = common.Changes(work, copy, cwd, a.project_dir, local=root if local is not None else None)
        code = common.run_watched(cmd, prompt, env, out, a.idle_limit, cwd=cwd)
        changes.write(out)
        oriented = replay_check.orientation(turn.calls, out / "events.jsonl")
        (out / "orientation.json").write_text(json.dumps(oriented, indent=1, ensure_ascii=False))
        trees = [(root, commit)] + ([(live_project, pcommit)] if project else [])
        reads, bad = common.record_reads(out, out / "events.jsonl", copy, trees, cutoff)
        (out / "replayed.json").write_text(json.dumps(
            {"session": str(session), "line": line, "record": rid, "commit": commit,
             "cutoff": target["timestamp"], "append": a.append, "model": a.model, "effort": a.effort,
             "history_pages": pages}, indent=1))
        common.write_manifest(
            out, out / "events.jsonl", started, host="codex", subcommand="codex-fork",
            **replay.configured(home, a), cli_version=common.cli_version("codex", env),
            flags=list(sys.argv[1:] if argv is None else argv), command=cmd,
            root=str(root), commit=commit, session=str(session), line=line, record=rid,
            project={"path": str(live_project), "commit": pcommit} if project else None,
            fidelity=common.fidelity(a, local, sessions, check), exit=code)
        print(f"{out}: codex exit {code}" + (f"; {len(bad)} contaminating reads" if bad else "")
              + f"; first {oriented['first_steps']} steps orienting: replay {oriented['replay_orienting']},"
              f" original {oriented['original_orienting']}")
        return code
    finally:
        if a.keep:
            print(f"work directory kept at {work}")
        else:
            common.remove_tree(work)


if __name__ == "__main__":
    sys.exit(main())
