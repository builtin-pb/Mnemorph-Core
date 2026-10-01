#!/usr/bin/env python3
"""Replay a task on Codex from a throwaway copy of a repository.

Lesson tests replay a miss on the model that made it
(src/core/memory/lesson-tests.md). This runs one non-interactive `codex exec`
from a fresh clone of --root (default: this repository) at a given commit, with
an optional patch applied and committed and optional files copied in (for
example gitignored working state), then writes the event log, final reply, the
run's changes (changes.diff; with --project, the framework copy's changes in
memory.diff) and manifest.json to an output directory. With --project the run
works in a clone of another repository and the copy is only the framework its
CODEX_HOME links to; --no-mnemorph is the bare arm.

Isolation: connectors, plugins, browsers, computer use and the MCP servers the
user's config defines are off; by default the run is sealed to its copy (its own
CODEX_HOME, live paths rewritten; see seal()); outside paths that commands name are listed
in outside-reads.json, and those carrying evaluation material, created after
the request or changed since it in contaminated.json; escalation
requests are refused (approval policy "never"); the copy has no Git remote; the
Codex sandbox's default write access to /tmp and $TMPDIR is turned off, so shell
writes reach only the working copy (untested beyond a dry run). Copies live under
~/.cache/mnemorph-replay, inside the already trusted home directory. A sealed
run links the user's Codex login and copies the global config into its own
home, so trust entries stay there. With --no-seal the run uses ~/.codex itself
and each copy adds a trusted-project entry to ~/.codex/config.toml; run
`--prune-trust` once after such a batch (not while runs are live, since Codex
rewrites that file). An installed copy refuses both. A sealed run's commands
cannot read the live checkout, its sibling Mnemorph checkouts, the project or the
hosts' live sessions, skills and Mnemorph links (live_paths(): a permissions
profile in place of `-s workspace-write`, checked before each run, since
rewriting paths alone never sealed a run); its permission instructions list the
denied paths. Other reads are still open; exclude runs whose contaminated.json is
non-empty. Standard library only; needs
the `codex` CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_common as common  # noqa: E402

DISABLED = ("apps", "plugins", "computer_use", "browser_use",
            "browser_use_external", "in_app_browser")
TMP_EXCLUSIONS = ("-c", "sandbox_workspace_write.exclude_slash_tmp=true",
                  "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true")


def user_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def enabled_mcp_servers() -> list[str]:
    """Enabled MCP servers defined in the user's config.toml; replays turn them off.

    Servers supplied by plugins or the app are not in config.toml, and a
    `-c mcp_servers.NAME.enabled=false` override for them creates an invalid
    entry that stops Codex from starting, so only config.toml servers are listed.
    If `codex mcp list` fails, every config.toml server is turned off, so a
    failed listing cannot leave one running outside the sandbox.
    """
    try:
        text = (user_home() / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return []
    defined = set(re.findall(r'^\[mcp_servers\.("?)([\w-]+)\1\]\s*$', text, re.M))
    defined = {name for _, name in defined}
    r = subprocess.run(["codex", "mcp", "list", "--json"], text=True, capture_output=True)
    try:
        servers = json.loads(r.stdout)
        if isinstance(servers, dict):
            servers = servers.get("servers", [])
        return [s["name"] for s in servers if s.get("enabled") and s.get("name") in defined]
    except (json.JSONDecodeError, AttributeError, KeyError, TypeError):
        return sorted(defined)


def model_options(a) -> list[str]:
    cmd = []
    if a.model:
        cmd += ["-m", a.model]
    if a.effort:
        cmd += ["-c", f'model_reasoning_effort="{a.effort}"']
    if a.verbosity:
        cmd += ["-c", f'model_verbosity="{a.verbosity}"']
    return cmd


def live_paths(root: Path | None, project: Path | None = None) -> list[str]:
    """What a run's commands must not read: the live checkout and its sibling
    Mnemorph checkouts, the live project, and the hosts' live sessions, skills and
    Mnemorph links. Rewriting paths cannot seal a run (history, ancestor pages,
    `~` and links name them in ways no rewrite catches), so the sandbox denies them."""
    home = Path.home()
    found = []
    if root is not None:
        found += [root, *(p for p in root.parent.iterdir() if p.name.lower().startswith("mnemorph"))]
    if project is not None:
        found.append(project)
    found += [home / ".codex" / n for n in ("sessions", "archived_sessions", "skills", "mnemorph", "memories")]
    found += [home / ".claude" / n for n in ("projects", "skills", "mnemorph")]
    for host in (home / ".codex", home / ".claude"):  # framework links under any name
        found += sorted(p for p in host.iterdir() if p.is_symlink()) if host.is_dir() else []
    out = []
    for p in found:
        for q in (p, p.resolve()):
            if (q.exists() or q.is_symlink()) and str(q) not in out:
                out.append(str(q))
    return out


def deny_options(deny: list[str]) -> list[str]:
    """A permissions profile: the workspace as usual, minus reads of live state."""
    if not deny:
        return []
    table = ", ".join(json.dumps(p) + ' = "deny"' for p in deny)
    return ["-c", 'default_permissions="replay_sealed"', "-c", 'permissions.replay_sealed.extends=":workspace"',
            "-c", "permissions.replay_sealed.filesystem={" + table + "}"]


def isolation_options(deny: list[str] = ()) -> list[str]:
    cmd = ["-c", 'approval_policy="never"', *TMP_EXCLUSIONS]
    cmd += deny_options(deny)
    for feature in DISABLED:
        cmd += ["--disable", feature]
    for server in enabled_mcp_servers():
        cmd += ["-c", f"mcp_servers.{server}.enabled=false"]
    return cmd


def check_denial(deny: list[str]) -> None:
    """Refuse to run when the profile does not stop a command reading live state."""
    if not deny or shutil.which("codex") is None:
        return
    probe = subprocess.run(["codex", "sandbox", *deny_options(deny), "--", "/bin/ls", deny[0]],
                           capture_output=True, text=True, timeout=60)
    if probe.returncode == 0:
        raise SystemExit(f"read-deny failed: a sandboxed command could list {deny[0]}; not running")


def codex_command(workdir: Path, out: Path, a, deny: list[str] = ()) -> list[str]:
    """workdir is the run's working directory: the copy, or the project clone."""
    # --ephemeral stops spawned subagents from finding their parent thread ("no
    # thread with id"); a sealed run keeps its sessions in its own CODEX_HOME,
    # which is deleted afterwards, so only an unsealed run stays ephemeral.
    cmd = ["codex", "exec", "-C", str(workdir), *(["--ephemeral"] if a.no_seal else []), "--json",
           "-o", str(out / "last.md"), *([] if deny else ["-s", "workspace-write"]), *isolation_options(deny)]
    cmd += model_options(a)
    for image in a.image or []:
        cmd += ["-i", str(Path(image).resolve())]
    return cmd


def configured(home: Path, a) -> dict:
    """Model, effort and verbosity: the flag, else the run's config.toml."""
    try:
        conf = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        conf = {}
    return {"model": a.model or conf.get("model"),
            "effort": a.effort or conf.get("model_reasoning_effort"),
            "verbosity": a.verbosity or conf.get("model_verbosity")}


def prune_trust() -> int:
    """Remove trusted-project entries that replay copies added to Codex config."""
    config = Path.home() / ".codex" / "config.toml"
    text = config.read_text(encoding="utf-8")
    pattern = re.compile(r'^\[projects\."[^"]*/\.cache/mnemorph-replay/[^"]*"\]\n'
                         r'(?:(?!\[).*\n)*', re.M)
    found = pattern.findall(text)
    if found:
        backup = config.with_name("config.toml.before-replay-prune")
        backup.write_text(text, encoding="utf-8")
        config.write_text(pattern.sub("", text), encoding="utf-8")
    print(f"removed {len(found)} replay trust entries from {config}")
    return 0


def seal(copy: Path, work: Path, prompt: str, root: Path, mnemorph: bool = True,
         project: tuple[Path, Path] | None = None,
         global_agents: str | None = None, extra: list[tuple[str, str]] = ()) -> tuple[dict, str]:
    """Point the run at its copy instead of the live checkout (common.seal_copy)
    and give it its own CODEX_HOME whose Mnemorph and skill links resolve to the
    copy, with the user's config and global AGENTS.md copied and auth linked
    (never copied). Without mnemorph the home has no Mnemorph link, skills or
    AGENTS.md line naming Mnemorph (prepare() also removes the copy's
    AGENTS.md), for a bare-host arm;
    global_agents is appended to the home's AGENTS.md.
    """
    prompt = common.seal_copy(copy, root, prompt, project, extra)
    real = user_home()
    home = work / "codex-home"
    (home / "skills").mkdir(parents=True)
    for name in ("config.toml", "AGENTS.md"):
        if (real / name).is_file():
            shutil.copyfile(real / name, home / name)
    agents = home / "AGENTS.md"
    if not mnemorph and agents.is_file():
        # the bare arm must not be told to load Mnemorph, as an installed instance's line does
        kept = [line for line in agents.read_text(encoding="utf-8").splitlines(keepends=True)
                if "mnemorph" not in line.lower()]
        agents.write_text("".join(kept), encoding="utf-8")
    if (real / "auth.json").exists():
        os.symlink(real / "auth.json", home / "auth.json")
    if mnemorph:
        os.symlink(copy, home / "mnemorph")
        skills = copy / "integrations" / "codex" / "skills"
        for d in (skills.iterdir() if skills.is_dir() else []):
            os.symlink(d, home / "skills" / d.name)
    if global_agents:
        agents = home / "AGENTS.md"
        prev = agents.read_text(encoding="utf-8") if agents.exists() else ""
        agents.write_text(prev.rstrip() + "\n\n" + Path(global_agents).read_text(encoding="utf-8"),
                          encoding="utf-8")
    return common.child_env(CODEX_HOME=str(home)), prompt


def parser(prog: str | None = None) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=prog, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    common.add_replay_args(ap, "Codex")
    ap.add_argument("--verbosity", help="model verbosity; default from Codex config")
    ap.add_argument("--image", action="append", help="image attached to the request")
    ap.add_argument("--prune-trust", action="store_true",
                    help="after a batch: remove replay copies' trust entries, then exit")
    ap.add_argument("--no-seal", action="store_true",
                    help="use the user's CODEX_HOME and leave live paths as they are")
    return ap


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    ap = parser(prog)
    a = ap.parse_args(argv)
    root = common.resolve_root(a.root, ap)
    common.guard(ap, a, root)
    if a.prune_trust:
        return prune_trust()
    common.check_replay_args(ap, a)
    live_project, trees = common.pin_commits(a, root)
    if a.no_mnemorph and a.no_seal:
        ap.error("--no-mnemorph needs a sealed run")
    if a.global_agents and a.no_seal:
        ap.error("--global-agents needs a sealed run")

    import replay_check
    prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    cutoff = float(a.cutoff) if a.cutoff else common.commit_time(root, a.commit)
    check = replay_check.digest_prompt(prompt, root, cutoff, "codex")
    out = Path(a.out).resolve()
    work = common.work_dir("codex", plain=not a.no_plain_paths)
    copy, project = common.layout(work, root, a.project)
    cmd = codex_command(project or copy, out, a, [] if a.no_seal else live_paths(root, live_project))
    if a.dry_run:
        print(shlex.join(cmd), "<", a.prompt_file)
        print(replay_check.summary(check))
        print(common.fidelity_note(a, check, copy))
        common.remove_tree(work)
        return 0
    check_denial([] if a.no_seal else live_paths(root, live_project))
    out.mkdir(parents=True, exist_ok=True)
    common.save_check(out, check)
    started = time.time()
    try:
        local = common.prepare(a, root, copy, project, cutoff)
        env, home = None, user_home()
        claude_projects = work / "claude-config" / "projects"
        if not a.no_seal:
            pairs = [] if a.no_sessions else common.session_pairs(claude_projects, work / "codex-home")
            env, prompt = seal(copy, work, prompt, root, mnemorph=not a.no_mnemorph,
                               project=(live_project, project) if project else None,
                               global_agents=a.global_agents, extra=pairs)
            home = Path(env["CODEX_HOME"])
        sessions = common.run_sessions(a, check, cutoff, env, claude_projects, home)
        if claude_projects.is_dir():
            env["CLAUDE_CONFIG_DIR"] = str(claude_projects.parent)
        changes = common.Changes(work, copy, project, a.project_dir,
                                 local=root if local is not None else None)
        code = common.run_watched(cmd, prompt, env, out, a.idle_limit)
        changes.write(out)
        reads, bad = common.record_reads(out, out / "events.jsonl", copy, trees, cutoff)
        common.write_manifest(
            out, out / "events.jsonl", started, host="codex", subcommand="codex",
            **configured(home, a), cli_version=common.cli_version("codex", env),
            flags=list(sys.argv[1:] if argv is None else argv), command=cmd,
            root=str(root), commit=a.commit, sealed=not a.no_seal, mnemorph=not a.no_mnemorph,
            project={"path": str(live_project), "commit": a.project_commit} if project else None,
            fidelity=common.fidelity(a, local, sessions, check), exit=code)
        print(f"{out}: codex exit {code}" + (f"; {len(reads)} outside reads, {len(bad)} contaminating" if reads else ""))
        return code
    finally:
        if a.keep:
            print(f"copy kept at {copy}")
        else:
            common.remove_tree(work)


if __name__ == "__main__":
    sys.exit(main())
