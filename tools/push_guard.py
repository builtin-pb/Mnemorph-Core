#!/usr/bin/env python3
# Mnemorph push guard -- managed by tools/push_guard.py; do not hand-edit.
"""Keep personal Mnemorph memory, and un-scrubbed Core commits, off a public
Git remote.

This one file plays three roles.

Run directly, `install [REMOTE ...]` copies this script, byte for byte, into
the repository's *common* git directory as `hooks/pre-push` (so a linked
worktree is covered too). For each named REMOTE it requires the remote to
already be configured, reads its current push URL, and pins that URL under
a per-remote `mnemorph.privateurl.<name>` config key (also recording the
name in the multi-valued `mnemorph.private` key, so older name-only entries
stay readable). A private remote is one an instance already trusts with its
full history, such as its own `origin`; never a public fork of Core. When
the URL is a github.com repository and `gh` is on PATH, install refuses to
pin a repository `gh repo view` reports as PUBLIC; when the check cannot
run, it says so and pins anyway, on the honor of the person running it.

Run directly, `review REV-RANGE` writes a review packet under the common git
directory's `mnemorph-scrub/packets/` for each commit in REV-RANGE that lacks
a recorded `clean` verdict: the push review brief, the mechanical scan hits,
the commit message and the full patch, for a fresh reviewer (a model
subagent or a human) to follow. `verdict COMMIT clean|blocked --reviewer
TEXT` then records that reviewer's judgment to `mnemorph-scrub/verdicts.jsonl`
(the latest line for a commit wins); a `clean` verdict on a commit whose
mechanical scan has hits is refused unless `--accept-scan` is given, so a
reviewer must consciously accept each hit. `status REV-RANGE` lists, per
commit, its latest verdict and scan hit count.

Installed as `pre-push`, Git runs this script before every push. A remote
counts as private -- and bypasses every check below -- only when the URL
Git is pushing to right now equals a URL pinned by `install`; a name-only
legacy entry, or a remote whose URL no longer matches what was pinned (for
example a re-pointed `origin`), is guarded like any other remote. For any
remote not private, it requires that every commit the push would add have a
full tree containing only Core paths: anything under `src/core/`, `tools/`,
`integrations/`, `memory-template/`, plus the root files `AGENTS.md`,
`README.md`, `CONTRIBUTING.md`, `LICENSE`, `memory-limits.json`,
`.gitattributes` and `.gitignore`; and then, for each such commit, that it
carry a recorded `clean` review verdict (with `--accept-scan` if the
mechanical scan -- home-directory paths, email addresses, terms from a
private `terms.txt`, and eight-word runs shared with the instance's own
private memory -- still finds hits). When the pushed ref is an annotated
tag, its own message is scanned the same way and gates the tagged commit
alongside its own content. Anything else refuses the push, fails closed on
error, and names exactly what to run to clear it. Standard library only, so
the copied hook needs nothing installed beyond Python and Git; it cannot
import other repository files, so verdicts, terms and memory are read
straight from the common git directory and the main worktree at hook time.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

GUARD_MARKER = "Mnemorph push guard"  # identifies a hook file as ours, any version
CONFIG_KEY = "mnemorph.private"
PRIVATE_URL_KEY_PREFIX = "mnemorph.privateurl."
CORE_DIRS = ("src/core/", "tools/", "integrations/", "memory-template/")
CORE_ROOT_FILES = {
    "AGENTS.md", "README.md", "CONTRIBUTING.md", "LICENSE",
    "memory-limits.json", ".gitattributes", ".gitignore",
}

# --- scrub review state (lives under the common git dir; never in the tree) -

SCRUB_DIRNAME = "mnemorph-scrub"
PACKETS_DIRNAME = "packets"
VERDICTS_FILENAME = "verdicts.jsonl"
TERMS_FILENAME = "terms.txt"

# --- mechanical scan ---------------------------------------------------------

HOME_PATH_RE = re.compile(r"(?:/Users/|/home/)([A-Za-z0-9_.<>-]+)/")
PLACEHOLDER_NAMES = {
    "u", "x", "me", "you", "<you>", "someone", "user", "username",
    "example", "runner", "<user>",
}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_EXEMPT_DOMAINS = {"example.com", "example.org", "example.net"}
EMAIL_EXEMPT_ADDRESSES = {"noreply@anthropic.com"}

# A run of this many consecutive normalised words shared with private memory
# is a hit; see build_memory_shingles().
SHINGLE_SIZE = 8
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")

GITHUB_URL_RE = re.compile(
    r"^(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)

Hit = tuple[str, int, str, str]  # path, line, kind, matched text


def is_zero(sha: str) -> bool:
    """True for git's all-zero sha, used for both SHA-1 (40) and SHA-256 (64) repos."""
    return bool(sha) and set(sha) <= {"0"}


def is_core_path(path: str) -> bool:
    return path in CORE_ROOT_FILES or path.startswith(CORE_DIRS)


# --- small git helpers ------------------------------------------------------


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], text=True, capture_output=True)


def git_out(*args: str) -> str:
    r = git(*args)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def git_lines(*args: str) -> list[str]:
    return [line for line in git_out(*args).splitlines() if line.strip()]


def config_get_all(key: str) -> list[str]:
    r = git("config", "--get-all", key)
    if r.returncode != 0:
        return []  # unset
    return [line for line in r.stdout.splitlines() if line.strip()]


def config_get(key: str) -> str | None:
    r = git("config", "--get", key)
    if r.returncode != 0:
        return None
    value = r.stdout.strip()
    return value or None


def remote_names() -> set[str]:
    return set(git_lines("remote"))


def common_git_dir() -> Path:
    d = Path(git_out("rev-parse", "--git-common-dir").strip())
    if not d.is_absolute():
        d = Path.cwd() / d
    return d


def scrub_dir() -> Path:
    return common_git_dir() / SCRUB_DIRNAME


def pinned_url(remote: str) -> str | None:
    return config_get(f"{PRIVATE_URL_KEY_PREFIX}{remote}")


def pinned_urls(private_names: set[str]) -> set[str]:
    urls = set()
    for name in private_names:
        u = pinned_url(name)
        if u:
            urls.add(u)
    return urls


# --- install -----------------------------------------------------------


def github_owner_repo(url: str) -> tuple[str, str] | None:
    m = GITHUB_URL_RE.match(url.strip())
    if not m:
        return None
    return m.group("owner"), m.group("repo")


def github_visibility(url: str) -> tuple[str, str]:
    """(status, detail): status is "public", "nonpublic" or "skipped"."""
    owner_repo = github_owner_repo(url)
    if owner_repo is None:
        return "skipped", "not a github.com URL"
    if shutil.which("gh") is None:
        return "skipped", "gh not found on PATH"
    owner, repo = owner_repo
    r = subprocess.run(["gh", "repo", "view", f"{owner}/{repo}", "--json", "visibility"],
                       text=True, capture_output=True)
    if r.returncode != 0:
        detail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "gh repo view failed"
        return "skipped", detail
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return "skipped", "could not parse gh output"
    visibility = str(data.get("visibility", "")).upper()
    if not visibility:
        return "skipped", "gh did not report a visibility"
    if visibility == "PUBLIC":
        return "public", f"{owner}/{repo} is PUBLIC on GitHub"
    return "nonpublic", f"{owner}/{repo} is {visibility} on GitHub"


def cmd_install(args: argparse.Namespace) -> int:
    common_dir = common_git_dir()
    hooks_dir = common_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path = hooks_dir / "pre-push"

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8", errors="replace")
        if GUARD_MARKER not in existing:
            raise SystemExit(
                f"refusing to install: {hook_path} already exists and is not a "
                "Mnemorph push guard. Remove or back it up, then re-run install."
            )

    source = Path(__file__).resolve().read_text(encoding="utf-8")
    hook_path.write_text(source, encoding="utf-8")
    hook_path.chmod(hook_path.stat().st_mode | 0o111)

    configured = remote_names()
    for remote in args.remotes:
        if remote not in configured:
            raise SystemExit(
                f"refusing to mark {remote} private: no remote named {remote!r} "
                "is configured in this repository"
            )
        url = git_out("remote", "get-url", "--push", remote).strip()
        status, detail = github_visibility(url)
        if status == "public":
            raise SystemExit(f"refusing to mark {remote} private: {detail}")
        if status == "skipped":
            print(f"visibility check skipped for {remote} ({detail}); pinning its URL anyway")
        else:
            print(f"visibility check for {remote}: {detail}")
        if remote not in set(config_get_all(CONFIG_KEY)):
            git_out("config", "--add", CONFIG_KEY, remote)
        git_out("config", f"{PRIVATE_URL_KEY_PREFIX}{remote}", url)

    private = set(config_get_all(CONFIG_KEY))
    where = ", ".join(sorted(private)) if private else "(none)"
    print(f"installed pre-push hook at {hook_path}; private remotes: {where}")
    return 0


# --- scrub review state: verdicts and terms ---------------------------------


def load_verdicts() -> dict[str, dict]:
    """Latest recorded verdict per full commit hash; later lines win."""
    path = scrub_dir() / VERDICTS_FILENAME
    if not path.exists():
        return {}
    latest: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        commit = rec.get("commit")
        if commit:
            latest[commit] = rec
    return latest


def load_terms() -> list[str]:
    """Non-empty, non-comment lines of the private terms.txt; absent means none."""
    path = scrub_dir() / TERMS_FILENAME
    if not path.exists():
        return []
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line)
    return terms


def resolve_commit(ref: str) -> str:
    return git_out("rev-parse", "--verify", f"{ref}^{{commit}}").strip()


# --- mechanical scan ---------------------------------------------------------


def format_hit(hit: Hit) -> str:
    path, line, kind, text = hit
    return f"{path}:{line}: {kind}: {text}"


def is_email_exempt(address: str) -> bool:
    lowered = address.lower()
    if lowered in EMAIL_EXEMPT_ADDRESSES:
        return True
    domain = lowered.rsplit("@", 1)[-1]
    return domain in EMAIL_EXEMPT_DOMAINS or domain.endswith(".invalid")


def compile_term_patterns() -> list[re.Pattern]:
    return [re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE) for t in load_terms()]


def normalized_tokens(text: str) -> list[str]:
    return [m.group().lower() for m in WORD_RE.finditer(text)]


def main_worktree_path() -> Path:
    """The repository's main worktree: the first entry of `git worktree list`."""
    out = git_out("worktree", "list", "--porcelain")
    for line in out.splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree "):].strip())
    return Path(git_out("rev-parse", "--show-toplevel").strip())  # defensive fallback


def memory_files(main_worktree: Path) -> list[Path]:
    """Markdown/JSONL files under the main worktree's src/, excluding src/core/."""
    src = main_worktree / "src"
    if not src.is_dir():
        return []
    core = (src / "core").resolve()
    found = []
    for p in src.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in (".md", ".jsonl"):
            continue
        rp = p.resolve()
        if rp == core or core in rp.parents:
            continue
        found.append(p)
    return found


def build_memory_shingles() -> dict[str, str]:
    """8-word shingle -> memory file path (relative to the main worktree).

    Private memory is Markdown and JSONL files under the main worktree's
    src/, excluding src/core/; a plain Mnemorph-Core checkout has none, so
    this is empty and the overlap check is skipped entirely. Build once per
    run and pass the result to mechanical_scan/scan_line for every commit
    checked in that run -- rebuilding per commit would not stay fast.
    """
    try:
        main_wt = main_worktree_path()
    except RuntimeError:
        return {}
    files = memory_files(main_wt)
    shingles: dict[str, str] = {}
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        words = normalized_tokens(text)
        if len(words) < SHINGLE_SIZE:
            continue
        try:
            rel = str(f.relative_to(main_wt))
        except ValueError:
            rel = str(f)
        for i in range(len(words) - SHINGLE_SIZE + 1):
            key = " ".join(words[i:i + SHINGLE_SIZE])
            shingles.setdefault(key, rel)
    return shingles


def scan_line(path: str, line_no: int, text: str, term_patterns: list[re.Pattern],
             memory_shingles: dict[str, str]) -> list[Hit]:
    hits: list[Hit] = []
    for m in HOME_PATH_RE.finditer(text):
        if m.group(1).lower() not in PLACEHOLDER_NAMES:
            hits.append((path, line_no, "home-path", m.group(0)))
    for m in EMAIL_RE.finditer(text):
        if not is_email_exempt(m.group(0)):
            hits.append((path, line_no, "email", m.group(0)))
    for pattern in term_patterns:
        for m in pattern.finditer(text):
            hits.append((path, line_no, "term", m.group(0)))
    if memory_shingles:
        tokens = list(WORD_RE.finditer(text))
        for i in range(len(tokens) - SHINGLE_SIZE + 1):
            window = tokens[i:i + SHINGLE_SIZE]
            key = " ".join(m.group().lower() for m in window)
            mem_file = memory_shingles.get(key)
            if mem_file:
                snippet = text[window[0].start():window[-1].end()]
                hits.append((path, line_no, "memory-overlap", f"{snippet} (in {mem_file})"))
    return hits


def scan_lines(path: str, text: str, term_patterns: list[re.Pattern],
              memory_shingles: dict[str, str]) -> list[Hit]:
    hits: list[Hit] = []
    for i, line in enumerate(text.splitlines(), start=1):
        hits.extend(scan_line(path, i, line, term_patterns, memory_shingles))
    return hits


def added_lines_by_file(patch_text: str) -> list[tuple[str, int, str]]:
    """(path, new-file line number, text) for each added line of a unified diff."""
    results: list[tuple[str, int, str]] = []
    current_path: str | None = None
    new_line: int | None = None
    in_binary = False
    for raw in patch_text.splitlines():
        if raw.startswith("diff --git "):
            current_path = None
            new_line = None
            in_binary = False
            continue
        if raw.startswith("Binary files "):
            in_binary = True
            continue
        if raw.startswith("--- "):
            continue  # old-file header; +++ carries the path we scan
        if raw.startswith("+++ "):
            target = raw[4:].strip()
            if target == "/dev/null":
                current_path = None
            elif target.startswith(("a/", "b/")):
                current_path = target[2:]
            else:
                current_path = target
            new_line = None
            continue
        if raw.startswith("@@"):
            m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if m:
                new_line = int(m.group(1))
            continue
        if in_binary or current_path is None or new_line is None:
            continue
        if raw.startswith("+"):
            results.append((current_path, new_line, raw[1:]))
            new_line += 1
        elif raw.startswith("-"):
            pass  # removed line: doesn't advance the new-file line number
        elif raw.startswith("\\"):
            pass  # "\ No newline at end of file"
        else:
            new_line += 1  # context line
    return results


def git_show_patch(commit: str) -> str:
    return git_out("show", "--no-color", "--patch", commit)


def mechanical_scan(commit: str, memory_shingles: dict[str, str] | None = None,
                    term_patterns: list[re.Pattern] | None = None) -> list[Hit]:
    """Scan a commit's message and added patch lines. A floor, never sufficient alone."""
    if memory_shingles is None:
        memory_shingles = build_memory_shingles()
    if term_patterns is None:
        term_patterns = compile_term_patterns()
    hits: list[Hit] = []
    message = git_out("log", "-1", "--format=%B", commit)
    hits.extend(scan_lines("message", message, term_patterns, memory_shingles))
    for path, line_no, text in added_lines_by_file(git_show_patch(commit)):
        hits.extend(scan_line(path, line_no, text, term_patterns, memory_shingles))
    return hits


# --- review / verdict / status ----------------------------------------------


def cmd_review(args: argparse.Namespace) -> int:
    verdicts = load_verdicts()
    memory_shingles = build_memory_shingles()
    term_patterns = compile_term_patterns()
    review_brief_path = Path(__file__).resolve().parent / "push_review.md"
    review_brief = review_brief_path.read_text(encoding="utf-8").rstrip("\n")
    packets_dir = scrub_dir() / PACKETS_DIRNAME
    packets_dir.mkdir(parents=True, exist_ok=True)

    already_clean = 0
    for commit in git_lines("rev-list", "--reverse", args.rev_range):
        rec = verdicts.get(commit)
        if rec and rec.get("verdict") == "clean":
            already_clean += 1
            continue
        hits = mechanical_scan(commit, memory_shingles, term_patterns)
        scan_section = ("\n".join(f"- {format_hit(h)}" for h in hits)
                        if hits else "(no mechanical scan hits)")
        message = git_out("log", "-1", "--format=%B", commit).rstrip("\n")
        patch = git_show_patch(commit)
        packet = (
            f"{review_brief}\n\n"
            "---\n\n"
            "## Mechanical scan hits\n\n"
            f"{scan_section}\n\n"
            "---\n\n"
            "## Commit message\n\n"
            f"{message}\n\n"
            "---\n\n"
            "## Patch\n\n"
            f"{patch}\n"
        )
        packet_path = packets_dir / f"{commit}.md"
        packet_path.write_text(packet, encoding="utf-8")
        print(str(packet_path))

    print(f"{already_clean} commit(s) already have a clean verdict")
    return 0


def cmd_verdict(args: argparse.Namespace) -> int:
    try:
        commit = resolve_commit(args.commit)
    except RuntimeError as e:
        print(f"push_guard: {e}", file=sys.stderr)
        return 1

    if args.outcome == "clean" and not args.accept_scan:
        hits = mechanical_scan(commit)
        if hits:
            print(
                f"push_guard: refusing clean verdict for {commit}: "
                f"{len(hits)} mechanical scan hit(s) not accepted; pass "
                "--accept-scan once a reviewer has consciously accepted them:",
                file=sys.stderr,
            )
            for h in hits:
                print(f"  {format_hit(h)}", file=sys.stderr)
            return 1

    d = scrub_dir()
    d.mkdir(parents=True, exist_ok=True)
    record = {
        "commit": commit,
        "verdict": args.outcome,
        "reviewer": args.reviewer,
        "note": args.note or "",
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "accept_scan": bool(args.accept_scan),
    }
    with (d / VERDICTS_FILENAME).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"recorded {args.outcome} verdict for {commit} ({args.reviewer})")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    verdicts = load_verdicts()
    memory_shingles = build_memory_shingles()
    term_patterns = compile_term_patterns()
    for commit in git_lines("rev-list", "--reverse", args.rev_range):
        short = git_out("rev-parse", "--short", commit).strip()
        subject = git_out("log", "-1", "--format=%s", commit).strip()
        rec = verdicts.get(commit)
        verdict = rec["verdict"] if rec else "unreviewed"
        hits = mechanical_scan(commit, memory_shingles, term_patterns)
        print(f"{short}\t{verdict}\thits={len(hits)}\t{subject}")
    return 0


# --- the hook itself -----------------------------------------------------


def non_core_paths(commit: str) -> list[str]:
    paths = git_lines("ls-tree", "-r", "--name-only", commit)
    return sorted(p for p in paths if not is_core_path(p))


def commits_to_check(remote: str, named: bool, local_sha: str, remote_sha: str) -> list[str]:
    """Commits reachable from local_sha the push would add: excludes commits
    already reachable from the remote's remote-tracking refs (when remote is a
    configured name) and from remote_sha itself (when we already have it).
    local_sha may be an annotated tag object; rev-list dereferences it."""
    rev_args = [local_sha, "--not"]
    if named:
        rev_args.append(f"--remotes={remote}")
    if not is_zero(remote_sha) and git("cat-file", "-e", f"{remote_sha}^{{commit}}").returncode == 0:
        rev_args.append(remote_sha)
    return git_lines("rev-list", "--reverse", *rev_args)


def refuse(remote: str, commit: str, bad: list[str], hint: str = "") -> str:
    subject = git_out("log", "-1", "--format=%s", commit).strip()
    short = git_out("rev-parse", "--short", commit).strip()
    shown = "\n".join(f"  {p}" for p in bad[:5])
    more = f"\n  ... and {len(bad) - 5} more" if len(bad) > 5 else ""
    return (
        f"push_guard: refusing push to {remote}: commit {short} \"{subject}\" "
        f"is not Core-only:\n{shown}{more}\n"
        f"If {remote} is meant to hold personal history, mark it private: "
        f"python3 tools/push_guard.py install {remote}{hint}"
    )


def review_range_hint(commit: str) -> str:
    """A REV-RANGE that `review` accepts to cover just this commit."""
    has_parent = git("rev-parse", "--verify", "-q", f"{commit}^").returncode == 0
    return f"{commit}^..{commit}" if has_parent else commit


def refuse_unreviewed(remote: str, commit: str, reason: str, hint: str = "") -> str:
    subject = git_out("log", "-1", "--format=%s", commit).strip()
    short = git_out("rev-parse", "--short", commit).strip()
    return (
        f"push_guard: refusing push to {remote}: commit {short} \"{subject}\" {reason}\n"
        f"Run: python3 tools/push_guard.py review {review_range_hint(commit)}\n"
        "Have a fresh reviewer follow each packet against tools/push_review.md, "
        "then record each verdict with: python3 tools/push_guard.py verdict "
        f"{short} clean|blocked --reviewer <name>{hint}"
    )


def privacy_hint(remote: str, private_names: set[str]) -> str:
    if remote in private_names and pinned_url(remote) is None:
        return (
            f"\n{remote} is marked private by name only, with no pinned URL; "
            f"re-run: python3 tools/push_guard.py install {remote}"
        )
    return ""


def is_annotated_tag(sha: str) -> bool:
    r = git("cat-file", "-t", sha)
    return r.returncode == 0 and r.stdout.strip() == "tag"


def tag_message(sha: str) -> str:
    """Full annotation text (subject+body) of an annotated tag object."""
    raw = git_out("cat-file", "-p", sha)
    _header, _, body = raw.partition("\n\n")
    return body


def tagged_commit(sha: str) -> str:
    return git_out("rev-parse", f"{sha}^{{commit}}").strip()


def run_hook(argv: list[str]) -> int:
    try:
        return _run_hook(argv)
    except Exception as e:  # fail closed: refuse rather than risk a leak
        print(f"push_guard: refusing push: {e}", file=sys.stderr)
        return 1


def _run_hook(argv: list[str]) -> int:
    if len(argv) < 2:
        raise RuntimeError("missing remote name; not invoked as a pre-push hook")
    remote = argv[1]
    remote_url = argv[2].strip() if len(argv) > 2 and argv[2] else remote
    private_names = set(config_get_all(CONFIG_KEY))
    updates = sys.stdin.read().splitlines()

    if remote_url in pinned_urls(private_names):
        return 0  # this push's current URL matches a URL pinned private

    hint = privacy_hint(remote, private_names)
    named = remote in remote_names()
    verdicts = load_verdicts()
    memory_shingles = build_memory_shingles()
    term_patterns = compile_term_patterns()

    for line in updates:
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 4:
            raise RuntimeError(f"unexpected pre-push input line: {line!r}")
        local_ref, local_sha, _remote_ref, remote_sha = parts
        if is_zero(local_sha):
            continue  # a deletion sends nothing

        # An annotated tag's own message is scanned too, and gates the
        # commit it tags alongside that commit's own content.
        tag_hits: list[Hit] = []
        tagged: str | None = None
        if local_ref.startswith("refs/tags/") and is_annotated_tag(local_sha):
            tagged = tagged_commit(local_sha)
            tag_hits = scan_lines("tag message", tag_message(local_sha), term_patterns, memory_shingles)

        for commit in commits_to_check(remote, named, local_sha, remote_sha):
            bad = non_core_paths(commit)
            if bad:
                print(refuse(remote, commit, bad, hint), file=sys.stderr)
                return 1
            rec = verdicts.get(commit)
            if not rec or rec.get("verdict") != "clean":
                print(refuse_unreviewed(remote, commit, "has no recorded clean review verdict", hint),
                     file=sys.stderr)
                return 1
            hits = mechanical_scan(commit, memory_shingles, term_patterns)
            if commit == tagged:
                hits = hits + tag_hits
            if hits and not rec.get("accept_scan"):
                print(refuse_unreviewed(
                    remote, commit,
                    f"has {len(hits)} mechanical scan hit(s) not accepted by its clean verdict",
                    hint,
                ), file=sys.stderr)
                return 1
    return 0


# --- entry point -----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if Path(argv[0]).name == "pre-push":
        return run_hook(argv)
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("install", help="install this script as the pre-push hook")
    p.add_argument("remotes", nargs="*", metavar="REMOTE",
                   help="remote(s) allowed to receive full history unchecked")
    p.set_defaults(fn=cmd_install)

    p = sub.add_parser("review", help="write scrub packets for commits in REV-RANGE without a clean verdict")
    p.add_argument("rev_range", metavar="REV-RANGE")
    p.set_defaults(fn=cmd_review)

    p = sub.add_parser("verdict", help="record a fresh reviewer's verdict for a commit")
    p.add_argument("commit", metavar="COMMIT")
    p.add_argument("outcome", metavar="clean|blocked", choices=["clean", "blocked"])
    p.add_argument("--reviewer", required=True, help="who reviewed it")
    p.add_argument("--note", default="", help="one-line note")
    p.add_argument("--accept-scan", action="store_true", dest="accept_scan",
                   help="a clean verdict despite mechanical scan hits, consciously accepted")
    p.set_defaults(fn=cmd_verdict)

    p = sub.add_parser("status", help="show recorded verdicts and scan hit counts for REV-RANGE")
    p.add_argument("rev_range", metavar="REV-RANGE")
    p.set_defaults(fn=cmd_status)

    args = ap.parse_args(argv[1:])
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
