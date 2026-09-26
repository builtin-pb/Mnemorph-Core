#!/usr/bin/env python3
"""Bounded Git recovery and character-budget inspection; never changes files."""
from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


class MemoryError(ValueError):
    """An actionable input or repository error."""


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "--literal-pathspecs", "-C", str(root), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise MemoryError(result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def path_arg(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ".." in path.parts or value.startswith("-")
            or "\x00" in value or "\\" in value or ":" in value
            or ".git" in path.parts):
        raise argparse.ArgumentTypeError("use a repository-relative path without traversal or Git metadata")
    return path.as_posix()


def bounded(minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("expected an integer") from None
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return number
    return parse


def revision(root: Path, value: str) -> str:
    if not value or value.startswith("-") or "\x00" in value:
        raise MemoryError("invalid revision")
    return git(root, "rev-parse", "--verify", "--end-of-options", value + "^{commit}").decode().strip()


def directory_path(root: Path, commit: str, path: str) -> bool:
    def object_type(at: str) -> bytes | None:
        fields = git(root, "ls-tree", at, "--", path).split(b" ", 2)
        return fields[1] if len(fields) > 1 else None

    if path == ".":
        return True
    kind = object_type(commit)
    if kind is not None:
        return kind == b"tree"
    # A deleted directory is absent at the tip; inspect the parents of its last
    # change rather than asking --follow to interpret it as a single file.
    parents = git(root, "log", "-1", "--format=%P", commit, "--", path).decode().split()
    return any(object_type(parent) == b"tree" for parent in parents)


def changed_path_groups(raw: bytes) -> list[list[str]]:
    """Read NUL-delimited Git name/status records without splitting filenames."""
    fields = raw.decode("utf-8", "replace").split("\x00")
    groups, index = [], 0
    while index < len(fields):
        status = fields[index].lstrip("\n")
        if not status:
            index += 1
            continue
        count = 2 if status.startswith(("R", "C")) else 1
        groups.append(fields[index + 1:index + 1 + count])
        index += count + 1
    return groups


def history(root: Path, args) -> dict:
    if not args.path and not args.query:
        raise MemoryError("history requires --path or a nonempty --query")
    commit = revision(root, args.revision)
    # Keep Git's path selection beside each commit: --follow can cross a rename
    # that -S omits because it did not change the queried occurrence count.
    separator = b"\x00\x00COMMIT\x00"
    options = ["log", "--format=%x00%x00COMMIT%x00%H%x00", "--name-status", "-z",
               f"-n{args.limit + 1}", "--find-renames", "--diff-merges=first-parent"]
    if args.query:
        options += ["-S" + args.query]
    directory = bool(args.path and directory_path(root, commit, args.path))
    if args.path and not directory:
        options += ["--follow"]
    changes = git(root, *options, commit, "--", *([args.path] if args.path else [])).split(separator)[1:]
    records = []
    for change in changes[:args.limit]:
        raw_commit, _, raw_paths = change.partition(b"\x00")
        item = raw_commit.decode("ascii")
        fields = git(root, "show", "-s", "--format=%H%x00%P%x00%cI%x00%s", item).decode("utf-8", "replace").rstrip("\n").split("\x00", 3)
        groups = changed_path_groups(raw_paths)
        paths = list(dict.fromkeys(path for group in groups for path in group))
        if directory:
            # A directory-scoped arrival/departure may expose only one side of
            # a rename. Include its counterpart, not unrelated commit changes.
            whole = git(root, "diff-tree", "--root", "--no-commit-id", "--name-status",
                        "--find-renames", "-r", "-m", "-z", item)
            selected = set(paths)
            for group in changed_path_groups(whole):
                if len(group) == 2 and selected.intersection(group):
                    paths.extend(path for path in group if path not in paths)
        records.append({"commit": fields[0], "parents": fields[1].split(), "date": fields[2],
                        "subject": fields[3][:500], "subject_truncated": len(fields[3]) > 500,
                        "paths": paths[:30], "paths_truncated": len(paths) > 30})
    return {"revision": commit, "path": args.path, "query": args.query, "records": records,
            "truncated": len(changes) > args.limit,
            "note": "Literal query matches changes in occurrence count, not all historical content; merge diffs compare against the first parent. Paths follow the requested scope, including rename counterparts; unscoped queries show matching changes. Use show with a commit (or its parent) and historical path."}


def show(root: Path, args) -> dict:
    commit = revision(root, args.revision)
    if args.path is None:
        headers, message = git(root, "cat-file", "commit", commit).split(b"\n\n", 1)
        body = message.decode("utf-8", "replace")
        parents = [line[7:].decode("ascii") for line in headers.splitlines() if line.startswith(b"parent ")]
        offset = args.offset or 0
        if offset > len(body):
            raise MemoryError("offset is beyond the commit message")
        text = body[offset:offset + args.max_chars]
        end = offset + len(text)
        return {"commit": commit, "parents": parents, "kind": "commit_message",
                "total_chars": len(body), "chars_truncated": end < len(body),
                "start_offset": offset, "next_offset": end if end < len(body) else None,
                "text": text}
    blob = git(root, "rev-parse", "--verify", commit + ":" + args.path).decode().strip()
    if git(root, "cat-file", "-t", blob).strip() != b"blob":
        raise MemoryError("requested path is not a file blob")
    data = git(root, "cat-file", "blob", blob)
    try:
        body = data.decode("utf-8")
    except UnicodeDecodeError:
        raise MemoryError("requested blob is not UTF-8 text") from None
    if "\x00" in body:
        raise MemoryError("requested blob is binary (contains NUL)")
    # Physical source lines end at LF. Other Unicode separators are content;
    # preserve CRLF bytes and offsets within a long line without normalization.
    total_lines = body.count("\n") + bool(body and not body.endswith("\n"))
    if args.offset is not None:
        offset = args.offset
        if offset > len(body):
            raise MemoryError("offset is beyond the file text")
        start_line = body.count("\n", 0, offset) + 1
    else:
        start_line = args.start_line or 1
        offset = 0
        for _ in range(min(start_line - 1, total_lines)):
            newline = body.find("\n", offset)
            offset = newline + 1 if newline >= 0 else len(body)
    selected_end = offset
    lines_selected = 0
    while selected_end < len(body) and lines_selected < args.lines:
        newline = body.find("\n", selected_end)
        selected_end = newline + 1 if newline >= 0 else len(body)
        lines_selected += 1
    end = min(selected_end, offset + args.max_chars)
    return {"commit": commit, "blob": blob, "path": args.path, "total_lines": total_lines,
            "total_chars": len(body), "start_line": start_line, "start_offset": offset,
            "lines_selected": lines_selected, "lines_truncated": selected_end < len(body),
            "chars_truncated": end < selected_end,
            "next_offset": end if end < len(body) else None, "text": body[offset:end]}


def glob_matches(path: str, pattern: str) -> bool:
    """Path globs: * and ? stay within segments; ** spans zero or more segments."""
    from functools import lru_cache
    parts, pats = path.split("/"), pattern.split("/")

    @lru_cache(None)
    def match(i, j):
        if j == len(pats):
            return i == len(parts)
        if pats[j] == "**":
            return match(i, j + 1) or (i < len(parts) and match(i + 1, j))
        return i < len(parts) and fnmatch.fnmatchcase(parts[i], pats[j]) and match(i + 1, j + 1)
    return match(0, 0)


def indexed_src_markdown(path: str, body: str) -> bool:
    """Subject memory is indexed by an id and summary in its opening header."""
    if not path.startswith("src/") or not path.endswith(".md"):
        return False
    rows = re.split(r"\r\n|\r|\n", body)
    if not rows or rows[0].strip() != "---":
        return False
    header = []
    for row in rows[1:]:
        if row.strip() == "---":
            return (any(re.match(r"^id:\s*\S", item) for item in header)
                    and any(re.match(r"^summary:\s*\S", item) for item in header))
        header.append(row)
    return False


INSTANCE_LIMITS = "src/memory-limits.json"


def _positive(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _check_rules(rules, source: str) -> None:
    for rule in rules:
        if (not isinstance(rule, dict) or set(rule) != {"pattern", "limit"}
                or not isinstance(rule["pattern"], str) or not rule["pattern"]
                or not _positive(rule["limit"])):
            raise MemoryError(f"invalid {source} rule: require pattern and positive integer limit")
        try:
            path_arg(rule["pattern"])
        except argparse.ArgumentTypeError as exc:
            raise MemoryError(f"invalid budget pattern: {exc}") from None


def _check_files(files: dict, config: dict) -> None:
    for path, limit in files.items():
        try:
            clean = path_arg(path)
        except argparse.ArgumentTypeError as exc:
            raise MemoryError(f"invalid budget path: {exc}") from None
        if clean != path or not _positive(limit):
            raise MemoryError("invalid budget override: require normalized path and positive integer limit")
        if not (any(glob_matches(path, pattern) for pattern in config["include"])
                or config["indexed_src"] and path.startswith("src/") and path.endswith(".md")):
            raise MemoryError(f"budget override is outside scope: {path}")


def load_limits(root: Path) -> dict:
    """The shared policy in memory-limits.json, extended by an instance's optional src/memory-limits.json."""
    try:
        config = json.loads((root / "memory-limits.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MemoryError(f"cannot read memory-limits.json: {exc}") from None
    if (not isinstance(config, dict) or set(config) != {"default", "include", "indexed_src", "rules", "files"}
            or not _positive(config["default"]) or not isinstance(config["rules"], list)
            or not isinstance(config["include"], list) or not isinstance(config["indexed_src"], bool)
            or not (config["include"] or config["indexed_src"])
            or not isinstance(config["files"], dict)):
        raise MemoryError("invalid memory-limits.json: require positive default, include and rules lists, indexed_src boolean, and files object")
    for pattern in config["include"]:
        if not isinstance(pattern, str) or not pattern:
            raise MemoryError("invalid budget scope: require nonempty path patterns")
        try:
            path_arg(pattern)
        except argparse.ArgumentTypeError as exc:
            raise MemoryError(f"invalid budget scope: {exc}") from None
    _check_rules(config["rules"], "memory-limits.json")
    _check_files(config["files"], config)
    # An instance keeps allowances for its own memory beside that memory, so the
    # shared file names no personal files. Its rules follow the shared ones and
    # its exact allowances replace shared ones for the same path.
    local = root / INSTANCE_LIMITS
    if local.is_file():
        try:
            extra = json.loads(local.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MemoryError(f"cannot read {INSTANCE_LIMITS}: {exc}") from None
        if (not isinstance(extra, dict) or not set(extra) <= {"rules", "files"}
                or not isinstance(extra.get("rules", []), list) or not isinstance(extra.get("files", {}), dict)):
            raise MemoryError(f"invalid {INSTANCE_LIMITS}: allow only a rules list and a files object")
        _check_rules(extra.get("rules", []), INSTANCE_LIMITS)
        _check_files(extra.get("files", {}), config)
        config["rules"] = config["rules"] + extra.get("rules", [])
        config["files"] = {**config["files"], **extra.get("files", {})}
    return config


def sizes(root: Path, args) -> dict:
    config = load_limits(root)
    paths = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *([args.path] if args.path else [])).decode("utf-8").split("\x00")
    skipped = {"binary": 0, "symlink": 0, "deleted": 0, "nonregular": 0, "out_of_scope": 0}
    records = []
    for name in sorted(set(p for p in paths if p)):
        direct = any(glob_matches(name, pattern) for pattern in config["include"])
        indexed_candidate = config["indexed_src"] and name.startswith("src/") and name.endswith(".md")
        if not (direct or indexed_candidate):
            skipped["out_of_scope"] += 1
            continue
        path = root / name
        if path.is_symlink():
            skipped["symlink"] += 1
            continue
        if not path.resolve().is_relative_to(root):
            raise MemoryError(f"file resolves outside repository: {name}")
        if not path.exists():
            # A tracked file may be deleted in the worktree before its deletion
            # is staged. It has no current text to measure.
            skipped["deleted"] += 1
            continue
        if not path.is_file():
            skipped["nonregular"] += 1
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise MemoryError(f"cannot read {name}: {exc}") from None
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped["binary"] += 1
            continue
        if "\x00" in body:
            skipped["binary"] += 1
            continue
        if indexed_candidate and not direct and not indexed_src_markdown(name, body):
            skipped["out_of_scope"] += 1
            continue
        limit, basis = config["default"], "default"
        for rule in config["rules"]:
            if glob_matches(name, rule["pattern"]):
                limit, basis = rule["limit"], rule["pattern"]
        if name in config["files"]:
            limit, basis = config["files"][name], "file override"
        records.append({"path": name, "chars": len(body), "limit": limit, "basis": basis,
                        "over_by": max(0, len(body) - limit), "ratio": round(len(body) / limit, 3)})
    if args.path is None:
        missing_overrides = set(config["files"]) - {record["path"] for record in records}
        if missing_overrides:
            raise MemoryError("budget overrides have no governed file: " + ", ".join(sorted(missing_overrides)))
    selected = sorted((r for r in records if args.all or r["over_by"]),
                      key=lambda r: (-r["chars"] / r["limit"], r["path"]))
    return {"text_files": len(records), "total_chars": sum(r["chars"] for r in records),
            "over_limit_files": sum(r["over_by"] > 0 for r in records), "skipped": skipped,
            "records": selected[:args.limit], "truncated": len(selected) > args.limit,
            "note": "Whole-file Unicode characters, not tokens or bytes. Over-limit files fail the size check."}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, epilog="Read-only JSON output. Use history selectively for failures or narrow exact matches, not every task.")
    result.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="repository root (default: this tool's repository)")
    commands = result.add_subparsers(dest="command", required=True)
    h = commands.add_parser("history", help="find bounded change records, without patches",
                            epilog="Example: history --path src/core/memory/reflect.md --query 'recovered material'")
    h.add_argument("--path", type=path_arg, help="literal file or directory; single files follow renames")
    h.add_argument("--query", help="exact literal pickaxe query, not a regular expression")
    h.add_argument("--revision", default="HEAD")
    h.add_argument("--limit", type=bounded(1, 50), default=5)
    s = commands.add_parser("show", help="read an exact historical blob or commit message, without checkout",
                            epilog="Examples: show HEAD~1 (commit rationale); show HEAD~1 src/core/memory/reflect.md --start-line 20 --lines 30 (file excerpt).")
    s.add_argument("revision")
    s.add_argument("path", type=path_arg, nargs="?", help="omit to read the bounded commit message")
    position = s.add_mutually_exclusive_group()
    position.add_argument("--start-line", type=bounded(1, 1000000000), help="first LF-delimited line of a file excerpt (default: 1)")
    position.add_argument("--offset", type=bounded(0, 1000000000), help="zero-based Unicode character offset; resume using next_offset and the returned commit")
    s.add_argument("--lines", type=bounded(1, 200), default=60, help="maximum lines in a file excerpt")
    s.add_argument("--max-chars", type=bounded(1, 20000), default=6000)
    z = commands.add_parser("sizes", help="inspect per-file character limits from memory-limits.json and an instance's src/memory-limits.json",
                            epilog="Example: sizes --path src/core --limit 10. "
                                   "Limit-rule patterns in memory-limits.json use * within a segment and ** across directories.")
    z.add_argument("--path", type=path_arg, help="literal repository-relative file or directory; no glob expansion")
    z.add_argument("--limit", type=bounded(1, 100), default=20)
    z.add_argument("--all", action="store_true", help="include within-limit files, still bounded")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        root = args.root.resolve()
        actual = Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
        if actual != root:
            raise MemoryError("--root must be the repository root")
        output = {"history": history, "show": show, "sizes": sizes}[args.command](root, args)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 1 if args.command == "sizes" and output["over_limit_files"] else 0
    except (MemoryError, OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
