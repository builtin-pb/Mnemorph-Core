"""Shared machinery for the replay tools; tools/replay.py is the entry point.

A replay clones a repository (--root, default: the repository these tools live
in) at a commit into ~/.cache/mnemorph-replay, seals the run to that clone
(seal_copy() plus each host's own home), runs the agent CLI once under an idle
watch, and writes events.jsonl, last.md, changes.diff, outside-reads.json,
contaminated.json and manifest.json to an output directory. Standard library
only.
"""

from __future__ import annotations

import datetime as dt
import difflib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
MARKER = TOOLS / "installed.json"  # written by `replay.py install`
FILES = ("replay.py", "replay_common.py", "codex_replay.py", "codex_fork_replay.py",
         "claude_replay.py", "claude_fork_replay.py", "replay_batch.py", "replay_check.py",
         "replay_judge.md")
# Variables the launching agent sets for its own session; a replay starts clean.
PARENT_AGENT = re.compile(r"^(CLAUDE|ANTHROPIC)|^(AI_AGENT|BAGGAGE)$")
# Left out of changes.diff: dependency trees and bytecode (plus --project-dir copies).
NOISE = (":(exclude,glob)**/node_modules/**", ":(exclude,glob)**/__pycache__/**",
         ":(exclude,glob)**/*.pyc")


def run(cmd: list[str], **kw) -> None:
    r = subprocess.run(cmd, text=True, capture_output=True, **kw)
    if r.returncode != 0:
        raise SystemExit(f"{' '.join(cmd[:3])}…: {r.stderr.strip() or r.stdout.strip()}")


def git_out(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], text=True,
                          capture_output=True).stdout.strip()


# -- where the tools run ---------------------------------------------------

def tool_repo() -> Path | None:
    """The repository these files belong to, or None for an installed copy."""
    if MARKER.exists():
        return None
    r = subprocess.run(["git", "-C", str(TOOLS), "rev-parse", "--show-toplevel"],
                       text=True, capture_output=True)
    if r.returncode != 0:
        return None
    top = Path(r.stdout.strip()).resolve()
    return top if top / "tools" == TOOLS else None


def installed() -> bool:
    return tool_repo() is None


def default_root() -> Path | None:
    repo = tool_repo()
    if repo:
        return repo
    try:
        return Path(json.loads(MARKER.read_text(encoding="utf-8"))["root"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def resolve_root(value: str | None, ap) -> Path:
    root = Path(value).expanduser().resolve() if value else default_root()
    if root is None:
        ap.error("--root is required: this copy is not inside a repository")
    top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                         text=True, capture_output=True)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
        ap.error(f"--root {root} is not the top of a Git repository")
    return root.resolve()


def stays_inside(rel: str) -> bool:
    """True when a relative path stays inside the directory it is joined to."""
    p = Path(rel)
    return not p.is_absolute() and ".." not in p.parts


def guard(ap, a, root: Path | None) -> None:
    """Refuse options that escape a run, and in an installed copy the risky ones.

    An installed copy (outside a Git checkout, or carrying installed.json) runs
    under a standing allow rule, so it refuses --no-seal and --prune-trust (they
    use or rewrite ~/.codex), --cwd (a fork's writable directory), --runner (an
    arbitrary script) and any --copy source outside --root.
    """
    for spec in getattr(a, "copy", None) or []:
        if "=" not in spec:
            ap.error(f"--copy {spec}: expected SRC=DEST")
        if not stays_inside(spec.split("=", 1)[1]):
            ap.error(f"--copy {spec}: DEST must be a relative path inside the copy")
    for d in getattr(a, "project_dir", None) or []:
        if not stays_inside(d):
            ap.error(f"--project-dir {d}: must be a relative path inside --project")
    if not installed():
        return
    risky = [flag for flag, attr in (("--no-seal", "no_seal"), ("--prune-trust", "prune_trust"),
                                     ("--cwd", "cwd"), ("--runner", "runner"))
             if getattr(a, attr, None)]
    if risky:
        ap.error(f"an installed copy refuses {', '.join(risky)}; run the tool from a checkout for that")
    for spec in getattr(a, "copy", None) or []:
        src = Path(spec.split("=", 1)[0]).expanduser().resolve()
        if root is None or not src.is_relative_to(root):
            ap.error(f"an installed copy refuses --copy from outside --root: {src}")


# -- arguments shared by the codex and claude replays ----------------------

def add_replay_args(ap, host: str) -> None:
    ap.add_argument("--root", help="repository to replay (default: the one these tools live in, "
                    "or for an installed copy the one it was installed from)")
    ap.add_argument("--commit", help="tree the replay starts from")
    ap.add_argument("--prompt-file", help="the user's request, verbatim")
    ap.add_argument("--out", help="directory for events.jsonl, last.md, changes.diff and manifest.json")
    ap.add_argument("--patch", help="patch applied and committed before the run")
    ap.add_argument("--copy", action="append", metavar="SRC=DEST",
                    help="file copied into the copy at DEST (relative), e.g. gitignored state")
    ap.add_argument("--model", help=f"{host} model; default from {host}'s own configuration")
    ap.add_argument("--effort", help="reasoning effort; default from the host's configuration")
    ap.add_argument("--keep", action="store_true", help="keep the copy for inspection")
    ap.add_argument("--idle-limit", type=int, default=1200,
                    help="kill the run after this many seconds without new events (default 1200)")
    ap.add_argument("--cutoff", help="Unix time of the replayed request (default: the commit's time); "
                    "files created after it count as post-dated")
    ap.add_argument("--no-mnemorph", action="store_true",
                    help="bare host: no Mnemorph link, skills or instruction line in the sealed home, "
                    "and AGENTS.md and CLAUDE.md removed from the copy (a --copy to AGENTS.md then "
                    "supplies a different instruction file)")
    ap.add_argument("--project", help="Git repository the run works in, cloned into the run's work "
                    "directory; the copy of --root is then only the framework the sealed home links to")
    ap.add_argument("--project-commit", default="HEAD")
    ap.add_argument("--project-patch", help="patch applied and committed in the project clone")
    ap.add_argument("--project-dir", action="append", default=[],
                    help="untracked directory copied from --project into the clone, e.g. node_modules")
    ap.add_argument("--global-agents", help="file appended to the sealed home's global instructions")
    ap.add_argument("--dry-run", action="store_true", help="print the command and the fidelity check only")
    add_fidelity_args(ap)


def add_fidelity_args(ap) -> None:
    """Seal workarounds that give a replay what the original agent had; each is on by default."""
    ap.add_argument("--no-origin", action="store_true", help="leave the copy without a remote (default: a "
                    "local bare clone at the replayed commit as `origin`, so pushes stay local)")
    ap.add_argument("--no-local-state", action="store_true", help="leave out .mnemorph-local (default: the "
                    "live one's files last modified before the request, as copy-on-write clones)")
    ap.add_argument("--no-sessions", action="store_true", help="leave out the other sessions the turn refers "
                    "to (default: copied into the sealed session stores, cut at the request)")
    ap.add_argument("--no-plain-paths", action="store_true", help="name the run's folder randomly (default: "
                    "<base>/<host>/<repository>, <host>-2 while another run holds it)")


def check_replay_args(ap, a) -> None:
    if not (a.commit and a.prompt_file and a.out):
        ap.error("--commit, --prompt-file and --out are required for a replay")


def pin_commits(a, root: Path) -> tuple[Path | None, list[tuple[Path, str]]]:
    """Resolve --commit (and --project-commit) to commit IDs before the run, so a
    moving HEAD cannot change what the copy or the contamination check uses.
    Returns the live project and the (live repository, commit) pairs."""
    a.commit = resolve_commit(root, a.commit)
    trees = [(root, a.commit)]
    if not a.project:
        return None, trees
    live_project = Path(a.project).expanduser().resolve()
    a.project_commit = resolve_commit(live_project, a.project_commit)
    return live_project, trees + [(live_project, a.project_commit)]


# -- the copy ---------------------------------------------------------------

def work_dir(host: str, outside_home: bool = False, plain: bool = True) -> Path:
    """A fresh folder for one run. Claude Code reads `.claude/CLAUDE.md` from every
    folder above its working directory, the user's home included, so Claude runs
    work under the system temporary folder instead of ~/.cache.

    Named plainly by default, <base>/<host> (then <host>-2 and so on while other
    runs hold them), so the copy reads like an ordinary checkout, e.g.
    .../mnemorph-replay/claude/Mnemorph rather than claude-fork-x7a2k9/Mnemorph."""
    base = (Path(tempfile.gettempdir()).resolve() if outside_home else Path.home() / ".cache") / "mnemorph-replay"
    base.mkdir(parents=True, exist_ok=True)
    if not plain:
        return Path(tempfile.mkdtemp(prefix=f"{host}-", dir=base))
    for n in range(1, 100):
        path = base / (host if n == 1 else f"{host}-{n}")
        try:
            path.mkdir()
        except FileExistsError:
            if not abandoned(path):
                continue
            trash = Path(tempfile.mkdtemp(prefix=".abandoned-", dir=base))
            try:
                path.rename(trash / "run")
            except OSError:
                continue
            finally:
                remove_tree(trash)
            try:
                path.mkdir()
            except FileExistsError:
                continue
        (path / ".lock").write_text(str(os.getpid()))
        return path
    return Path(tempfile.mkdtemp(prefix=f"{host}-", dir=base))


def abandoned(path: Path) -> bool:
    """True when the run that held this folder is gone."""
    try:
        pid = int((path / ".lock").read_text())
    except (OSError, ValueError):
        try:
            return time.time() - path.stat().st_mtime > 600  # never locked: a crash mid-setup
        except OSError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def remove_tree(path: Path) -> None:
    """Delete a run's folder, read-only parts included (carried state keeps its modes)."""
    if not path.exists():
        return
    for d, _, _ in os.walk(path):
        try:
            os.chmod(d, os.lstat(d).st_mode | stat.S_IRWXU)
        except OSError:
            pass
    shutil.rmtree(path, ignore_errors=True)


def layout(work: Path, root: Path, project: str | None) -> tuple[Path, Path | None]:
    copy = work / root.name
    if not project:
        return copy, None
    name = Path(project).expanduser().resolve().name
    return copy, work / (name if name != copy.name else name + "-project")


def clone(src: Path, dest: Path, commit: str) -> None:
    run(["git", "clone", "-q", str(src), str(dest)])
    run(["git", "-C", str(dest), "checkout", "-q", "-B", "main", commit])
    run(["git", "-C", str(dest), "remote", "remove", "origin"])


def commit_patch(repo: Path, patch: str, message: str) -> None:
    run(["git", "-C", str(repo), "apply", str(Path(patch).resolve())])
    run(["git", "-C", str(repo), "add", "-A"])
    run(["git", "-C", str(repo), "commit", "-q", "-m", message])


def prepare(a, root: Path, copy: Path, project: Path | None, cutoff: float | None = None) -> dict | None:
    """Clone --root (and --project) with a local `origin`; apply patches, the bare
    arm, the carried local state and copies. Returns the carried local state's
    listing (for Changes), or None when it was not carried."""
    work = copy.parent
    clone(root, copy, a.commit)
    if not getattr(a, "no_origin", False):
        add_origin(copy, work, a.commit)
    if a.patch:
        commit_patch(copy, a.patch, "Replay variant")
    if a.no_mnemorph:
        run(["git", "-C", str(copy), "rm", "-q", "--ignore-unmatch", "AGENTS.md", "CLAUDE.md"])
        run(["git", "-C", str(copy), "commit", "-q", "--allow-empty", "-m", "Replay: bare host"])
    local = None
    if cutoff is not None and not getattr(a, "no_local_state", False):
        local = carry_local_state(root, copy, cutoff)
    for spec in a.copy or []:
        src, dest = spec.split("=", 1)
        target = copy / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    if project:
        src = Path(a.project).expanduser().resolve()
        clone(src, project, a.project_commit)
        if not getattr(a, "no_origin", False):
            add_origin(project, work, a.project_commit)
        if a.project_patch:
            commit_patch(project, a.project_patch, "Work in progress")
        for d in a.project_dir:
            run(["cp", "-Rc", str(src / d), str(project / d)])
    return local


# -- fidelity: what the original agent had ------------------------------------

LOCAL = ".mnemorph-local"


def add_origin(repo: Path, work: Path, commit: str) -> None:
    """A local bare clone holding history up to `commit` as the repository's
    `origin`, so `git remote -v`, `git status` and pushes look ordinary and stay local."""
    remote = work / "remote" / f"{repo.name}.git"
    run(["git", "init", "-q", "--bare", str(remote)])
    run(["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"])
    run(["git", "-C", str(repo), "push", "-q", str(remote), f"{commit}:refs/heads/main"])
    run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)])
    run(["git", "-C", str(repo), "fetch", "-q", "origin"])
    run(["git", "-C", str(repo), "branch", "-q", "--set-upstream-to=origin/main", "main"])
    run(["git", "-C", str(repo), "remote", "set-head", "origin", "main"])


def listing(folder: Path) -> dict[str, tuple[int, int]]:
    """Regular files under folder with their size and mtime."""
    found = {}
    for d, _, files in os.walk(folder):
        for name in files:
            p = Path(d) / name
            try:
                st = p.lstat()
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                found[str(p.relative_to(folder))] = (st.st_size, st.st_mtime_ns)
    return found


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except PermissionError:
        mode = p.parent.lstat().st_mode
        os.chmod(p.parent, mode | stat.S_IWUSR)
        p.unlink()
        os.chmod(p.parent, mode)


def carry_local_state(root: Path, copy: Path, cutoff: float) -> dict[str, tuple[int, int]]:
    """Carry root/.mnemorph-local into the copy with only the files last modified
    before the request, as copy-on-write clones (APFS), so the replay has the
    native state the original agent had and nothing written since. Links leading
    out of it are dropped. Returns what was carried."""
    src, dest = root / LOCAL, copy / LOCAL
    if not src.is_dir() or dest.exists():
        return {}
    if subprocess.run(["cp", "-Rcp", str(src), str(dest)], capture_output=True).returncode != 0:
        remove_tree(dest)
        newer = lambda d, names: [n for n in names if (Path(d) / n).is_file()
                                  and (Path(d) / n).stat().st_mtime >= cutoff]
        shutil.copytree(src, dest, symlinks=True, ignore=newer)
    for d, dirs, files in os.walk(dest, topdown=False):
        here = Path(d)
        for name in files + dirs:
            p = here / name
            try:
                st = p.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                if not Path(os.path.realpath(p)).is_relative_to(dest):
                    _unlink(p)
            elif not stat.S_ISDIR(st.st_mode) and (st.st_mtime >= cutoff or not stat.S_ISREG(st.st_mode)):
                _unlink(p)
        if here != dest and not any(here.iterdir()):
            try:
                born = (src / here.relative_to(dest)).stat()
                born = getattr(born, "st_birthtime", born.st_mtime)
            except OSError:
                born = cutoff
            if born >= cutoff:
                os.chmod(here.parent, here.parent.lstat().st_mode | stat.S_IWUSR)
                here.rmdir()
    return listing(dest)


def local_diff(copy: Path, live: Path, before: dict[str, tuple[int, int]]) -> str:
    """The run's changes to the carried local state, as a unified diff (its files
    stay out of the Git snapshots: they can run to gigabytes). A changed file's
    earlier text is the live one it was cloned from, when that is still unchanged."""
    after = listing(copy / LOCAL)
    chunks = []

    def read(p: Path) -> str | None:
        try:
            data = p.read_bytes()
        except OSError:
            return ""
        if len(data) > 2_000_000 or b"\0" in data[:8192]:
            return None
        return data.decode("utf-8", errors="replace")

    for rel in sorted(set(before) | set(after)):
        if before.get(rel) == after.get(rel):
            continue
        name = f"{LOCAL}/{rel}"
        old = ""
        if rel in before:
            source = live / LOCAL / rel
            try:
                same = source.stat().st_size == before[rel][0] and \
                    int(source.stat().st_mtime) == before[rel][1] // 1_000_000_000
            except OSError:
                same = False
            old = read(source) if same else None
        new = read(copy / LOCAL / rel) if rel in after else ""
        if old is None or new is None:
            chunks.append(f"Binary or large file {name} changed\n")
            continue
        chunks.append(f"diff --git a/{name} b/{name}\n" + "".join(difflib.unified_diff(
            old.splitlines(True), new.splitlines(True),
            f"a/{name}" if rel in before else "/dev/null", f"b/{name}" if rel in after else "/dev/null")))
    return "".join(chunks)


def session_pairs(claude_projects: Path, codex_home: Path) -> list[tuple[str, str]]:
    """Live session stores and their sealed stand-ins, for repointing paths."""
    home = Path.home()
    return [(str(home / ".claude" / "projects"), str(claude_projects)),
            (str(home / ".codex" / "sessions"), str(codex_home / "sessions")),
            (str(home / ".codex" / "archived_sessions"), str(codex_home / "archived_sessions"))]


def cut_at(path: Path, cutoff: float) -> str:
    """A session file up to its first record at or after the cutoff."""
    kept = []
    with path.open(encoding="utf-8", errors="ignore") as handle:
        for raw in handle:
            try:
                stamp = json.loads(raw).get("timestamp")
            except (json.JSONDecodeError, AttributeError):
                stamp = None
            if isinstance(stamp, str):
                try:
                    if dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() >= cutoff:
                        break
                except ValueError:
                    pass
            kept.append(raw)
    return "".join(kept)


def carry_sessions(paths: list[str], cutoff: float, claude_projects: Path, codex_home: Path) -> list[str]:
    """Copy each session the turn refers to, cut at the request, into the sealed
    stores (Claude transcripts under the same project folder, Codex rollouts and
    the pages their history names under the same dated folder), so the replay can
    read or resume them as the original could. Returns the sealed paths."""
    home = Path.home()
    live_claude, live_codex = home / ".claude" / "projects", home / ".codex"
    pending, seen, done = [Path(p) for p in paths], set(), []
    while pending:
        p = pending.pop()
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        if p.is_relative_to(live_claude):
            dest = claude_projects / p.relative_to(live_claude)
        elif p.is_relative_to(live_codex):
            dest = codex_home / p.relative_to(live_codex)
            try:
                meta = json.loads(p.open(encoding="utf-8", errors="ignore").readline()).get("payload") or {}
            except (json.JSONDecodeError, AttributeError):
                meta = {}
            base = meta.get("history_base") if isinstance(meta.get("history_base"), dict) else {}
            for ref in (base.get("thread_id"), meta.get("forked_from_id")):
                if isinstance(ref, str) and ref:
                    pending += sorted(live_codex.glob(f"*sessions/*/*/*/*{ref}.jsonl"))
        else:
            continue
        text = cut_at(p, cutoff)
        if not text or dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        done.append(str(dest))
    return done


def run_sessions(a, check: dict, cutoff: float, env: dict | None, claude_projects: Path,
                 codex_home: Path) -> list[str]:
    """Carry the sessions the check found referenced (unless --no-sessions or unsealed)."""
    if getattr(a, "no_sessions", False) or env is None or not check.get("sessions"):
        return []
    return carry_sessions(check["sessions"], cutoff, claude_projects, codex_home)


def save_check(out: Path, check: dict) -> None:
    """The run's fidelity digest (replay_check.py), for a judge and for reading the run."""
    (out / "check.json").write_text(json.dumps(check, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def fidelity(a, local: dict | None, sessions: list[str], check: dict) -> dict:
    """What the manifest records about the seal workarounds and the fidelity check."""
    return {"missing": sorted({x["kind"] for x in check.get("missing", [])}),
            "origin": not getattr(a, "no_origin", False),
            "local_state_files": None if local is None else len(local), "sessions": sessions,
            "plain_paths": not getattr(a, "no_plain_paths", False)}


def fidelity_note(a, check: dict, plain_path: Path | None = None) -> str:
    on = lambda flag: "off" if getattr(a, flag, False) else "on"
    parts = [f"origin {on('no_origin')}", f"local-state {on('no_local_state')}",
             f"sessions {on('no_sessions')} ({len(check.get('sessions', []))} referenced)",
             f"plain paths {on('no_plain_paths')}" + (f" ({plain_path})" if plain_path else "")]
    return "workarounds: " + "; ".join(parts)


def live_path(live: str) -> re.Pattern:
    """An absolute path as a whole path: /x/Mnemorph, not the start of /x/Mnemorph-Two."""
    return re.compile(re.escape(live) + r"(?![\w.-])")


def seal_copy(copy: Path, root: Path, prompt: str,
              project: tuple[Path, Path] | None = None, extra: list[tuple[str, str]] = ()) -> str:
    """Point the run at its copy instead of the live checkout; return the prompt.

    Rewrites absolute paths to --root inside the copy (committed) and in the
    prompt, copying any file the prompt names into the copy; the live
    --project path in the prompt becomes its clone. Each host then gives the run
    its own home whose Mnemorph and skill links resolve to the copy. The rest of
    the machine stays as it is, so the environment remains natural;
    outside_reads() classifies what a run then touches.
    """
    live = str(root)
    pat = live_path(live)
    files = subprocess.run(["git", "-C", str(copy), "grep", "-lI", "--fixed-strings", live],
                           text=True, capture_output=True).stdout.splitlines()
    changed = False
    for rel in files:
        f = copy / rel
        text = f.read_text(encoding="utf-8")
        new = pat.sub(lambda _: str(copy), text)
        if new != text:
            f.write_text(new, encoding="utf-8")
            changed = True
    if changed:
        run(["git", "-C", str(copy), "add", "-A"])
        run(["git", "-C", str(copy), "commit", "-q", "-m", "Replay: point paths at this copy"])
    for m in set(re.findall(re.escape(live) + r"/[^\s)\]>\"']+", prompt)):
        src = Path(m)
        if src.is_file():
            target = copy / src.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    prompt = pat.sub(lambda _: str(copy), prompt)
    if project:
        prompt = live_path(str(project[0])).sub(lambda _: str(project[1]), prompt)
    return repoint(prompt, list(extra)) if extra else prompt


def child_env(**extra: str) -> dict:
    """The environment without the launching agent's session variables, plus extra."""
    env = {k: v for k, v in os.environ.items() if not PARENT_AGENT.match(k)}
    env.update(extra)
    return env


# -- running and watching ---------------------------------------------------

def run_watched(cmd: list[str], prompt: str, env: dict | None, out: Path, idle: int,
                cwd: Path | None = None) -> int:
    """Run the host CLI; kill it if events.jsonl gains nothing for `idle` seconds (a silent hang)."""
    events = out / "events.jsonl"
    with open(events, "w") as ev, open(out / "stderr.txt", "w") as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=ev, stderr=err, text=True,
                                env=env, cwd=cwd, start_new_session=True)
        proc.stdin.write(prompt)
        proc.stdin.close()
        while True:
            try:
                return proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if time.time() - events.stat().st_mtime > idle:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                    err.write(f"\nreplay: killed after {idle}s without new events\n")
                    return 124


def snapshot(tree: Path, index: Path, exclude: list[str] = (), git_dir: Path | None = None) -> str:
    """A Git tree of `tree`'s working files, committed or not and ignored ones
    included (minus NOISE and `exclude`), written through a private index so the
    repository's own index and HEAD stay as the run left them. A directory
    outside Git is read through `git_dir`, a private repository in the work dir."""
    own = git_dir or tree / ".git"
    if (own / "index").exists():
        shutil.copyfile(own / "index", index)  # reuse its stat cache
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    if git_dir:
        env.update(GIT_DIR=str(git_dir), GIT_WORK_TREE=str(tree))
    specs = [".", *NOISE, *(f":(exclude){d}" for d in exclude)]
    subprocess.run(["git", "-C", str(tree), "add", "-A", "-f", "--", *specs],
                   env=env, capture_output=True)
    return subprocess.run(["git", "-C", str(tree), "write-tree"], env=env,
                          text=True, capture_output=True).stdout.strip()


def diff_trees(tree: Path, before: str, after: str, git_dir: Path | None = None) -> str:
    env = dict(os.environ, **({"GIT_DIR": str(git_dir), "GIT_WORK_TREE": str(tree)} if git_dir else {}))
    return subprocess.run(["git", "-C", str(tree), "diff", "--no-ext-diff", "--no-textconv",
                           before, after], text=True, capture_output=True, env=env).stdout


class Changes:
    """What a run changed: changes.diff for its working directory and, when it
    worked outside the framework copy (a --project clone, or a fork's empty
    directory), memory.diff for the copy. `exclude` applies to the working
    directory (--project-dir copies)."""

    def __init__(self, work: Path, copy: Path, workdir: Path | None = None, exclude: list[str] = (),
                 local: Path | None = None):
        # with the live root when .mnemorph-local was carried: listed now, diffed apart
        self.local = (local, listing(copy / LOCAL)) if local else None
        workdir = workdir or copy
        if workdir.is_relative_to(copy):
            workdir = copy
        self.work, self.copy, self.exclude = work, copy, list(exclude)
        self.names = {workdir: "changes.diff"}
        if workdir != copy:
            self.names[copy] = "memory.diff"
        self.git_dirs = {}
        for tree in self.names:
            if not (tree / ".git").exists():  # never an enclosing repository
                private = work / f"git-{tree.name}"
                run(["git", "init", "-q", str(private)])
                self.git_dirs[tree] = private / ".git"
        self.workdir = workdir
        self.before = {tree: self.snap(tree, "before") for tree in self.names}

    def snap(self, tree: Path, when: str) -> str:
        skip = (self.exclude if tree == self.workdir else []) + ([LOCAL] if self.local and tree == self.copy else [])
        return snapshot(tree, self.work / f"index-{tree.name}-{when}", skip, self.git_dirs.get(tree))

    def write(self, out: Path) -> None:
        for tree, name in self.names.items():
            diff = diff_trees(tree, self.before[tree], self.snap(tree, "after"), self.git_dirs.get(tree))
            if self.local and tree == self.copy:
                diff += local_diff(self.copy, self.local[0], self.local[1])
            diff = diff.replace(str(self.copy), "<copy>" if name == "changes.diff" else "<mnemorph>")
            (out / name).write_text(diff.replace(str(self.work), "<work>"), encoding="utf-8")


# -- forks: one turn of a real session at its real distance -----------------

def add_fork_args(ap, host: str) -> None:
    ap.add_argument("--session", help="the real session's transcript (default with --record: "
                    "the record's source file)")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--line", type=int, help="1-based line of the user message to replay; history "
                       "stops before it (a record's line is accepted too and normalized)")
    which.add_argument("--record", help=f"record id of the message ({host}:<id>, as in src/record), "
                       "resolved to its line")
    ap.add_argument("--append", default="", help="text added after the replayed message")
    ap.add_argument("--root", help="repository whose copy the run uses (default: the one these tools "
                    "live in, or for an installed copy the one it was installed from)")
    ap.add_argument("--commit", help="tree of the copy (default: the last commit before the message)")
    ap.add_argument("--patch", help="patch applied and committed in the copy, e.g. the change under test")
    ap.add_argument("--project", help="Git repository the session worked in, cloned at --project-commit "
                    "(default: its last commit before the message)")
    ap.add_argument("--project-commit")
    ap.add_argument("--project-dir", action="append", default=[],
                    help="untracked directory copied from --project into the clone, e.g. node_modules")
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--cwd", help="working directory for the fork (refused by an installed copy); default: "
                    "the copy or project clone when the session ran in --root or --project, else an empty one")
    ap.add_argument("--out", required=True)
    ap.add_argument("--idle-limit", type=int, default=1200,
                    help="kill the fork after this many seconds without new events")
    ap.add_argument("--keep", action="store_true", help="keep the work directory for inspection")
    ap.add_argument("--dry-run", action="store_true", help="print the command and the fidelity check only")
    add_fidelity_args(ap)


def record_id(ap, a, host: str) -> str | None:
    """--record as a full id: `host:` is added when missing, another host's id is refused."""
    if not a.record:
        return None
    head = a.record.split(":", 1)[0]
    rid = a.record if head in ("codex", "claude") else f"{host}:{a.record}"
    if not rid.startswith(host + ":"):
        ap.error(f"--record {a.record} is not a {host} record")
    return rid


def find_record(root: Path, rid: str) -> dict | None:
    """The entry with this id in the instance's record of the user's words."""
    needle = json.dumps(rid)
    for f in sorted((root / "src" / "record").glob("*.jsonl")):
        with f.open(encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if needle in line:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("id") == rid:
                        return entry
    return None


def session_file(ap, a, root: Path, rid: str | None) -> Path:
    if a.session:
        return Path(a.session).expanduser().resolve()
    if not rid:
        ap.error("--session is required with --line")
    entry = find_record(root, rid)
    if not entry or not entry.get("source"):
        ap.error(f"record {rid} is not in {root}/src/record; give --session")
    return Path(entry["source"]).expanduser().resolve()


def commit_before(repo: Path, cutoff: float) -> str:
    return git_out(repo, "rev-list", "-1", f"--before={int(cutoff)}", "HEAD")


def fork_commits(a, root: Path, cutoff: float) -> tuple[str, Path | None, str | None]:
    """The copy's commit, the live project and its commit, pinned before the run."""
    commit = resolve_commit(root, a.commit) if a.commit else commit_before(root, cutoff)
    if not commit:
        raise SystemExit(f"no commit in {root} before the replayed message")
    if not a.project:
        return commit, None, None
    live = Path(a.project).expanduser().resolve()
    pcommit = resolve_commit(live, a.project_commit) if a.project_commit else commit_before(live, cutoff)
    if not pcommit:
        raise SystemExit(f"no commit in {live} before the replayed message")
    return commit, live, pcommit


def fork_prepare(a, root: Path, copy: Path, commit: str, project: Path | None,
                 live_project: Path | None, pcommit: str | None, cutoff: float) -> dict | None:
    """As prepare() for a fork; returns the carried local state's listing, or None."""
    work = copy.parent
    clone(root, copy, commit)
    if not a.no_origin:
        add_origin(copy, work, commit)
    if a.patch:
        commit_patch(copy, a.patch, "Replay variant")
    local = None if a.no_local_state else carry_local_state(root, copy, cutoff)
    if project:
        clone(live_project, project, pcommit)
        if not a.no_origin:
            add_origin(project, work, pcommit)
        for d in a.project_dir:
            run(["cp", "-Rc", str(live_project / d), str(project / d)])
    return local


def fork_workdir(session_cwd: str | None, places: list[tuple[Path | None, Path | None]],
                 work: Path, override: str | None, plain: bool = True) -> Path:
    """Where the fork works: --cwd; else the clone standing for the live directory
    the session ran in (--root or --project, or below them); else an empty directory."""
    if override:
        return Path(override).expanduser().resolve()
    for live, clone_dir in places:
        if live and clone_dir and session_cwd and Path(session_cwd).is_relative_to(live):
            target = clone_dir / Path(session_cwd).relative_to(live)
            target.mkdir(parents=True, exist_ok=True)
            return target
    name = Path(session_cwd).name if plain and session_cwd else "cwd"  # the original folder's name
    empty = work / name
    if empty.exists() or name in ("remote", "home", "codex-home", "claude-config"):
        empty = work / "cwd"
    empty.mkdir(exist_ok=True)
    return empty


def repoint(text: str, pairs: list[tuple[str, str]]) -> str:
    """Replace each live path by its stand-in, as whole paths, longest first."""
    for live, local in sorted(pairs, key=lambda p: -len(p[0])):
        if live and live != local:
            text = live_path(live).sub(lambda _: local, text)
    return text


# -- contamination ----------------------------------------------------------

ANSWER = re.compile(r"/\.mnemorph-local/(experiments|lesson-tests|taste-guard|handoff)|"
                    r"/src/research/")
CONTAMINATING = ("answer", "post-dated", "changed")


def classify(path: str, trees: list[tuple[Path, str]], cutoff: float) -> str:
    """environment, answer (evaluation material), post-dated (created after the
    request), or changed (existed then, modified since). `trees` pairs each live
    repository (--root, --project) with the commit the run started from."""
    if ANSWER.search(path):
        return "answer"
    p = Path(path.replace("~", str(Path.home()), 1)).resolve() if path.startswith("~") else Path(path)
    for live_root, commit in trees:
        live = str(live_root)
        if not str(p).startswith(live + "/"):
            continue
        rel = str(p)[len(live) + 1:].rstrip(";,")
        at = subprocess.run(["git", "-C", live, "cat-file", "-e", f"{commit}:{rel}"],
                            capture_output=True).returncode == 0
        ignored = subprocess.run(["git", "-C", live, "check-ignore", "-q", rel],
                                 capture_output=True).returncode == 0
        if not at and not ignored:
            return "post-dated"
        if ignored:  # native state outside Git: judge by its own times
            return by_time(p, cutoff)
        same = subprocess.run(["git", "-C", live, "diff", "--quiet", commit, "--", rel],
                              capture_output=True).returncode == 0
        return "environment" if same else "changed"
    return by_time(p, cutoff)


def by_time(p: Path, cutoff: float) -> str:
    try:
        st = p.stat()
    except OSError:
        return "environment"
    born = getattr(st, "st_birthtime", st.st_mtime)
    if born > cutoff:
        return "post-dated"
    return "changed" if st.st_mtime > cutoff and p.is_file() else "environment"


WRITTEN = {"content", "new_string", "old_string", "new_source"}  # text a tool writes, not reads


def strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = [v for k, v in value.items() if k not in WRITTEN]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


def actions(events: Path):
    """(input, output) text of each command or tool call in a Codex or Claude Code event log."""
    for line in events.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(e, dict):
            continue
        item = e.get("item")
        if isinstance(item, dict) and item.get("type") == "command_execution":
            yield item.get("command", "") or "", item.get("aggregated_output", "") or ""
        message = e.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield "\n".join(strings(block.get("input"))), ""
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                yield "", "\n".join(strings(block.get("content")))


def outside_reads(events: Path, copy: Path, trees: list[tuple[Path, str]], cutoff: float) -> list[dict]:
    """Paths under the live checkout or the user's home that commands named, classified."""
    pat = re.compile(r"(?:~|" + re.escape(str(Path.home())) + r")/[^\s\"'`)|;]+")
    hits = {}
    for command, output in actions(events):
        for m in pat.finditer(command):
            path = m.group(0).rstrip(".,:")
            if path.startswith(str(copy)) or path.startswith(str(copy.parent)) or "/.cache/" in path:
                continue
            hits.setdefault(path, classify(path, trees, cutoff))
        # evaluation material surfacing in output, e.g. from a search over the live checkout
        for live, _ in trees:
            for m in re.finditer(re.escape(str(live)) + r"/[^\s\"'`)|;:]+", output):
                if ANSWER.search(m.group(0)):
                    hits[m.group(0)] = "answer"
    return [{"path": k, "class": v} for k, v in sorted(hits.items())]


def record_reads(out: Path, events: Path, copy: Path, trees: list[tuple[Path, str]],
                 cutoff: float) -> tuple[list, list]:
    reads = outside_reads(events, copy, trees, cutoff)
    bad = [r for r in reads if r["class"] in CONTAMINATING]
    (out / "outside-reads.json").write_text(json.dumps(reads, indent=1))
    (out / "contaminated.json").write_text(json.dumps(bad, indent=1))
    return reads, bad


def commit_time(repo: Path, commit: str) -> float:
    return float(git_out(repo, "log", "-1", "--format=%ct", commit) or 0)


def resolve_commit(repo: Path, commit: str) -> str:
    return git_out(repo, "rev-parse", "--verify", "-q", f"{commit}^{{commit}}") or commit


# -- manifest ---------------------------------------------------------------

def final_usage(events: Path) -> dict:
    """Token usage from the last usage event (Codex turn.completed, Claude Code result)."""
    found = {}
    for line in events.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(e, dict) and e.get("type") in ("turn.completed", "result") \
                and isinstance(e.get("usage"), dict):
            found = {"usage": e["usage"]}
            for src, dest in (("total_cost_usd", "cost_usd"), ("modelUsage", "model_usage"),
                              ("num_turns", "turns")):
                if src in e:
                    found[dest] = e[src]
    return found or {"usage": None}


def cost_note(out: Path) -> str:
    """The whole run's list-price cost, helpers included. The manifest's
    `usage` covers only the last message and understates it badly."""
    try:
        cost = json.loads((out / "manifest.json").read_text()).get("cost_usd")
    except (OSError, ValueError):
        return ""
    return f"; about ${cost:.0f} at list prices, helpers included" if cost else ""


def cli_version(binary: str, env: dict | None = None) -> str | None:
    try:
        r = subprocess.run([binary, "--version"], text=True, capture_output=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (r.stdout.strip() or r.stderr.strip()) or None


def write_manifest(out: Path, events: Path, started: float, **fields) -> dict:
    """manifest.json: host, model, effort, verbosity if set, CLI version, flags, commit, usage."""
    data = {k: v for k, v in fields.items() if v is not None or k in ("model", "effort")}
    data.update(final_usage(events))
    data["started"] = dt.datetime.fromtimestamp(started).astimezone().isoformat(timespec="seconds")
    data["seconds"] = int(time.time() - started)
    (out / "manifest.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return data
