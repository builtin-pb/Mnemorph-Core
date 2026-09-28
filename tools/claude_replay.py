#!/usr/bin/env python3
"""Replay a task on Claude Code from a throwaway copy of a repository.

The Claude Code counterpart of codex_replay.py, with the same seal, copy
options (--root, --patch, --copy, --project, --no-mnemorph, --global-agents)
and outputs (events.jsonl as stream-json, last.md, changes.diff, outside-reads.json,
contaminated.json, manifest.json): one non-interactive `claude -p` from a fresh
clone at a given commit.

Isolation: the run gets a fresh CLAUDE_CONFIG_DIR holding only a `mnemorph`
link to the copy, the copy's Claude skills and a CLAUDE.md saying "Use the
mnemorph skill on every task." (none of these with --no-mnemorph), an empty
HOME, and a working copy under the system temporary folder, so ~/.claude (its
CLAUDE.md, skills, settings, plugins, hooks and login) is never read. With
CLAUDE_CONFIG_DIR set, the `user` setting source is that directory, but Claude
Code 2.1.283 still loads $HOME/.claude/CLAUDE.md, and loads `.claude/CLAUDE.md`
from every folder above the working directory as project instructions, so a
copy under the user's home would read it too (seen on real runs;
`--setting-sources project,local` would also drop the run's own CLAUDE.md and
skills). Git keeps the user's global config (GIT_CONFIG_GLOBAL). MCP servers are off
(--strict-mcp-config with none given) and so are claude.ai connectors
(ENABLE_CLAUDEAI_MCP_SERVERS=false) and Chrome; Bash runs in Claude Code's
sandbox, auto-allowed there; file edits are accepted inside the working copy
only; anything that would ask permission is refused (--permission-prompts none);
the copy has no Git remote and the session is not saved. The launching agent's
CLAUDE*/ANTHROPIC* variables are dropped. Auth: CLAUDE_CODE_OAUTH_TOKEN if set,
else the login Keychain item `mnemorph-claude-token` (store one made by
`claude setup-token`); the token goes only into the child's environment and is
never printed or written. Standard library only; needs `claude` on PATH.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_common as common  # noqa: E402

SETTINGS = {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True}}
SOURCES = "user,project,local"  # `user` is the run's own CLAUDE_CONFIG_DIR
INSTRUCTION = "Use the mnemorph skill on every task."
KEYCHAIN_ITEM = "mnemorph-claude-token"


def live_paths(a) -> list[str]:
    """What a replay must not read: the live repository and its sibling
    instances, and the hosts' session stores, which hold what happened after
    the replayed request. Extra paths come from MNEMORPH_REPLAY_DENY."""
    home = Path.home()
    value = getattr(a, "root", None)
    root = Path(value).expanduser() if value else (common.default_root() or Path.cwd())
    paths = {root.resolve(), home / ".claude" / "projects", home / ".codex"}
    for sibling in root.resolve().parent.glob("Mnemorph*"):
        if (sibling / ".git").exists():
            paths.add(sibling.resolve())
    extra = os.environ.get("MNEMORPH_REPLAY_DENY", "")
    paths |= {Path(x).expanduser().resolve() for x in extra.split(os.pathsep) if x}
    return sorted(str(x) for x in paths)


def settings(a) -> dict:
    """The sandbox blocks shell reads of live paths at the OS level; deny rules
    stop Claude's own Read, Grep and Glob there."""
    deny = live_paths(a)
    out = json.loads(json.dumps(SETTINGS))
    out["sandbox"]["filesystem"] = {"denyRead": deny}
    out["permissions"] = {"deny": [f"Read(/{d}/**)" for d in deny]}
    return out


def claude_command(a) -> list[str]:
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--strict-mcp-config", "--no-chrome",
           "--setting-sources", SOURCES,
           "--permission-mode", "acceptEdits", "--permission-prompts", "none",
           "--settings", json.dumps(settings(a))]
    if a.model:
        cmd += ["--model", a.model]
    if a.effort:
        cmd += ["--effort", a.effort]
    return cmd


def token() -> str:
    """The OAuth token for the child: CLAUDE_CODE_OAUTH_TOKEN, else the Keychain item."""
    value = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if value:
        return value
    user = os.environ.get("USER") or getpass.getuser()
    try:
        r = subprocess.run(["security", "find-generic-password", "-a", user,
                            "-s", KEYCHAIN_ITEM, "-w"], text=True, capture_output=True)
        value = r.stdout.strip() if r.returncode == 0 else ""
    except OSError:
        value = ""
    if not value:
        raise SystemExit("claude replay: no token. Set CLAUDE_CODE_OAUTH_TOKEN, or store a token "
                         "from `claude setup-token` in the login Keychain: security "
                         f'add-generic-password -a "$USER" -s {KEYCHAIN_ITEM} -w')
    return value


def seal(copy: Path, work: Path, prompt: str, root: Path, auth: str, mnemorph: bool = True,
         project: tuple[Path, Path] | None = None,
         global_agents: str | None = None, extra: list[tuple[str, str]] = ()) -> tuple[dict, str]:
    """Point the run at its copy (common.seal_copy) and give it a fresh
    CLAUDE_CONFIG_DIR: a `mnemorph` link to the copy, the copy's Claude skills
    and the one-line CLAUDE.md, or with mnemorph=False none of them;
    global_agents is appended to that CLAUDE.md."""
    prompt = common.seal_copy(copy, root, prompt, project, extra)
    config = work / "claude-config"
    config.mkdir()
    parts = []
    if mnemorph:
        os.symlink(copy, config / "mnemorph")
        skills = copy / "integrations" / "claude" / "skills"
        (config / "skills").mkdir()
        for d in (skills.iterdir() if skills.is_dir() else []):
            os.symlink(d, config / "skills" / d.name)
        parts.append(INSTRUCTION)
    if global_agents:
        parts.append(Path(global_agents).read_text(encoding="utf-8").strip())
    if parts:
        (config / "CLAUDE.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    # Claude Code reads the user's CLAUDE.md from $HOME/.claude even when
    # CLAUDE_CONFIG_DIR is set, so the run gets an empty HOME; Git keeps the
    # user's global config.
    home = work / "home"
    home.mkdir()
    extra = {"GIT_CONFIG_GLOBAL": str(Path.home() / ".gitconfig")} if (Path.home() / ".gitconfig").exists() else {}
    env = common.child_env(CLAUDE_CONFIG_DIR=str(config), CLAUDE_CODE_OAUTH_TOKEN=auth, HOME=str(home),
                           ENABLE_CLAUDEAI_MCP_SERVERS="false", DISABLE_AUTOUPDATER="1", **extra)
    return env, prompt


def link_sessions(work: Path) -> None:
    """Let ~/.claude/projects in the run's empty HOME reach the sealed session store
    (a link to that folder alone: the user's CLAUDE.md stays out)."""
    store, home = work / "claude-config" / "projects", work / "home"
    if store.is_dir() and home.is_dir() and not (home / ".claude").exists():
        (home / ".claude").mkdir()
        os.symlink(store, home / ".claude" / "projects")


def events_of(path: Path):
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict):
            yield e


def final_reply(events: Path) -> str:
    """The text of the last `result` event, or of the last assistant message."""
    reply = ""
    for e in events_of(events):
        if e.get("type") == "result" and isinstance(e.get("result"), str):
            reply = e["result"]
        elif e.get("type") == "assistant" and not reply:
            parts = (e.get("message") or {}).get("content", [])
            text = "".join(p.get("text", "") for p in parts
                           if isinstance(p, dict) and p.get("type") == "text")
            reply = text or reply
    return reply


def init_event(events: Path) -> dict:
    """What the session reported loading: model, MCP servers, skills, plugins."""
    for e in events_of(events):
        if e.get("type") == "system" and e.get("subtype") == "init":
            return {k: e.get(k) for k in ("model", "permissionMode", "mcp_servers", "skills",
                                          "plugins", "apiKeySource", "claude_code_version")}
    return {}


def parser(prog: str | None = None) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=prog, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common.add_replay_args(ap, "Claude Code")
    return ap


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    ap = parser(prog)
    a = ap.parse_args(argv)
    root = common.resolve_root(a.root, ap)
    common.guard(ap, a, root)
    common.check_replay_args(ap, a)
    live_project, trees = common.pin_commits(a, root)

    import replay_check
    prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    cutoff = float(a.cutoff) if a.cutoff else common.commit_time(root, a.commit)
    check = replay_check.digest_prompt(prompt, root, cutoff, "claude")
    cmd = claude_command(a)
    if a.dry_run:
        source = ("CLAUDE_CODE_OAUTH_TOKEN" if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
                  else f"Keychain item {KEYCHAIN_ITEM}")
        print(shlex.join(cmd), "<", a.prompt_file)
        print(f"auth: {source}; config: fresh CLAUDE_CONFIG_DIR"
              + (" with nothing in it" if a.no_mnemorph else " with mnemorph, skills and CLAUDE.md"))
        print(replay_check.summary(check))
        print(common.fidelity_note(a, check))
        return 0
    auth = token()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    common.save_check(out, check)
    started = time.time()
    work = common.work_dir("claude", outside_home=True, plain=not a.no_plain_paths)
    copy, project = common.layout(work, root, a.project)
    try:
        local = common.prepare(a, root, copy, project, cutoff)
        stores = (work / "claude-config" / "projects", work / "home" / ".codex")
        env, prompt = seal(copy, work, prompt, root, auth, mnemorph=not a.no_mnemorph,
                           project=(live_project, project) if project else None,
                           global_agents=a.global_agents,
                           extra=[] if a.no_sessions else common.session_pairs(*stores))
        sessions = common.run_sessions(a, check, cutoff, env, *stores)
        link_sessions(work)
        changes = common.Changes(work, copy, project, a.project_dir,
                                 local=root if local is not None else None)
        code = common.run_watched(cmd, prompt, env, out, a.idle_limit, cwd=project or copy)
        (out / "last.md").write_text(final_reply(out / "events.jsonl"), encoding="utf-8")
        changes.write(out)
        reads, bad = common.record_reads(out, out / "events.jsonl", copy, trees, cutoff)
        init = init_event(out / "events.jsonl")
        version_env = {k: v for k, v in env.items() if k != "CLAUDE_CODE_OAUTH_TOKEN"}
        common.write_manifest(
            out, out / "events.jsonl", started, host="claude", subcommand="claude",
            model=a.model or init.get("model"), effort=a.effort,
            cli_version=common.cli_version("claude", version_env),
            flags=list(sys.argv[1:] if argv is None else argv), command=cmd,
            root=str(root), commit=a.commit, mnemorph=not a.no_mnemorph,
            project={"path": str(live_project), "commit": a.project_commit} if project else None,
            fidelity=common.fidelity(a, local, sessions, check), init=init or None, exit=code)
        print(f"{out}: claude exit {code}" + (f"; {len(reads)} outside reads, {len(bad)} contaminating" if reads else "")
              + common.cost_note(out))
        return code
    finally:
        if a.keep:
            print(f"copy kept at {copy}")
        else:
            common.remove_tree(work)


if __name__ == "__main__":
    sys.exit(main())
