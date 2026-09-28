"""Check that Claude Code replays are sealed, keep ~/.claude out and handle the token safely.

A fake `claude` on PATH records its arguments, environment (the token only as a
hash) and config directory and writes stream-json events; a fake `security`
stands in for the Keychain."""

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_fakes import ReplayCase  # noqa: E402

ENV_TOKEN = "sk-ant-oat01-env-token-for-tests"
KEYCHAIN_TOKEN = "sk-ant-oat01-keychain-token-for-tests"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class ClaudeReplay(ReplayCase):
    def assert_token_hidden(self, r, token):
        self.assertNotIn(token, r.stdout + r.stderr)
        for f in self.out.rglob("*"):
            if f.is_file():
                self.assertNotIn(token, f.read_text(errors="ignore"), f.name)
        self.assertNotIn(token, " ".join(self.fake_log()["argv"]))

    def test_sealed_config_dir(self):
        r = self.ok(self.replay("claude", *self.run_args("--model", "opus", "--effort", "high"),
                                CLAUDE_CODE_OAUTH_TOKEN=ENV_TOKEN))
        log = self.fake_log()
        copy = Path(log["cwd"])
        # outside HOME: Claude Code reads .claude/CLAUDE.md in every folder above its cwd
        self.assertTrue(str(copy).startswith(str(Path(self.env["TMPDIR"]).resolve() / "mnemorph-replay")))
        self.assertFalse(str(copy).startswith(str(self.home)))
        self.assertEqual(Path(log["home_dir"]).parent, copy.parent)  # an empty HOME beside the copy
        self.assertEqual(log["home_entries"], [])
        self.assertNotEqual(Path(log["config_dir"]), self.home / ".claude")
        self.assertEqual(log["config"], {"CLAUDE.md": "file", "mnemorph": str(copy), "skills": "dir"})
        self.assertEqual(log["skills"], {s: str(copy / "integrations/claude/skills" / s)
                                         for s in ("learn", "mnemorph")})
        self.assertEqual(log["claude_md"], "Use the mnemorph skill on every task.\n")
        self.assertEqual(log["prompt"], f"Please read {copy}/src/x.md\n")
        self.assertEqual(log["note"], f"See {copy}/src/x.md and {self.root}-Shared/y.md\n")
        self.assertEqual(log["env"], {"ENABLE_CLAUDEAI_MCP_SERVERS": "false", "DISABLE_AUTOUPDATER": "1"})
        self.assertEqual(log["parent_vars"], [])
        argv = " ".join(log["argv"])
        for flag in ("-p", "--output-format stream-json", "--no-session-persistence",
                     "--strict-mcp-config", "--no-chrome", "--setting-sources user,project,local",
                     "--permission-mode acceptEdits", "--permission-prompts none",
                     "--model opus", "--effort high"):
            self.assertIn(flag, argv)
        self.assertNotIn("--mcp-config", argv)
        self.assertNotIn("dangerously", argv)
        self.assertNotIn("bypassPermissions", argv)
        self.assert_token_hidden(r, ENV_TOKEN)

    def test_outputs_and_manifest(self):
        self.ok(self.replay("claude", *self.run_args("--effort", "high"),
                            CLAUDE_CODE_OAUTH_TOKEN=ENV_TOKEN, FAKE_READ=str(self.root / "later.md")))
        self.assertEqual((self.out / "last.md").read_text(), "fake claude reply")
        bad = json.loads((self.out / "contaminated.json").read_text())
        self.assertEqual(bad, [{"path": str(self.root / "later.md"), "class": "post-dated"}])
        diff = (self.out / "changes.diff").read_text()
        self.assertIn("made-by-run.txt", diff)
        m = self.manifest()
        self.assertEqual((m["host"], m["subcommand"], m["model"], m["effort"]),
                         ("claude", "claude", "claude-opus-5-5", "high"))
        self.assertEqual(m["cli_version"], "9.9.9 (Claude Code)")
        self.assertEqual(m["commit"], self.base)
        self.assertEqual(m["usage"], {"input_tokens": 50, "output_tokens": 5})
        self.assertEqual(m["cost_usd"], 0.01)
        self.assertEqual(m["init"]["mcp_servers"], [])
        self.assertEqual(m["init"]["skills"], ["learn", "mnemorph"])
        self.assertNotIn("verbosity", m)

    def test_token_from_environment_reaches_only_the_child(self):
        self.ok(self.replay("claude", *self.run_args(), CLAUDE_CODE_OAUTH_TOKEN=ENV_TOKEN,
                            FAKE_KEYCHAIN_TOKEN=KEYCHAIN_TOKEN))
        self.assertEqual(self.fake_log()["token_sha"], sha(ENV_TOKEN))
        self.assertFalse(self.security_log.exists())

    def test_token_from_keychain(self):
        r = self.ok(self.replay("claude", *self.run_args(), FAKE_KEYCHAIN_TOKEN=KEYCHAIN_TOKEN))
        self.assertEqual(json.loads(self.security_log.read_text()),
                         ["find-generic-password", "-a", "replay-tester", "-s", "mnemorph-claude-token", "-w"])
        self.assertEqual(self.fake_log()["token_sha"], sha(KEYCHAIN_TOKEN))
        self.assert_token_hidden(r, KEYCHAIN_TOKEN)

    def test_missing_token_stops_before_the_run(self):
        r = self.replay("claude", *self.run_args())
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no token", r.stderr)
        self.assertFalse(self.log.exists())

    def test_dry_run_reads_no_token(self):
        r = self.ok(self.replay("claude", *self.run_args("--dry-run"), FAKE_KEYCHAIN_TOKEN=KEYCHAIN_TOKEN))
        self.assertIn("--strict-mcp-config", r.stdout)
        self.assertIn("auth: Keychain item mnemorph-claude-token", r.stdout)
        self.assertNotIn(KEYCHAIN_TOKEN, r.stdout)
        self.assertFalse(self.security_log.exists())
        self.assertFalse(self.log.exists())

    def test_bare_arm(self):
        self.ok(self.replay("claude", *self.run_args("--no-mnemorph"), CLAUDE_CODE_OAUTH_TOKEN=ENV_TOKEN))
        log = self.fake_log()
        self.assertEqual(log["config"], {})
        self.assertFalse(log["cwd_agents"])

    def test_bare_arm_with_global_instructions(self):
        extra = self.home / "simple.md"
        extra.write_text("Be autonomous.\n")
        self.ok(self.replay("claude", *self.run_args("--no-mnemorph", "--global-agents", extra),
                            CLAUDE_CODE_OAUTH_TOKEN=ENV_TOKEN))
        log = self.fake_log()
        self.assertEqual(log["config"], {"CLAUDE.md": "file"})
        self.assertEqual(log["claude_md"], "Be autonomous.\n")

    def test_project_run(self):
        project = self.home / "src" / "app"
        self.write(project / "main.py", "print('hi')\n")
        self.commit(project, "app")
        self.ok(self.replay("claude", *self.run_args("--project", project), CLAUDE_CODE_OAUTH_TOKEN=ENV_TOKEN))
        log = self.fake_log()
        self.assertEqual(Path(log["cwd"]).name, "app")
        self.assertEqual(Path(log["config"]["mnemorph"]).name, "Mnemorph")
        self.assertIn("made-by-run.txt", (self.out / "changes.diff").read_text())
        self.assertTrue((self.out / "memory.diff").exists())


if __name__ == "__main__":
    unittest.main()


class LiveReadTests(unittest.TestCase):
    def test_replays_cannot_read_the_live_repository_or_session_stores(self):
        import argparse, importlib.util
        spec = importlib.util.spec_from_file_location(
            "claude_replay", Path(__file__).resolve().parents[1] / "claude_replay.py")
        claude_replay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(claude_replay)
        a = argparse.Namespace(root=str(Path(__file__).resolve().parents[2]), model=None, effort=None)
        s = claude_replay.settings(a)
        deny = s["sandbox"]["filesystem"]["denyRead"]
        self.assertIn(str(Path(a.root).resolve()), deny)
        self.assertIn(str(Path.home() / ".claude" / "projects"), deny)
        self.assertTrue(all(r.startswith("Read(//") for r in s["permissions"]["deny"]))
        cmd = claude_replay.claude_command(a)
        self.assertIn("denyRead", cmd[cmd.index("--settings") + 1])
