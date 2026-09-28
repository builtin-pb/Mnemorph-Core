#!/usr/bin/env python3
"""Gather the evidence for judging whether a real turn can be replayed faithfully.

A sealed copy lacks some of what the original agent had. This writes a digest of
one turn: the replayed request, what the original agent did in that turn (its
tool calls until the next user message, with the paths, CLIs, MCP tools,
remotes and other sessions they touched), what the recent session before it
(the last three user turns) named, and which of these the sealed copy lacks or
would change, noting the seal workaround that covers each: `origin` (a local
bare clone as the copy's remote), `local-state` (`.mnemorph-local` files older
than the request carried into the copy), `sessions` (the referenced sessions
carried into the sealed stores, cut at the request) or `project` (another
repository given as --project).

It issues no verdict. Patterns cannot see semantic dependencies ("maybe ask it"
names no path or tool until the agent acts), so a fresh model judges each
digest by the brief in tools/replay_judge.md and returns replayable, degraded
or unusable with reasons; `--record-verdicts` stores those, labeled as judged by
a model.

  check --session FILE (--line N | --record ID)     one fork's digest
  check --prompt-file FILE --commit C [--host H]    a fresh run's digest
  check --cases FILE...                             digests.json and judge packets beside the first
  check --record-verdicts JSON --cases-dir DIR --judge NAME

Forks also write orientation.json: how many of the replay's first steps inspect
the environment (remotes, history, home or temporary folders, which, env)
beside the original's, as evidence, not a gate. Standard library only.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_fork_replay as claude_fork  # noqa: E402
import codex_fork_replay as codex_fork  # noqa: E402
import replay_common as common  # noqa: E402

BRIEF = Path(__file__).resolve().parent / "replay_judge.md"
VERDICTS = ("replayable", "degraded", "unusable")
STEPS = 30        # original steps shown in a digest
STEP_CHARS = 200
REQUEST_CHARS = 4000

UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
PATH = re.compile(r"(?:~|/Users/[^/\s\"'`]+|/Volumes/[^/\s\"'`]+|/private/tmp|/tmp|/private/var/folders|/var/folders)"
                  r"(?:/[^\s\"'`()|;,<>*?\\\[\]{}^$]+)+")
MCP_CALL = re.compile(r"\bmcp__[A-Za-z0-9_-]+")
LIVE_AGENT_TOOLS = ("SendMessage", "followup_task", "send_input", "send_message")
LOCAL = re.compile(r"\.mnemorph-local/[^\s\"'`()|;,<>*?\\]+")
STARTS = r"(?:^|[;&|(`\n\"']|\$\(|\bthen\b|\bdo\b)\s*(?:which\s+|command\s+-v\s+|exec\s+)?"
CLI = re.compile(STARTS + r"(codex|claude)(?=\s|$|[;&|)\"'`])", re.M)
GH = re.compile(STARTS + r"gh\s+[a-z]", re.M)
GIT_REMOTE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)*(push|pull|fetch|ls-remote|remote)\b")
WEB_CMD = re.compile(STARTS + r"(curl|wget)\b|https?://", re.M)
ASK = re.compile(r"\b(?:ask|tell|check with|message|ping|query|resume)\b[^.\n]{0,40}?\b(?:session|thread)s?\b", re.I)
WEB_TOOLS = ("WebFetch", "WebSearch", "web_search", "web.run", "web_fetch")
EPHEMERAL = ("/private/tmp", "/tmp", "/private/var/folders", "/var/folders", "/dev")
# Host folders a replay has anyway: the seal's own links and skills, the host's
# runtimes, caches and plugin files (still on disk), and its per-session output.
PROVIDED = {"codex": (".codex/mnemorph", ".codex/skills", ".codex/plugins",
                      ".codex/AGENTS.md", ".codex/config.toml", ".cache/codex-runtimes", ".codex/.tmp"),
            "claude": (".claude/mnemorph", ".claude/skills", ".claude/CLAUDE.md")}
RECENT_TURNS = 3


def texts(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in texts(v)]
    if isinstance(value, list):
        return [s for v in value for s in texts(v)]
    return []


def session_index(home: Path) -> dict[str, Path]:
    """Session files by id: Claude transcripts by name, Codex rollouts by the ids in theirs."""
    index: dict[str, Path] = {}
    for p in sorted((home / ".claude" / "projects").glob("*/*.jsonl")):
        index.setdefault(p.stem, p)
    for store in ("sessions", "archived_sessions"):
        for p in sorted((home / ".codex" / store).rglob("rollout-*.jsonl")):
            for m in UUID.finditer(p.name):
                index.setdefault(m.group(0), p)
    return index


# -- the turn: request, recent history and the original agent's calls ----------

class Turn:
    """What the check reads: the request, the recent history (its tool calls and
    message text) and the original agent's calls in the replayed turn."""

    def __init__(self, request: str, own: set[str]):
        self.request, self.own = request, own
        self.history_calls: list[str] = []
        self.history_text: list[str] = []
        self.calls: list[tuple[str, str]] = []


def claude_calls(entry: dict) -> list[tuple[str, str]]:
    message = entry.get("message") if entry.get("type") == "assistant" else None
    return [(str(b.get("name")), "\n".join(texts(b.get("input")))) for b in (message or {}).get("content") or []
            if isinstance(b, dict) and b.get("type") == "tool_use"]


def claude_text(entry: dict) -> str:
    words = claude_fork.user_words(entry)
    if words is not None:
        return words
    message = entry.get("message") if entry.get("type") == "assistant" else None
    return "\n".join(b.get("text", "") for b in (message or {}).get("content") or []
                     if isinstance(b, dict) and b.get("type") == "text")


def claude_turn(lines: list[str], line: int) -> Turn:
    parse = claude_fork.parse
    turn = Turn(claude_fork.user_words(parse(lines[line - 1])) or "",
                {claude_fork.session_id(lines, line, Path(""))})
    starts = [n for n in range(1, line) if claude_fork.user_words(parse(lines[n - 1])) is not None]
    first = starts[-RECENT_TURNS] if len(starts) >= RECENT_TURNS else 1
    for raw in lines[first - 1:line - 1]:
        e = parse(raw)
        turn.history_calls += [text for _, text in claude_calls(e)]
        turn.history_text.append(claude_text(e))
    for raw in lines[line:]:
        e = parse(raw)
        if claude_fork.user_words(e) is not None:
            break
        turn.calls += claude_calls(e)
    return turn


def codex_calls(entry: dict) -> list[tuple[str, str]]:
    p = codex_fork.payload(entry)
    if entry.get("type") != "response_item" or p.get("type") not in (
            "function_call", "custom_tool_call", "local_shell_call", "web_search_call"):
        return []
    name = str(p.get("name") or p.get("type"))
    if p.get("namespace"):
        name = f"{p['namespace']}__{name}"
    return [(name, "\n".join(texts({k: v for k, v in p.items() if k in ("arguments", "input", "action")})))]


def codex_text(entry: dict) -> str:
    p = codex_fork.payload(entry)
    if entry.get("type") == "response_item" and p.get("type") == "message":
        return "\n".join(c.get("text", "") for c in p.get("content", []) if isinstance(c, dict))
    return ""


def codex_turn(lines: list[str], line: int) -> Turn:
    parse = codex_fork.parse
    meta = parse(lines[0])
    own = {codex_fork.payload(meta).get("id"), codex_fork.payload(meta).get("session_id"),
           *codex_fork.history_refs(meta)}
    turn = Turn(codex_fork.user_text(parse(lines[line - 1])) or "", {i for i in own if i})
    starts = [n for n in range(1, line) if (codex_fork.event_item(parse(lines[n - 1])) or {}).get("type") == "UserMessage"]
    first = starts[-RECENT_TURNS] if len(starts) >= RECENT_TURNS else 1
    for raw in lines[first - 1:line - 1]:
        e = parse(raw)
        turn.history_calls += [text for _, text in codex_calls(e)]
        turn.history_text.append(codex_text(e))
    seen_own = False
    for raw in lines[line:]:
        e = parse(raw)
        if (codex_fork.event_item(e) or {}).get("type") == "UserMessage":
            if seen_own:
                break
            seen_own = True
            continue
        turn.calls += codex_calls(e)
    return turn


# -- dependencies ---------------------------------------------------------------

class Found:
    def __init__(self):
        self.deps: dict[tuple[str, str], dict] = {}

    def add(self, kind: str, detail: str, where: str, covered: str | None, note: str = "") -> None:
        d = self.deps.setdefault((kind, detail), {"kind": kind, "detail": detail, "where": [],
                                                  "covered_by": covered, "note": note})
        if where not in d["where"]:
            d["where"].append(where)


def local_state(rel: str, root: Path, cutoff: float) -> tuple[str, str, str | None, str]:
    """A `.mnemorph-local` path (relative to that folder): covered when it predates the request."""
    detail = ".mnemorph-local/" + "/".join(Path(rel).parts[:2])
    live = root / ".mnemorph-local" / rel
    try:
        fresh = live.stat().st_mtime >= cutoff
    except OSError:
        return "local-state", detail, None, "missing now"
    if fresh and live.is_file():
        return "local-state", detail, None, "changed or created after the request"
    return "local-state", detail, "local-state", ""


def classify_path(raw: str, root: Path, home: Path, cutoff: float, index: dict[str, Path],
                  own: set[str], host: str) -> tuple[str, str, str | None, str] | None:
    """(kind, detail, workaround, note) for an absolute path the turn names, or None when it is fine."""
    path = raw.rstrip(".:")
    p = Path(str(home) + path[1:]) if path.startswith("~") else Path(path)
    s = str(p)
    if "/.cache/mnemorph-replay" in s or "/mnemorph-replay/" in s:
        return None
    for store in (home / ".claude" / "projects", home / ".codex" / "sessions", home / ".codex" / "archived_sessions"):
        if s.startswith(str(store) + "/") or s == str(store):
            named = UUID.findall(s)
            ids = [i for i in named if i not in own]
            if named and not ids:
                return None  # the replayed session itself
            if p.is_dir():
                return "session-store", str(store), "sessions", "holds only the sessions the turn names"
            target = p if p.is_file() else next((index[i] for i in ids if i in index), None)
            target = target or next(iter(sorted(p.parent.glob(p.name + "*.jsonl"))), None)
            if target:
                return "session", str(target), "sessions", ""
            return "session", s, None, "not found"
    if s.startswith(EPHEMERAL) and not s.startswith((str(home) + "/", str(root) + "/")):
        top = Path(*p.parts[:6]) if len(p.parts) > 6 else p
        return "temp-path", str(top), None, "the original's temporary files: not carried"
    if s.startswith(str(root) + "/.mnemorph-local/"):
        return local_state(s[len(str(root)) + len("/.mnemorph-local/"):], root, cutoff)
    if s == str(root) or s.startswith(str(root) + "/"):
        return None
    if any(s == str(home / x) or s.startswith(str(home / x) + "/") for x in PROVIDED[host]):
        return None
    if s in (str(home / ".codex"), str(home / ".claude")):
        return None
    if s.startswith((str(home / ".codex") + "/", str(home / ".claude") + "/")):
        return "host-config", str(Path(*Path(s).parts[:5])), None, "the other host's configuration or state"
    top = Path(*p.parts[:5]) if len(p.parts) > 5 else p  # e.g. /Users/me/Desktop/project
    if (top / ".git").exists():
        return "outside-path", str(top), "project", f"covered with --project {top}"
    return "outside-path", str(top), None, ""


def scan(text: str, where: str, found: Found, ctx: dict, commands: bool) -> set[str]:
    """Record what `text` depends on; returns the uncovered kinds it named. Command
    patterns apply only to tool calls."""
    kinds = set()

    def add(kind, detail, covered, note=""):
        if kind == "local-state" and note == "missing now" and where == "turn" \
                and (kind, detail) not in found.deps:
            return  # first named by the turn itself: most likely its own output
        found.add(kind, detail, where, covered, note)
        if not covered:
            kinds.add(kind)

    root, cutoff, index, own = ctx["root"], ctx["cutoff"], ctx["index"], ctx["own"]
    for m in PATH.finditer(text):
        hit = classify_path(m.group(0), root, ctx["home"], cutoff, index, own, ctx["host"])
        if hit:
            add(*hit)
    for m in LOCAL.finditer(text):
        if m.start() == 0 or text[m.start() - 1] != "/":  # absolute ones were classified above
            add(*local_state(m.group(0)[len(".mnemorph-local/"):], root, cutoff))
    for m in UUID.finditer(text):
        if m.group(0) in index and m.group(0) not in own:
            add("session", str(index[m.group(0)]), "sessions")
    if ASK.search(text):
        add("live-session", "asks another agent session", None, "sessions can be read, not asked")
    if commands:
        for m in GIT_REMOTE.finditer(text):
            add("git-remote", f"git {m.group(1)}", "origin")
        if GH.search(text):
            add("gh", "gh (GitHub CLI)", None, "no GitHub access")
        for m in CLI.finditer(text):
            add("cli", m.group(1), None, "needs the host's login and network")
        if WEB_CMD.search(text):
            add("web", "network or URL", None, "replays have no web access")
    return kinds


def digest(turn: Turn, root: Path, cutoff: float, host: str, home: Path | None = None) -> dict:
    """The evidence for one turn; no verdict."""
    home = home or Path.home()
    ctx = {"root": root, "cutoff": cutoff, "home": home, "index": session_index(home), "own": turn.own,
           "host": host}
    found = Found()
    scan(turn.request, "request", found, ctx, commands=False)
    for text in turn.history_calls:
        scan(text, "history", found, ctx, commands=True)
    for text in turn.history_text:
        scan(text, "history", found, ctx, commands=False)
    steps = []
    for name, text in turn.calls:
        kinds = scan(text, "turn", found, ctx, commands=True)
        if name.startswith("mcp__") or "mcp" in name.split("__")[0]:
            found.add("mcp", name, "turn", None, "no MCP servers or connectors")
            kinds.add("mcp")
        if name in WEB_TOOLS:
            found.add("web", name, "turn", None, "replays have no web access")
            kinds.add("web")
        for m in set(MCP_CALL.findall(text)):  # e.g. called from inside Codex's exec code
            found.add("mcp", m, "turn", None, "no MCP servers or connectors")
            kinds.add("mcp")
        if name.split("__")[-1] in LIVE_AGENT_TOOLS:
            found.add("live-session", f"{name} to a running agent", "turn", None, "a replay's earlier agents are not running")
            kinds.add("live-session")
        steps.append({"tool": name, "input": " ".join(text.split())[:STEP_CHARS],
                      "missing": sorted(kinds)})
    deps = sorted(found.deps.values(), key=lambda d: (d["covered_by"] is not None, d["kind"], d["detail"]))
    return {"request": turn.request[:REQUEST_CHARS] + (" [...]" if len(turn.request) > REQUEST_CHARS else ""),
            "original_turn": {"calls": len(turn.calls),
                              "tools": dict(collections.Counter(n for n, _ in turn.calls).most_common()),
                              "calls_touching_missing": sum(bool(s["missing"]) for s in steps),
                              "steps": steps[:STEPS]},
            "missing": [d for d in deps if not d["covered_by"]],
            "covered": [d for d in deps if d["covered_by"]],
            "sessions": sorted({d["detail"] for d in deps if d["kind"] == "session" and d["covered_by"]})}


def load_turn(session: Path, line: int, host: str | None = None) -> tuple[str, int, Turn, str]:
    lines = session.read_text(encoding="utf-8", errors="ignore").split("\n")
    host = host or ("codex" if "/.codex/" in str(session) else "claude")
    if host == "codex":
        line = codex_fork.normalize(lines, line)
        return host, line, codex_turn(lines, line), codex_fork.parse(lines[line - 1]).get("timestamp")
    return host, line, claude_turn(lines, line), claude_fork.parse(lines[line - 1]).get("timestamp")


def digest_fork(session: Path, line: int, root: Path, host: str | None = None,
                home: Path | None = None) -> tuple[dict, Turn]:
    host, line, turn, stamp = load_turn(session, line, host)
    cutoff = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    return {"host": host, "session": str(session), "line": line, "time": stamp,
            **digest(turn, root, cutoff, host, home)}, turn


def digest_prompt(prompt: str, root: Path, cutoff: float, host: str) -> dict:
    return {"host": host, **digest(Turn(prompt, set()), root, cutoff, host)}


def summary(d: dict) -> str:
    turn = d["original_turn"]
    out = [f"digest: {d.get('session', 'prompt')}" + (f" line {d['line']}" if d.get("line") else "")
           + f"; original turn: {turn['calls']} calls, {turn['calls_touching_missing']} touching something missing"]
    for label, items in (("missing", d["missing"]), ("covered", d["covered"])):
        for x in items:
            cover = f"by {x['covered_by']}" if x["covered_by"] else ""
            out.append(f"  {label:8} {x['kind']:13} {cover:15} {','.join(x['where']):20} {x['detail'][-80:]}"
                       + (f" ({x['note']})" if x["note"] else ""))
    return "\n".join(out)


# -- orientation: the replay's first steps beside the original's --------------

ORIENTING = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)*(?:remote|log|status|branch|rev-parse|config|worktree|reflog)\b"
    r"|(?:^|[;&|(\n\"'])\s*(?:pwd|whoami|hostname|uname|printenv|env|which|type|command\s+-v)\b"
    r"|\becho\s+\"?\$\{?(?:HOME|PWD|TMPDIR|CODEX_HOME|CLAUDE_CONFIG_DIR)"
    r"|\bls\b[^;&|\n]*?(?:\s~/?|\$HOME|\s/Users/[^/\s]+/?(?:\s|$)|/tmp\b|/private/|/var/folders|\s\.\.(?:\s|$))", re.M)


def replay_calls(events: Path) -> list[tuple[str, str]]:
    """The replay's tool calls in order, from a Codex or Claude Code event log."""
    calls, seen = [], set()
    for line in events.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = e.get("item") if isinstance(e, dict) else None
        if isinstance(item, dict) and item.get("type") not in (None, "agent_message", "reasoning") \
                and item.get("id") not in seen:
            seen.add(item.get("id"))
            calls.append((str(item.get("type")), item.get("command") or json.dumps(item)[:400]))
        message = e.get("message") if isinstance(e, dict) else None
        for block in (message or {}).get("content") or [] if isinstance(message, dict) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append((str(block.get("name")), "\n".join(texts(block.get("input")))))
    return calls


def orientation(original: list[tuple[str, str]], events: Path, first: int = 8) -> dict:
    """How many of the first steps inspect the environment, replay beside original."""
    def mark(calls):
        return [{"tool": n, "input": " ".join(x.split())[:STEP_CHARS], "orienting": bool(ORIENTING.search(x))}
                for n, x in calls[:first]]
    replay, orig = mark(replay_calls(events)), mark(original)
    return {"first_steps": first, "replay_orienting": sum(s["orienting"] for s in replay),
            "original_orienting": sum(s["orienting"] for s in orig), "replay": replay, "original": orig,
            "note": "evidence, not a gate: orienting steps are ones that inspect remotes, history, "
                    "home or temporary folders, or the shell environment"}


# -- cases: digests, judge packets and model-judged verdicts --------------------

def case_id(file: Path, group: str, index: int) -> str:
    return f"{file.stem}-{group}-{index}"


def check_cases(files: list[Path], root: Path) -> dict:
    cases = []
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        for group in ("positive", "negative"):
            for i, case in enumerate(data.get(group, [])):
                session = Path(os.path.expanduser(case.get("session_file") or case.get("file")))
                entry = {"id": case_id(f, group, i), "file": f.name, "group": group, "index": i}
                try:
                    d, _ = digest_fork(session, int(case["line"]), root, case.get("host"))
                    entry.update(d)
                except (SystemExit, OSError, ValueError, KeyError) as e:
                    entry.update(host=case.get("host"), session=str(session), line=case.get("line"), error=str(e))
                cases.append(entry)
    return {"made": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "root": str(root),
            "note": "evidence for a model judge (tools/replay_judge.md); no verdicts", "cases": cases}


def packets(digests: dict, folder: Path) -> list[Path]:
    """One judge packet per case: the brief's path and the case's digest."""
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for c in digests["cases"]:
        p = folder / f"{c['id']}.md"
        p.write_text(f"Judge this case by the brief in {BRIEF}. Reply with one JSON object: "
                     f'{{"id": "{c["id"]}", "verdict": "replayable|degraded|unusable", "reasons": "...", '
                     f'"would_help": "..."}}\n\n```json\n{json.dumps(c, indent=1, ensure_ascii=False)}\n```\n',
                     encoding="utf-8")
        made.append(p)
    return made


def record_verdicts(judged: list[dict], folder: Path, judge: str) -> Path:
    """Merge a judge's verdicts into verdicts.json beside the digests, labeled as model-judged."""
    ids = {c["id"] for c in json.loads((folder / "digests.json").read_text(encoding="utf-8"))["cases"]}
    target = folder / "verdicts.json"
    data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"cases": {}}
    brief_commit = subprocess.run(["git", "-C", str(BRIEF.parent), "log", "-1", "--format=%H", "--", BRIEF.name],
                                  text=True, capture_output=True).stdout.strip() or None
    data.update(judged_by="model", note="verdicts judged by a fresh model from the digests; not a mechanical check",
                brief=f"tools/{BRIEF.name}", brief_commit=brief_commit)
    for v in judged:
        if v.get("id") not in ids or v.get("verdict") not in VERDICTS or not v.get("reasons"):
            raise SystemExit(f"judged entry is not a known case with a verdict and reasons: {v}")
        data["cases"][v["id"]] = {"verdict": v["verdict"], "reasons": v["reasons"],
                                  "would_help": v.get("would_help", ""), "judge": judge,
                                  "judged": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
    counts = collections.Counter(c["verdict"] for c in data["cases"].values())
    data["counts"] = {v: counts.get(v, 0) for v in VERDICTS}
    data["unjudged"] = sorted(ids - set(data["cases"]))
    target.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    ap = argparse.ArgumentParser(prog=prog, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="repository the replay copies (default: as for the replays)")
    ap.add_argument("--session", help="transcript of the session to fork")
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--line", type=int)
    which.add_argument("--record", help="record id (codex:<message> or claude:<uuid>)")
    ap.add_argument("--prompt-file", help="a fresh run's request")
    ap.add_argument("--commit", help="with --prompt-file: the tree the run starts from (its time is the cutoff)")
    ap.add_argument("--host", choices=("codex", "claude"), default="claude", help="with --prompt-file: the host")
    ap.add_argument("--cases", nargs="+", help="case files; digests.json and judge-packets/ go beside the first")
    ap.add_argument("--record-verdicts", metavar="JSON",
                    help="a judge's verdicts (a JSON list of {id, verdict, reasons, would_help})")
    ap.add_argument("--cases-dir", help="with --record-verdicts: the folder holding digests.json")
    ap.add_argument("--judge", help="with --record-verdicts: the judging model, e.g. 'Claude Opus 5.5 subagent'")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a summary")
    a = ap.parse_args(argv)
    root = common.resolve_root(a.root, ap)
    writes = [Path(c).expanduser().resolve() for c in (a.cases or [])] + \
        ([Path(a.cases_dir).expanduser().resolve()] if a.cases_dir else [])
    if common.installed() and any(not w.is_relative_to(root) for w in writes):
        ap.error("an installed copy writes digests and verdicts only beside case files inside --root")
    if a.record_verdicts:
        if not (a.cases_dir and a.judge):
            ap.error("--record-verdicts needs --cases-dir and --judge")
        judged = json.loads(Path(a.record_verdicts).read_text(encoding="utf-8"))
        target = record_verdicts(judged, Path(a.cases_dir).expanduser().resolve(), a.judge)
        print(f"{len(judged)} model-judged verdicts -> {target}")
        return 0
    if a.cases:
        files = [Path(c).expanduser().resolve() for c in a.cases]
        result = check_cases(files, root)
        target = files[0].parent / "digests.json"
        target.write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        made = packets(result, files[0].parent / "judge-packets")
        for c in result["cases"]:
            miss = sorted({x["kind"] for x in c.get("missing", []) if x["where"] != ["history"]})
            print(f"{c['id']:22} {c.get('host', '?'):6} line {c.get('line')}: "
                  + (f"error {c['error']}" if c.get("error") else
                     f"{c['original_turn']['calls']} calls; missing in request or turn: {', '.join(miss) or '-'}"))
        print(f"{len(result['cases'])} digests -> {target}; {len(made)} judge packets -> {made[0].parent if made else '-'}")
        return 0
    if a.prompt_file:
        if not a.commit:
            ap.error("--prompt-file needs --commit")
        d = digest_prompt(Path(a.prompt_file).read_text(encoding="utf-8"), root, common.commit_time(root, a.commit), a.host)
    else:
        host = a.record.split(":", 1)[0] if a.record and ":" in a.record else None
        host = host if host in ("codex", "claude") else None
        if a.record and not host:
            ap.error("--record needs a codex: or claude: id")
        if not (a.line or a.record):
            ap.error("give --session with --line or --record, --prompt-file, --cases or --record-verdicts")
        rid = common.record_id(ap, a, host) if a.record else None
        session = common.session_file(ap, a, root, rid)
        lines = session.read_text(encoding="utf-8", errors="ignore").split("\n")
        host = host or ("codex" if "/.codex/" in str(session) else "claude")
        line = a.line or (codex_fork.locate(lines, rid) if host == "codex" else claude_fork.locate(lines, rid))
        d, _ = digest_fork(session, line, root, host)
    print(json.dumps(d, indent=1, ensure_ascii=False) if a.json else summary(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
