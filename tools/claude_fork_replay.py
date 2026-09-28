#!/usr/bin/env python3
"""Replay one turn of a real Claude Code session at its real distance.

The Claude Code counterpart of codex_fork_replay.py, sealed like
claude_replay.py. It copies the session's transcript up to (not including) a
given line into a fresh CLAUDE_CONFIG_DIR, under the project key Claude Code
derives from the fork's working directory, and runs
`claude -p --resume <id> --fork-session` with that line's user message
(optionally with a restated instruction appended), with a chosen model and
effort. Claude Code keys a project by the physical working directory with every
character outside [A-Za-z0-9] replaced by "-", cut at 200 characters plus a
hash; `--resume` finds a session only under that key (checked against Claude
Code 2.1.283, which then restores the whole parent chain under a new id).

The model and effort default to the ones the session last recorded, with
Claude Code's 1M window (`[1m]`) when the session's context passed 200k tokens:
without it, resume compacts the restored history before sending the message.

The message is given by --line (a `user` record, or a queued command the user
typed while the agent worked, delivered as an `attachment`) or by --record, an
id from src/record (`claude:<uuid>`), which also supplies the session file.
Injected context (system reminders, IDE tags) is dropped from it, a slash
command is sent as `/name args`, and images are not carried over.

The fork works in a copy of --root at the last commit before the message (or
--commit, with --patch applied) when the session ran in --root, in a clone of
--project when it ran there, and otherwise in an empty directory; --cwd
overrides (refused by an installed copy). In the copied transcript, absolute
paths to --root, --project, the session's own directory and its persisted tool
results (copied along) point at their stand-ins. The config directory, auth,
isolation and outputs are claude_replay.py's, plus replayed.json. Standard
library only; needs `claude` on PATH.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_replay as claude  # noqa: E402
import replay_common as common  # noqa: E402

KEY_LIMIT = 200
WINDOW_200K = 200_000  # beyond this a session ran with a 1M window; without one, resume compacts first
INJECTED = re.compile(r"<(system-reminder|ide_opened_file|ide_selection|ide_diagnostics|local-command-caveat)"
                      r"\b[^>]*>.*?</\1>", re.S)
COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)


def java_hash(text: str) -> int:
    """JavaScript's `(h << 5) - h + charCode | 0` over UTF-16 code units."""
    h = 0
    data = text.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h = (h * 31 + int.from_bytes(data[i:i + 2], "little")) & 0xFFFFFFFF
    return h - (1 << 32) if h >= 1 << 31 else h


def base36(n: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if not n:
            return out


def project_key(cwd: Path) -> str:
    """The directory under projects/ where Claude Code keeps sessions started in cwd."""
    path = os.path.realpath(cwd)
    key = re.sub(r"[^a-zA-Z0-9]", "-", path)
    return key if len(key) <= KEY_LIMIT else f"{key[:KEY_LIMIT]}-{base36(abs(java_hash(path)))}"


def parse(raw: str) -> dict:
    try:
        e = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return e if isinstance(e, dict) else {}


def user_words(entry: dict) -> str | None:
    """The user's message on a transcript line: a `user` record or a queued command; else None."""
    if entry.get("isSidechain"):
        return None
    if entry.get("type") == "user" and not entry.get("isMeta") and not entry.get("isCompactSummary"):
        content = (entry.get("message") or {}).get("content")
    elif entry.get("type") == "attachment" and (entry.get("attachment") or {}).get("type") == "queued_command":
        content = entry["attachment"].get("prompt")
    else:
        return None
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list):
        if content and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        texts = [b.get("text") or "" for b in content if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return None
    text = "\n\n".join(t for t in (INJECTED.sub("", t).strip("\n") for t in texts) if t.strip())
    command = COMMAND.search(text)
    if command and text.lstrip().startswith("<command-"):
        args = COMMAND_ARGS.search(text)
        name = command.group(1).strip()
        text = (("/" + name.lstrip("/")) + " " + (args.group(1).strip() if args else "")).strip()
    return text or None


def locate(lines: list[str], rid: str) -> int:
    uuid = rid.split(":", 1)[1]
    for n, raw in enumerate(lines, 1):
        if uuid in raw and parse(raw).get("uuid") == uuid:
            return n
    raise SystemExit(f"record {rid} is not in the session file")


def session_settings(lines: list[str], line: int) -> tuple[str | None, str | None, int]:
    """The model and effort the session last ran with, and the largest context it reached."""
    model = effort = None
    peak = 0
    for raw in lines[:line - 1]:
        if '"assistant"' not in raw:
            continue
        e = parse(raw)
        message = e.get("message") if e.get("type") == "assistant" else None
        if not isinstance(message, dict) or str(message.get("model", "<")).startswith("<"):
            continue
        model, effort = message.get("model"), e.get("effort") or effort
        usage = message.get("usage") or {}
        peak = max(peak, sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                                            "cache_creation_input_tokens")))
    return model, effort, peak


def session_cwd(lines: list[str], line: int) -> str | None:
    cwd = None
    for raw in lines[:line - 1]:
        if '"cwd"' in raw:
            value = parse(raw).get("cwd")
            cwd = value if isinstance(value, str) else cwd
    return cwd


def session_id(lines: list[str], line: int, session: Path) -> str:
    return next((parse(raw).get("sessionId") for raw in lines[:line - 1] if '"sessionId"' in raw),
                None) or session.stem


def write_fork(lines: list[str], line: int, sid: str, session: Path, config: Path, cwd: Path,
               pairs: list[tuple[str, str]]) -> Path:
    """The transcript before `line` in config's project for cwd, with live paths repointed."""
    project = config / "projects" / project_key(cwd)
    project.mkdir(parents=True, exist_ok=True)
    live_dir = session.parent / session.stem  # persisted tool results: <stem>/tool-results/x.txt
    pairs = pairs + [(str(live_dir), str(project / sid))]
    prefix = lines[:line - 1]
    kept = [common.repoint(raw, pairs) for raw in prefix if raw.strip()]
    # only the results the history names: later ones were written after the request
    for name in sorted(set(re.findall(r"tool-results/([\w.-]+)", "\n".join(prefix)))):
        src, dest = live_dir / "tool-results" / name, project / sid / "tool-results" / name
        if src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = src.read_bytes()
            try:
                dest.write_text(common.repoint(data.decode("utf-8"), pairs), encoding="utf-8")
            except UnicodeDecodeError:
                dest.write_bytes(data)
    path = project / f"{sid}.jsonl"
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return path


def fork_command(a, sid: str) -> list[str]:
    return claude.claude_command(a) + ["--resume", sid, "--fork-session"]


def parser(prog: str | None = None) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=prog, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common.add_fork_args(ap, "claude")
    return ap


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    ap = parser(prog)
    a = ap.parse_args(argv)
    root = common.resolve_root(a.root, ap)
    common.guard(ap, a, root)
    rid = common.record_id(ap, a, "claude")
    session = common.session_file(ap, a, root, rid)

    lines = session.read_text(encoding="utf-8", errors="ignore").split("\n")
    line = locate(lines, rid) if rid else a.line
    if not 1 <= line <= len(lines):
        ap.error(f"--line {line} is outside the session's {len(lines)} lines")
    target = parse(lines[line - 1])
    words = user_words(target)
    if words is None:
        raise SystemExit(f"line {line} is not a user message or a queued command")
    prompt = words + (("\n\n" + a.append.strip()) if a.append else "")
    model, effort, peak = session_settings(lines, line)
    wide = peak > WINDOW_200K
    if not a.model and model:
        a.model = model + ("[1m]" if wide and not model.endswith("]") else "")
    elif a.model and wide and "[1m]" not in a.model:
        print(f"claude-fork: the session reached {peak} tokens; without a 1M window "
              f"(--model '{a.model}[1m]') Claude Code compacts it before the message", file=sys.stderr)
    a.effort = a.effort or effort
    cutoff = dt.datetime.fromisoformat(target["timestamp"].replace("Z", "+00:00")).timestamp()
    commit, live_project, pcommit = common.fork_commits(a, root, cutoff)
    sid = session_id(lines, line, session)
    import replay_check
    check, turn = replay_check.digest_fork(session, line, root, "claude")

    out = Path(a.out).resolve()
    if a.dry_run:
        print(shlex.join(fork_command(a, sid)), f"< line {line} of {session}")
        print(replay_check.summary(check))
        print(common.fidelity_note(a, check))
        return 0
    auth = claude.token()
    out.mkdir(parents=True, exist_ok=True)
    common.save_check(out, check)
    started = time.time()
    work = common.work_dir("claude", outside_home=True, plain=not a.no_plain_paths)
    copy, project = common.layout(work, root, a.project)
    try:
        local = common.fork_prepare(a, root, copy, commit, project, live_project, pcommit, cutoff)
        env, _ = claude.seal(copy, work, "", root, auth)
        config = Path(env["CLAUDE_CONFIG_DIR"])
        stores = (config / "projects", Path(env["HOME"]) / ".codex")
        live_cwd = session_cwd(lines, line)
        cwd = common.fork_workdir(live_cwd, [(live_project, project), (root, copy)], work, a.cwd,
                                  plain=not a.no_plain_paths)
        pairs = [(str(root), str(copy))] + ([(str(live_project), str(project))] if project else [])
        if live_cwd and not any(Path(live_cwd).is_relative_to(live) for live, _ in pairs):
            pairs.append((live_cwd, str(cwd)))  # e.g. a scratch workspace: the empty directory
        if not a.no_sessions:
            pairs += common.session_pairs(*stores)
        write_fork(lines, line, sid, session, config, cwd, pairs)
        sessions = common.run_sessions(a, check, cutoff, env, *stores)
        claude.link_sessions(work)
        cmd = fork_command(a, sid)
        changes = common.Changes(work, copy, cwd, a.project_dir, local=root if local is not None else None)
        code = common.run_watched(cmd, prompt, env, out, a.idle_limit, cwd=cwd)
        (out / "last.md").write_text(claude.final_reply(out / "events.jsonl"), encoding="utf-8")
        changes.write(out)
        oriented = replay_check.orientation(turn.calls, out / "events.jsonl")
        (out / "orientation.json").write_text(json.dumps(oriented, indent=1, ensure_ascii=False))
        trees = [(root, commit)] + ([(live_project, pcommit)] if project else [])
        reads, bad = common.record_reads(out, out / "events.jsonl", copy, trees, cutoff)
        (out / "replayed.json").write_text(json.dumps(
            {"session": str(session), "line": line, "record": rid, "commit": commit,
             "cutoff": target["timestamp"], "kind": target.get("type"), "append": a.append,
             "model": a.model, "effort": a.effort}, indent=1))
        init = claude.init_event(out / "events.jsonl")
        version_env = {k: v for k, v in env.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"}
        common.write_manifest(
            out, out / "events.jsonl", started, host="claude", subcommand="claude-fork",
            model=a.model or init.get("model"), effort=a.effort,
            cli_version=common.cli_version("claude", version_env),
            flags=list(sys.argv[1:] if argv is None else argv), command=cmd,
            root=str(root), commit=commit, session=str(session), line=line, record=rid,
            project={"path": str(live_project), "commit": pcommit} if project else None,
            fidelity=common.fidelity(a, local, sessions, check), init=init or None, exit=code)
        print(f"{out}: claude exit {code}" + (f"; {len(bad)} contaminating reads" if bad else "")
              + f"; first {oriented['first_steps']} steps orienting: replay {oriented['replay_orienting']},"
              f" original {oriented['original_orienting']}" + common.cost_note(out))
        return code
    finally:
        if a.keep:
            print(f"work directory kept at {work}")
        else:
            common.remove_tree(work)


if __name__ == "__main__":
    sys.exit(main())
