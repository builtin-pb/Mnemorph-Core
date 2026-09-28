"""Fixture for the replay tests: a temporary HOME holding a repository to replay,
and fake `codex`, `claude` and `security` commands on PATH that record what they
were given (to FAKE_LOG) and write events like the real CLIs."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
REPLAY = TOOLS / "replay.py"

COMMON = r'''
import hashlib, json, os, pathlib, subprocess, sys
args = sys.argv[1:]

def tree(d):
    """Small text files under d, by relative path (following a linked top folder)."""
    d = pathlib.Path(d)
    if not d.exists():
        return None
    base = d.resolve()
    return {str(x.relative_to(base)): x.read_text(errors="ignore") for x in sorted(base.rglob("*")) if x.is_file()}

def links(d):
    d = pathlib.Path(d)
    if not d.is_dir():
        return None
    return {p.name: (os.readlink(p) if p.is_symlink() else "dir" if p.is_dir() else "file")
            for p in sorted(d.iterdir())}

def record(rec, cwd):
    w = pathlib.Path(cwd)
    def git(*a):
        r = subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    local = w / ".mnemorph-local"
    rec.update(remote=git("remote", "-v"), origin_main=git("rev-parse", "origin/main"),
               local_files=sorted(str(x.relative_to(local)) for x in local.rglob("*") if x.is_file())
               if local.is_dir() else None)
    rec.update(argv=args, cwd=cwd, cwd_agents=(w / "AGENTS.md").exists(),
               cwd_files=sorted(x.name for x in w.iterdir()),
               note=(w / "note.md").read_text() if (w / "note.md").exists() else None,
               parent_vars=sorted(k for k in os.environ if k.startswith(("CLAUDECODE", "ANTHROPIC", "AI_AGENT"))))
    pathlib.Path(os.environ["FAKE_LOG"]).write_text(json.dumps(rec))
    (w / "made-by-run.txt").write_text("run output\n")
    (w / ".mnemorph-local").mkdir(exist_ok=True)
    (w / ".mnemorph-local" / "run-note.md").write_text("ignored state the run wrote\n")
'''

CODEX = COMMON + r'''
if args[:1] == ["--version"]:
    print("codex-cli 9.9.9"); sys.exit(0)
if args[:2] == ["mcp", "list"]:
    if os.environ.get("FAKE_MCP_LIST_FAILS"):
        print("error: could not read MCP servers", file=sys.stderr); sys.exit(1)
    print(json.dumps([{"name": "docs", "enabled": True}, {"name": "app-server", "enabled": True}]))
    sys.exit(0)
prompt = sys.stdin.read()
cwd = args[args.index("-C") + 1] if "-C" in args else os.getcwd()
home = pathlib.Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") else None
record({"prompt": prompt, "codex_home": str(home) if home else None,
        "home": links(home) if home else None, "skills": links(home / "skills") if home else None,
        "agents_md": (home / "AGENTS.md").read_text() if home and (home / "AGENTS.md").exists() else None,
        "sessions": sorted(str(x.relative_to(home / "sessions")) for x in (home / "sessions").rglob("*.jsonl"))
        if home and (home / "sessions").is_dir() else None,
        "sealed_codex": tree(home / "sessions") if home else None,
        "sealed_claude": tree(pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects")
        if os.environ.get("CLAUDE_CONFIG_DIR") else None},
       cwd)
read = os.environ.get("FAKE_READ", "")
for e in [{"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"},
          {"type": "item.completed", "item": {"type": "command_execution", "command": "cat " + read,
                                              "aggregated_output": "ok", "exit_code": 0}},
          {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                               "output_tokens": 7, "reasoning_output_tokens": 3}}]:
    print(json.dumps(e), flush=True)
if "-o" in args:
    pathlib.Path(args[args.index("-o") + 1]).write_text("fake codex reply\n")
'''

CLAUDE = COMMON + r'''
if args[:1] == ["--version"]:
    print("9.9.9 (Claude Code)"); sys.exit(0)
prompt = sys.stdin.read()
cfg = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", "/nonexistent"))
tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
projects = {d.name: sorted(str(x.relative_to(d)) for x in d.rglob("*") if x.is_file())
            for d in sorted((cfg / "projects").iterdir())} if (cfg / "projects").is_dir() else {}
resumed = None
if "--resume" in args:
    hits = sorted((cfg / "projects").glob(f"*/{args[args.index('--resume') + 1]}.jsonl"))
    resumed = hits[0].read_text() if hits else None
record({"projects": projects, "transcript": resumed,"prompt": prompt, "config_dir": str(cfg), "config": links(cfg), "skills": links(cfg / "skills"),
        "claude_md": (cfg / "CLAUDE.md").read_text() if (cfg / "CLAUDE.md").exists() else None,
        "token_sha": hashlib.sha256(tok.encode()).hexdigest() if tok else None,
        "env": {k: os.environ.get(k) for k in ("ENABLE_CLAUDEAI_MCP_SERVERS", "DISABLE_AUTOUPDATER")},
        "home_dir": os.environ.get("HOME"), "home_entries": sorted(os.listdir(os.environ["HOME"])),
        "sealed_codex": tree(pathlib.Path(os.environ["HOME"]) / ".codex" / "sessions"),
        "sealed_claude": tree(cfg / "projects"),
        "home_claude_projects": tree(pathlib.Path(os.environ["HOME"]) / ".claude" / "projects")},
       os.getcwd())
read = os.environ.get("FAKE_READ", "")
skills = sorted((links(cfg / "skills") or {}).keys())
for e in [{"type": "system", "subtype": "init", "model": "claude-opus-5-5", "permissionMode": "acceptEdits",
           "mcp_servers": [], "skills": skills, "plugins": [], "apiKeySource": "none",
           "claude_code_version": "9.9.9"},
          {"type": "assistant", "message": {"content": [
              {"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "cat " + read}}]}},
          {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b1", "content": "ok"}]}},
          {"type": "assistant", "message": {"content": [{"type": "text", "text": "fake claude reply"}]}},
          {"type": "result", "subtype": "success", "result": "fake claude reply", "num_turns": 2,
           "usage": {"input_tokens": 50, "output_tokens": 5}, "total_cost_usd": 0.01,
           "modelUsage": {"claude-opus-5-5": {"inputTokens": 50, "outputTokens": 5}}}]:
    print(json.dumps(e), flush=True)
'''

SECURITY = r'''
import json, os, pathlib, sys
args = sys.argv[1:]
pathlib.Path(os.environ["FAKE_SECURITY_LOG"]).write_text(json.dumps(args))
tok = os.environ.get("FAKE_KEYCHAIN_TOKEN")
if tok and args[:1] == ["find-generic-password"] and "-w" in args \
        and args[args.index("-s") + 1] == "mnemorph-claude-token":
    print(tok); sys.exit(0)
print("security: The specified item could not be found in the keychain.", file=sys.stderr)
sys.exit(44)
'''


class ReplayCase(unittest.TestCase):
    """A HOME with ~/.codex, a repository to replay at src/Mnemorph and fakes on PATH."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.home = base / "home"
        self.bin = base / "bin"
        self.bin.mkdir()
        for name, body in (("codex", CODEX), ("claude", CLAUDE), ("security", SECURITY)):
            f = self.bin / name
            f.write_text(f"#!{sys.executable}\n{body}")
            f.chmod(0o755)
        codex = self.home / ".codex"
        codex.mkdir(parents=True)
        (codex / "config.toml").write_text('model = "gpt-test"\nmodel_reasoning_effort = "low"\n\n'
                                           '[mcp_servers.docs]\ncommand = "docs"\n')
        (codex / "auth.json").write_text("{}")
        (codex / "AGENTS.md").write_text("Global Codex rule.\n")
        self.log = base / "fake-log.json"
        self.security_log = base / "security-log.json"
        self.out = base / "out"
        self.root = self.home / "src" / "Mnemorph"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CLAUDE", "ANTHROPIC", "CODEX", "GIT_"))}
        (base / "tmp").mkdir()
        self.env.update(HOME=str(self.home), PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", TMPDIR=str(base / "tmp"),
                        USER="replay-tester", FAKE_LOG=str(self.log),
                        FAKE_SECURITY_LOG=str(self.security_log),
                        GIT_AUTHOR_NAME="Replay tests", GIT_AUTHOR_EMAIL="replay@example.invalid",
                        GIT_COMMITTER_NAME="Replay tests", GIT_COMMITTER_EMAIL="replay@example.invalid",
                        CLAUDECODE="1", ANTHROPIC_BASE_URL="http://parent.invalid")
        self.write(self.root / "AGENTS.md", "Load Mnemorph.\n")
        self.write(self.root / ".gitignore", "/.mnemorph-local/\n")
        self.write(self.root / "src" / "x.md", "memory\n")
        self.write(self.root / "note.md", f"See {self.root}/src/x.md and {self.root}-Shared/y.md\n")
        for host, skills in (("codex", ("mnemorph",)), ("claude", ("mnemorph", "learn"))):
            for skill in skills:
                self.write(self.root / "integrations" / host / "skills" / skill / "SKILL.md", f"{skill}\n")
        self.base = self.commit(self.root, "base")
        self.write(self.root / "later.md", "written after the request\n")
        self.later = self.commit(self.root, "later")
        self.prompt = base / "prompt.txt"
        self.prompt.write_text(f"Please read {self.root}/src/x.md\n")

    def write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def commit(self, repo: Path, message: str) -> str:
        if not (repo / ".git").exists():
            subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=self.env)
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=self.env)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", message], check=True, env=self.env)
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, env=self.env,
                              text=True, capture_output=True).stdout.strip()

    def replay(self, *args, replay=REPLAY, **env) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(replay), *map(str, args)], text=True,
                              capture_output=True, env={**self.env, **env}, timeout=120)

    def run_args(self, *extra) -> list:
        return ["--root", self.root, "--commit", self.base, "--prompt-file", self.prompt,
                "--out", self.out, *extra]

    def fake_log(self) -> dict:
        return json.loads(self.log.read_text())

    def manifest(self) -> dict:
        return json.loads((self.out / "manifest.json").read_text())

    def ok(self, r: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r
