#!/usr/bin/env python3
"""Publish an instance's Core commits to public Mnemorph-Core, unattended.

An instance that tracks public Core (its `main` contains `upstream/main`)
makes Core changes as Core-only commits (AGENTS.md). `run` finds the commits
on the source branch that touch Core, are newer than the `mnemorph.publishFrom`
commit and are not yet published, applies their Core part in order on top of
the public branch in a private worktree under the common git directory (a
commit that also touched memory publishes only its Core files), has a fresh Codex reviewer
judge each against tools/push_review.md, records its verdict with
push_guard.py, runs Core's checks and pushes the reviewed prefix. The pre-push
guard still checks every commit.

- A blocked commit is retried folded with the commits after it, so a later
  fix clears it; a set already judged blocked is not reviewed again.
- A conflict with public Core, a failed check or a failed push stops the run;
  each new stop is reported once (a macOS notification and the log).
- `skip COMMIT` marks a commit never to publish.

`install --from COMMIT` records the starting commit and loads a launchd agent
that runs `run` every 30 minutes as the user, outside any agent's sandbox;
`uninstall` removes it; `status` shows pending commits and the last result.
Needs git, `gh` (for push credentials) and the `codex` CLI. Standard library.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import push_guard as pg  # noqa: E402

LABEL = "com.mnemorph.publish-core"
FROM_KEY = "mnemorph.publishFrom"
CREDENTIAL = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "accept_scan", "note", "particulars"],
    "properties": {
        "verdict": {"type": "string", "enum": ["clean", "blocked"]},
        "accept_scan": {"type": "boolean"},
        "note": {"type": "string"},
        "particulars": {"type": "array", "items": {"type": "string"}},
    },
}
REVIEW_TAIL = """

---

You are the reviewer. Do not run commands or read files; judge only the text above.
Instead of recording the verdict yourself, reply with JSON only:
{"verdict": "clean" or "blocked", "accept_scan": true only if you examined every
mechanical scan hit and none is a personal particular, "note": one line,
"particulars": for blocked, each as "file:line: text -> neutral rewrite"}.
"""


class Stop(Exception):
    """A condition that ends the run and deserves one notification."""


def git(*args: str, cwd: Path | None = None, check: bool = True,
        env: dict | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True,
                       env=None if env is None else {**os.environ, **env})
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r


def out(*args: str, cwd: Path | None = None) -> str:
    return git(*args, cwd=cwd).stdout.strip()


def state_dir() -> Path:
    d = pg.common_git_dir() / "mnemorph-publish"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_state() -> dict:
    p = state_dir() / "state.json"
    s = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    for key in ("published", "blocked"):
        s.setdefault(key, {})
    for key in ("skipped", "notified"):
        s.setdefault(key, [])
    return s


def save_state(s: dict) -> None:
    p = state_dir() / "state.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(p)


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with (state_dir() / "log").open("a", encoding="utf-8") as f:
        f.write(f"{stamp} {message}\n")
    print(message)


def notify(state: dict, message: str) -> None:
    """Report a stop once: repeated runs hitting the same stop stay quiet."""
    log(message)
    if message in state["notified"]:
        return
    state["notified"] = (state["notified"] + [message])[-20:]
    if sys.platform == "darwin" and not os.environ.get("MNEMORPH_PUBLISH_QUIET"):
        text = message.replace("\\", "\\\\").replace('"', '\\"')[:220]
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "Mnemorph publisher"'],
                       capture_output=True)


def core_paths(sha: str) -> list[str]:
    paths = out("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).split("\n")
    return [p for p in paths if p and pg.is_core_path(p)]


def candidates(source: str, public: str, start: str, state: dict) -> list[str]:
    """Commits after `start` on `source` that touch Core, oldest first, not yet published."""
    done = set(state["published"]) | set(state["skipped"])
    equivalent = {line[2:] for line in out("cherry", public, source).splitlines() if line.startswith("- ")}
    found = []
    for sha in out("rev-list", "--reverse", "--no-merges", source, f"^{public}", f"^{start}").split():
        if sha in done or sha in equivalent:
            continue
        if core_paths(sha):
            found.append(sha)
    return found


def codex_review(packet: str, model: str, effort: str) -> dict:
    """A fresh Codex run in an empty directory with its own CODEX_HOME: the user's
    config and login, but no Mnemorph line, skills, plugins or MCP servers."""
    import codex_replay  # noqa: PLC0415 (needed only for real reviews)
    with tempfile.TemporaryDirectory(prefix="mnemorph-review-") as tmp:
        tmp = Path(tmp)
        home, work = tmp / "home", tmp / "work"
        home.mkdir()
        work.mkdir()
        real = codex_replay.user_home()
        if (real / "config.toml").is_file():
            shutil.copyfile(real / "config.toml", home / "config.toml")
        if (real / "auth.json").exists():
            os.symlink(real / "auth.json", home / "auth.json")
        (tmp / "schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
        cmd = ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
               "-C", str(work), "-m", model, "-c", f'model_reasoning_effort="{effort}"',
               "--output-schema", str(tmp / "schema.json"), "-o", str(tmp / "verdict.json"),
               *codex_replay.isolation_options(), "-"]
        env = {**os.environ, "CODEX_HOME": str(home)}
        r = subprocess.run(cmd, input=packet + REVIEW_TAIL, text=True, capture_output=True,
                           env=env, timeout=1800)
        result = tmp / "verdict.json"
        if r.returncode or not result.is_file():
            raise Stop(f"the Codex reviewer failed (exit {r.returncode}): {r.stderr.strip()[-300:]}")
        return json.loads(result.read_text(encoding="utf-8"))


def review(commit: str, wt: Path, args: argparse.Namespace) -> dict:
    guard = [sys.executable, str(HERE / "push_guard.py")]
    printed = subprocess.run([*guard, "review", f"{commit}^..{commit}"], cwd=wt, text=True,
                             capture_output=True, check=True).stdout.split("\n")
    packet_path = next((p for p in printed if p.endswith(".md")), None)
    if packet_path is None:  # already has a clean verdict
        return {"verdict": "clean", "accept_scan": True, "note": "already clean", "particulars": []}
    packet = Path(packet_path).read_text(encoding="utf-8")
    command = os.environ.get("MNEMORPH_PUBLISH_REVIEWER")  # tests: a command reading stdin, printing JSON
    if command:
        r = subprocess.run(command, shell=True, input=packet, text=True, capture_output=True, check=True)
        verdict = json.loads(r.stdout)
    else:
        verdict = codex_review(packet, args.model, args.effort)
    if verdict.get("verdict") == "clean":
        who = "test reviewer" if command else f"codex {args.model}"
        cmd = [*guard, "verdict", commit, "clean", "--reviewer", f"publish_core ({who})",
               "--note", verdict.get("note", "")]
        if verdict.get("accept_scan"):
            cmd.append("--accept-scan")
        r = subprocess.run(cmd, cwd=wt, text=True, capture_output=True)
        if r.returncode:  # scan hits the reviewer did not accept
            verdict = {**verdict, "verdict": "blocked", "note": r.stderr.strip()[:300]}
    return verdict


def replay(shas: list[str], base: str, wt: Path) -> str | None:
    """Commit the Core part of `shas` on `base` as one commit (the original's
    message for one); None if together they change nothing. Raises Stop on a
    conflict. Dates come from the last original, so replaying the same content
    on the same base yields the same commit and reuses its recorded verdict."""
    git("checkout", "-q", "--force", "--detach", base, cwd=wt)
    for sha in shas:
        patch = git("diff", "--binary", f"{sha}^", sha, "--", *core_paths(sha)).stdout
        r = subprocess.run(["git", "apply", "--3way", "--index", "--whitespace=nowarn", "-"],
                           input=patch, cwd=wt, text=True, capture_output=True)
        if r.returncode:
            git("checkout", "-q", "--force", "--detach", base, cwd=wt, check=False)
            raise Stop(f"{sha[:8]} does not apply on public Core; merge upstream into the "
                       f"instance, resolve, and commit the Core fix")
    if not git("diff", "--cached", "--quiet", cwd=wt, check=False).returncode:
        return None
    dates = {"GIT_AUTHOR_DATE": out("log", "-1", "--format=%aI", shas[-1]),
             "GIT_COMMITTER_DATE": out("log", "-1", "--format=%cI", shas[-1])}
    if len(shas) == 1:
        git("commit", "-q", "-C", shas[0], cwd=wt, env=dates)
    else:
        subjects = "\n".join(f"- {out('log', '-1', '--format=%s', s)}" for s in shas)
        git("commit", "-q", "--author", out("log", "-1", "--format=%an <%ae>", shas[-1]),
            "-m", f"Publish {len(shas)} instance commits together\n\n{subjects}", cwd=wt, env=dates)
    return out("rev-parse", "HEAD", cwd=wt)


def checks(wt: Path) -> str | None:
    if os.environ.get("MNEMORPH_PUBLISH_CHECKS") == "0":  # tests: synthetic trees lack Core's tools
        return None
    for cmd in (["tools/modules.py", "check"], ["-m", "unittest", "discover", "-s", "tools/tests"]):
        r = subprocess.run([sys.executable, *cmd], cwd=wt, text=True, capture_output=True, timeout=1800)
        if r.returncode:
            return f"{' '.join(cmd)} failed: {(r.stdout + r.stderr).strip()[-300:]}"
    sizes = subprocess.run([sys.executable, "tools/memory.py", "sizes"], cwd=wt, text=True, capture_output=True)
    if json.loads(sizes.stdout or "{}").get("over_limit_files"):
        return "a Core file is over its size limit"
    return None


def publish(args: argparse.Namespace, state: dict) -> int:
    start = pg.config_get(FROM_KEY)
    if not start:
        raise Stop(f"no starting commit: run install --from COMMIT (sets {FROM_KEY})")
    git("fetch", "-q", args.remote, args.branch)
    public = f"{args.remote}/{args.branch}"
    todo = candidates(args.source, public, start, state)
    if not todo:
        log("nothing to publish")
        return 0
    wt = state_dir() / "worktree"
    git("worktree", "remove", "--force", str(wt), check=False)
    if wt.exists():
        shutil.rmtree(wt)
    git("worktree", "add", "-q", "--detach", str(wt), public)
    tip = out("rev-parse", public)
    head, mapping, stop = tip, {}, None
    try:
        i = 0
        while i < len(todo):
            for j in range(i, len(todo)):
                group = todo[i:j + 1]
                key = ",".join(group)
                if key in state["blocked"]:
                    continue
                new = replay(group, head, wt)
                if new is None:
                    mapping.update({s: None for s in group})
                    break
                verdict = review(new, wt, args)
                if verdict.get("verdict") == "clean":
                    head = new
                    mapping.update({s: new for s in group})
                    break
                state["blocked"][key] = (verdict.get("note", "") + " "
                                         + "; ".join(verdict.get("particulars", []))).strip()
                log(f"blocked {key}: {state['blocked'][key]}")
            else:
                reason = state["blocked"].get(todo[i], "")
                raise Stop(f"{todo[i][:8]} was blocked by the privacy reviewer: {reason}"[:400])
            i = j + 1
    except Stop as e:
        stop = str(e)
    if head != tip:
        git("checkout", "-q", "--force", "--detach", head, cwd=wt)
        failure = checks(wt)
        if failure:
            stop = f"not published: {failure}"
        elif args.dry_run:
            log(f"dry run: would push {out('rev-list', '--count', f'{tip}..{head}')} commit(s)")
        else:
            r = git(*CREDENTIAL, "push", args.remote, f"{head}:refs/heads/{args.branch}", cwd=wt, check=False)
            if r.returncode:
                stop = f"push failed: {r.stderr.strip()[-300:]}"
            else:
                state["published"].update(mapping)
                log(f"published {len({v for v in mapping.values() if v})} commit(s) to {public}")
    elif mapping and not args.dry_run:
        state["published"].update(mapping)  # they change nothing public Core lacks
    git("worktree", "remove", "--force", str(wt), check=False)
    if stop:
        raise Stop(stop)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    lock = (state_dir() / "lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another publish run is active")
        return 0
    state = load_state()
    try:
        code = publish(args, state)
    except Stop as e:
        notify(state, str(e))
        code = 1
    except Exception as e:  # noqa: BLE001 - an unattended job must report, not vanish
        notify(state, f"publisher error: {e}"[:400])
        code = 1
    state["last_run"] = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "exit": code}
    save_state(state)
    return code


def cmd_skip(args: argparse.Namespace) -> int:
    state = load_state()
    sha = out("rev-parse", args.commit)
    state["skipped"] = sorted(set(state["skipped"]) | {sha})
    save_state(state)
    print(f"will not publish {sha[:8]}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = load_state()
    start = pg.config_get(FROM_KEY)
    print(f"from: {start or '(unset)'}  last run: {state.get('last_run')}")
    if start:
        try:
            for sha in candidates(args.source, f"{args.remote}/{args.branch}", start, state):
                print(f"pending {out('log', '-1', '--format=%h %s', sha)}")
        except Stop as e:
            print(f"stopped: {e}")
    log_file = state_dir() / "log"
    if log_file.is_file():
        print("".join(log_file.read_text(encoding="utf-8").splitlines(keepends=True)[-5:]), end="")
    return 0


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def cmd_install(args: argparse.Namespace) -> int:
    root = Path(out("rev-parse", "--show-toplevel"))
    git("config", FROM_KEY, out("rev-parse", args.start))
    found = [shutil.which(t) for t in ("git", "gh", "codex")]
    path = os.pathsep.join(dict.fromkeys([str(Path(t).parent) for t in found if t]
                                         + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]))
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(root / "tools" / "publish_core.py"), "run",
                             "--remote", args.remote, "--branch", args.branch, "--source", args.source],
        "WorkingDirectory": str(root),
        "StartInterval": args.interval, "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": path, "HOME": str(Path.home())},
        "StandardOutPath": str(state_dir() / "launchd.log"),
        "StandardErrorPath": str(state_dir() / "launchd.log"),
    }
    plist_path().parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
    plist_path().write_bytes(plistlib.dumps(plist))
    r = subprocess.run(["launchctl", "bootstrap", domain, str(plist_path())], text=True, capture_output=True)
    if r.returncode:
        print(f"launchctl bootstrap failed: {r.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"installed {LABEL}: every {args.interval // 60} min, publishing commits after "
          f"{args.start}; undo with: python3 tools/publish_core.py uninstall")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    plist_path().unlink(missing_ok=True)
    print(f"removed {LABEL}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn in (("run", cmd_run), ("status", cmd_status), ("install", cmd_install),
                     ("uninstall", cmd_uninstall), ("skip", cmd_skip)):
        s = sub.add_parser(name)
        s.set_defaults(fn=fn)
        if name in ("run", "status", "install"):
            s.add_argument("--remote", default="upstream")
            s.add_argument("--branch", default="main")
            s.add_argument("--source", default="main")
    sub.choices["run"].add_argument("--dry-run", action="store_true", help="review and check, but do not push")
    sub.choices["run"].add_argument("--model", default="gpt-6-astra")
    sub.choices["run"].add_argument("--effort", default="high")
    sub.choices["install"].add_argument("--from", dest="start", required=True,
                                        help="publish only commits after this one")
    sub.choices["install"].add_argument("--interval", type=int, default=1800, help="seconds between runs")
    sub.choices["skip"].add_argument("commit")
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
