#!/usr/bin/env python3
"""Show where sessions' effort went, for reflection on faster routes.

`rank` lists Claude Code and Codex sessions active since a time, most effort
first, counting only work since that time: active minutes (gaps over ten minutes
count as idle), tool calls, failed calls, repeated identical calls, signs of
silent waste (nudges: a short user message after twenty idle minutes; polls:
sleeps and waits held in the turn), subagents and tokens. A session that runs across several reflection windows is ranked by its
new work in each. `failures` groups failed
calls that recur across sessions, subagents included, by a normalized error,
between --since and an optional --until, so periods can be compared.
`timeline` prints one session compactly: the user's messages, the agent's replies, each tool call
with a short input (`[bg]` marks a command left running in the background,
which holds no turn), its own and its subagents' failures and idle gaps; `--full` prints messages whole
and `--since` only what happened after a time.
Neither prints tool output. Replays, `codex exec`,
SDK and temporary-directory runs are left out unless `--all` is given;
scheduled runs such as a nightly reflection are kept, since they repeat.
Codex threads archived in its state database are never shown, as in record.py,
and `rank` leaves out sessions run inside another Mnemorph instance, such as a
shared one, whose material belongs to that instance.
Standard library only.
"""
from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
IDLE_S = 600
NUDGE_GAP_S, NUDGE_CHARS = 1200, 40  # a short user message after 20 idle minutes, such as "well?"
POLL = re.compile(r"\bsleep\b|\bwait_agent\b|\bwrite_stdin\b")
TEMP_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
_SPEC = importlib.util.spec_from_file_location("record_tool", Path(__file__).with_name("record.py"))
record = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(record)

FAILED = re.compile(r'(?:Process exited with code|exit code:?|Exit code:?) ?[1-9]|Script (?:failed|error)')


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def archived_codex() -> set[str]:
    return record._codex_state(codex_home() / "state_5.sqlite")


def instance_of(cwd: str, cache: dict) -> Path | None:
    """The Mnemorph instance (a directory holding src/core/__entry__.md) that contains cwd, if any."""
    if not cwd:
        return None
    if cwd not in cache:
        found = None
        for d in [Path(cwd), *Path(cwd).parents]:
            if (d / "src" / "core" / "__entry__.md").is_file():
                found = d.resolve()
                break
        cache[cwd] = found
    return cache[cwd]


def when(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def one_line(text, width: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= width else text[: width - 1] + "…"


class Session:
    def __init__(self, host: str, sid: str, path: Path):
        self.host, self.sid, self.path = host, sid, path
        self.events: list[tuple[datetime, str, str]] = []  # (time, "user" | "say" | "call" | "fail", text)
        self.marks: list[tuple[datetime, int]] = []  # (time, tokens used then)
        self.model = ""
        self.cwd = ""
        self.skip = ""  # why rank leaves it out by default
        self.children: list["Session"] = []  # Claude Code subagent transcripts
        self.spawned: list[datetime] = []  # Codex subagent launches

    def add(self, moment, kind, text):
        if moment:
            self.events.append((moment, kind, text))

    def within(self, since: datetime | None, until: datetime | None = None) -> list[tuple[datetime, str, str]]:
        return [e for e in self.events if (since is None or e[0] >= since) and (until is None or e[0] < until)]

    def tokens(self, since: datetime | None = None) -> int:
        return sum(n for t, n in self.marks if since is None or t >= since)

    def summary(self, since: datetime | None = None) -> dict:
        """Effort since a time (all of it without one); `start` is the session's own start."""
        events = self.within(since)
        times = sorted(e[0] for e in events)
        active = sum(min((b - a).total_seconds(), IDLE_S) for a, b in zip(times, times[1:]))
        calls = collections.Counter(e[2] for e in events if e[1] == "call")
        children = [c for c in self.children if c.within(since)]
        first = min((e[0] for e in self.events), default=None)
        return {"host": self.host, "session": self.sid, "model": self.model,
                "start": first.strftime("%Y-%m-%dT%H:%MZ") if first else "",
                "users": sum(e[1] == "user" for e in events), "active_min": round(active / 60),
                "calls": sum(calls.values()), "failed": sum(e[1] == "fail" for e in events),
                "repeated": sum(n - 1 for n in calls.values() if n > 1),
                "nudges": sum(kind == "user" and len(text.strip()) <= NUDGE_CHARS and (t - prev).total_seconds() >= NUDGE_GAP_S
                              for (prev, _, _), (t, kind, text) in zip(sorted(events), sorted(events)[1:])),
                "polls": sum(kind == "call" and "[bg]" not in text and bool(POLL.search(text)) for _, kind, text in events),
                "subagents": len(children) + sum(since is None or t >= since for t in self.spawned),
                "subagent_calls": sum(sum(e[1] == "call" for e in c.within(since)) for c in children),
                "tokens_m": round((self.tokens(since) + sum(c.tokens(since) for c in children)) / 1e6, 1),
                "first": next((e[2] for e in events if e[1] == "user"), "")}


# --- Claude Code ------------------------------------------------------------

def _text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def _call(block) -> str:
    i = block.get("input") or {}
    arg = (i.get("command") or i.get("file_path") or i.get("pattern") or i.get("query")
           or i.get("description") or i.get("skill") or json.dumps(i, ensure_ascii=False))
    return f"{block.get('name')}{' [bg]' if i.get('run_in_background') else ''}: {arg}"


def claude(path: Path) -> Session:
    s = Session("claude", path.stem, path)
    usage: dict[str, tuple[datetime, int]] = {}
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = when(r.get("timestamp"))
            s.cwd = s.cwd or r.get("cwd") or ""
            if str(r.get("entrypoint", "")).startswith("sdk"):
                s.skip = "sdk"
            kind = r.get("type")
            if kind == "user":
                content = r.get("message", {}).get("content")
                text = _text(content)
                if text.strip() and not r.get("isMeta") and not text.lstrip().startswith("<"):
                    s.add(t, "user", text)
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        s.add(t, "fail", _text(b.get("content")) if not isinstance(b.get("content"), str) else b["content"])
            elif kind == "attachment":
                a = r.get("attachment") or {}
                if a.get("type") == "queued_command" and (a.get("origin") or {}).get("kind") == "human":
                    s.add(t, "user", str(a.get("prompt")))
            elif kind == "assistant":
                m = r.get("message", {})
                if m.get("model") and m["model"] != "<synthetic>":
                    s.model = m["model"]
                u = m.get("usage") or {}
                if t:
                    usage[m.get("id") or r.get("uuid")] = (t, sum(u.get(k) or 0 for k in (
                        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")))
                for b in m.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        s.add(t, "call", _call(b))
                    elif isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                        s.add(t, "say", b["text"])
    s.marks = list(usage.values())
    if (s.cwd + "/").startswith(TEMP_PREFIXES):
        s.skip = s.skip or "temporary directory"
    return s


def claude_with_subagents(path: Path) -> Session:
    s = claude(path)
    s.children = [claude(sub) for sub in sorted((path.parent / path.stem / "subagents").glob("*.jsonl"))]
    return s


# --- Codex ------------------------------------------------------------------

def _codex_failure(output) -> str | None:
    """The error text of a failed Codex call, or None. Code-mode output holds one JSON chunk per command."""
    texts = [output] if isinstance(output, str) else [c.get("text", "") for c in output or [] if isinstance(c, dict)]
    for text in texts:
        try:
            chunk = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(chunk, dict) and isinstance(chunk.get("exit_code"), int) and chunk["exit_code"]:
            return str(chunk.get("output") or f"exit code {chunk['exit_code']}")
    blob = "\n".join(str(t) for t in texts)[:4000]
    return blob if FAILED.search(blob) else None


def codex(path: Path, archived: set[str] | None = None) -> Session:
    s = Session("codex", "", path)
    archived = archived_codex() if archived is None else archived
    total = 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = when(r.get("timestamp"))
            p = r.get("payload") or {}
            kind, sub = r.get("type"), p.get("type")
            if kind == "session_meta":
                s.sid, s.cwd = p.get("id") or s.sid, p.get("cwd") or s.cwd
                source = p.get("source")
                if s.sid in archived:
                    s.skip = "archived"
                elif isinstance(source, dict):
                    s.skip = "subagent"
                elif source == "exec":
                    s.skip = "codex exec"
                elif (s.cwd + "/").startswith(TEMP_PREFIXES):
                    s.skip = "temporary directory"
            elif kind == "turn_context":
                s.model = p.get("model") or s.model
            elif kind == "event_msg" and sub == "item_completed" and (p.get("item") or {}).get("type") == "UserMessage":
                text = " ".join(c.get("text", "") for c in p["item"].get("content") or [] if isinstance(c, dict))
                if text.strip() and not text.lstrip().startswith(("<", "# AGENTS.md")):
                    s.add(t, "user", text)
            elif kind == "event_msg" and sub == "item_completed" and (p.get("item") or {}).get("type") == "AgentMessage":
                text = " ".join(c.get("text", "") for c in p["item"].get("content") or [] if isinstance(c, dict))
                if text.strip():
                    s.add(t, "say", text)
            elif kind == "event_msg" and sub == "token_count":
                now = ((p.get("info") or {}).get("total_token_usage") or {}).get("total_tokens")
                if isinstance(now, int) and t and now > total:  # a running total; keep what each report adds
                    s.marks.append((t, now - total))
                    total = now
            elif kind == "response_item" and sub in ("function_call", "custom_tool_call", "local_shell_call"):
                arg = p.get("arguments") or p.get("input") or json.dumps(p.get("action") or {})
                if isinstance(arg, str) and arg.startswith("{"):
                    try:
                        parsed = json.loads(arg)
                        arg = parsed.get("cmd") or parsed.get("command") or arg
                    except (json.JSONDecodeError, AttributeError):
                        pass
                s.add(t, "call", f"{p.get('name') or sub}: {arg}")
                if t and (p.get("name") == "spawn_agent" or "spawn_agent" in str(arg)[:200]):
                    s.spawned.append(t)
            elif kind == "response_item" and sub in ("function_call_output", "custom_tool_call_output"):
                failed = _codex_failure(p.get("output"))
                if failed is not None:
                    s.add(t, "fail", failed)
    s.sid = s.sid or path.stem
    return s


# --- Commands ---------------------------------------------------------------

def recent(root: Path, pattern: str, since: datetime):
    for path in sorted(root.glob(pattern)):
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) >= since:
            yield path


def rank(args) -> int:
    since = when(args.since)
    if not since:
        sys.exit("--since needs an ISO time, such as 2026-01-01T00:00:00Z")
    found = [claude_with_subagents(p) for p in recent(claude_home() / "projects", "*/*.jsonl", since)]
    archived = archived_codex()
    found += [codex(p, archived) for p in recent(codex_home() / "sessions", "**/rollout-*.jsonl", since)]
    instances: dict = {}
    for s in found:
        other = instance_of(s.cwd, instances)
        if other and other != ROOT:
            s.skip = "archived" if s.skip == "archived" else "another instance"
    rows = [s.summary(since) for s in found
            if s.skip not in ("archived", "another instance") and (args.all or not s.skip)]
    rows = [r for r in rows if r["calls"] >= args.min_calls]
    rows.sort(key=lambda r: (r["active_min"], r["calls"] + r["subagent_calls"]), reverse=True)
    for r in rows[: args.limit]:
        sub = f" subagents={r['subagents']}({r['subagent_calls']} calls)" if r["subagents"] else ""
        silent = "".join(f" {k}={r[k]}" for k in ("nudges", "polls") if r[k])
        print(f"{r['host']:6} {r['session'][:36]:36} {r['start']} {r['model'][:18]:18} users={r['users']} "
              f"active={r['active_min']}m calls={r['calls']} failed={r['failed']} repeated={r['repeated']}{silent}{sub} "
              f"tokens={r['tokens_m']}M | {one_line(r['first'], 90)}")
    return 0


NOISE = re.compile(r"^\s*(Exit code|Process exited with code|Chunk ID|Wall time|Output:|Original token|Script (?:error|failed):?\s*$)")
SIGNAL = re.compile(r"command not found|not found|No such file|No module named|Permission denied|not permitted|"
                    r"denied|refused|timed out|no matches found|unknown option|invalid|[A-Za-z]*Error\b|fatal:", re.I)


def signature(text: str) -> str:
    """A failed call's error, with paths and numbers blanked, so repeats group together."""
    lines = [l for l in str(text).replace("\\n", "\n").splitlines() if l.strip() and not NOISE.match(l)]
    line = next((l for l in lines if SIGNAL.search(l)), lines[0] if lines else "")
    line = re.sub(r"/[^\s:'\"]+", "<path>", line)
    line = re.sub(r"([^\w\s])\1+", r"\1\1", line)  # "=====", "===" and "==" are one error
    return re.sub(r"\d+", "N", line).strip()[:120]


def failures(args) -> int:
    since, until = when(args.since), when(args.until) if args.until else None
    if not since or args.until and not until:
        sys.exit("--since and --until need ISO times, such as 2026-01-01T00:00:00Z")
    found = [claude_with_subagents(p) for p in recent(claude_home() / "projects", "*/*.jsonl", since)]
    archived = archived_codex()
    found += [codex(p, archived) for p in recent(codex_home() / "sessions", "**/rollout-*.jsonl", since)]
    instances: dict = {}
    sessions, counts, in_subagents, example = collections.defaultdict(set), collections.Counter(), collections.Counter(), {}
    for s in found:
        other = instance_of(s.cwd, instances)
        if s.skip == "archived" or other and other != ROOT:
            continue
        for part, is_sub in [(s, False)] + [(c, True) for c in s.children]:
            for _, kind, text in part.within(since, until):
                key = signature(text) if kind == "fail" else ""
                if key:  # a failure with no error text names nothing to fix
                    sessions[key].add(s.sid)
                    counts[key] += 1
                    in_subagents[key] += is_sub
                    example.setdefault(key, s.sid)
    rows = sorted(sessions, key=lambda k: (len(sessions[k]), counts[k]), reverse=True)
    for key in [k for k in rows if len(sessions[k]) >= args.min_sessions][: args.limit]:
        print(f"{len(sessions[key]):3d} sessions {counts[key]:4d} failures ({in_subagents[key]} in subagents) "
              f"e.g. {example[key][:8]} | {key}")
    return 0


def find(sid: str) -> Session:
    for path in sorted((claude_home() / "projects").glob(f"*/{sid}*.jsonl")):
        return claude_with_subagents(path)
    for path in sorted((codex_home() / "sessions").glob(f"**/rollout-*{sid}*.jsonl")):
        return codex(path)
    path = Path(sid)
    if path.is_file():
        with path.open(encoding="utf-8", errors="replace") as handle:
            first = handle.readline()
        return codex(path) if '"session_meta"' in first else claude_with_subagents(path)
    sys.exit(f"no Claude Code or Codex session matches {sid}")


def timeline(args) -> int:
    s = find(args.session)
    if s.skip == "archived":
        sys.exit(f"{s.sid} is an archived Codex thread; archived sessions are not read")
    since = when(args.since) if args.since else None
    if args.since and not since:
        sys.exit("--since needs an ISO time, such as 2026-01-01T00:00:00Z")
    events = s.within(since) + [(t, "subfail", text) for c in s.children for t, kind, text in c.within(since)
                                 if kind == "fail"]
    events.sort(key=lambda e: e[0])
    print(f"{s.host} {s.sid} {s.model} {s.path}" + (f" (since {args.since})" if since else ""))
    previous = start = events[0][0] if events else None
    for moment, kind, text in events:
        gap = (moment - previous).total_seconds()
        previous = moment
        mark = f" (+{int(gap // 60)}m)" if gap >= 180 else ""
        label = {"user": "USER", "say": " SAY", "call": "  call", "fail": "  FAIL", "subfail": "  FAIL [subagent]"}[kind]
        if args.full and kind in ("user", "say"):
            body = str(text).strip()
        else:
            body = one_line(text, args.width * 3 if kind == "user" else args.width)
        print(f"{int((moment - start).total_seconds() // 60):4d}m{mark} {label} {body}")
    print(json.dumps({k: v for k, v in s.summary(since).items() if k != "first"}, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("rank", help="sessions active since a time, most effort first")
    r.add_argument("--since", required=True, help="ISO time, such as a reflection window's start")
    r.add_argument("--limit", type=int, default=20)
    r.add_argument("--min-calls", type=int, default=5)
    r.add_argument("--all", action="store_true", help="include replays, codex exec, SDK and temporary-directory runs")
    r.set_defaults(func=rank)
    f = sub.add_parser("failures", help="failed calls that recur across sessions since a time")
    f.add_argument("--since", required=True, help="ISO time, such as a reflection window's start")
    f.add_argument("--until", help="ISO time; count only failures before it, to compare periods")
    f.add_argument("--min-sessions", type=int, default=2)
    f.add_argument("--limit", type=int, default=30)
    f.set_defaults(func=failures)
    t = sub.add_parser("timeline", help="one session, compactly")
    t.add_argument("session", help="session id, its prefix, or a transcript path")
    t.add_argument("--width", type=int, default=120)
    t.add_argument("--full", action="store_true", help="print the user's messages and the agent's replies whole")
    t.add_argument("--since", help="ISO time; show only what happened after it, such as a reflection window's start")
    t.set_defaults(func=timeline)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
