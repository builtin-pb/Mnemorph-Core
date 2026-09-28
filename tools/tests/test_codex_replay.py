"""Check that Codex replays stay inside their copy and record what they ran.

A fake `codex` on PATH records its arguments, environment and sealed home and
writes events like `codex exec --json`."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_fakes import ReplayCase  # noqa: E402


class DryRun(ReplayCase):
    def test_command_disables_outside_access(self):
        out = self.ok(self.replay("codex", *self.run_args("--dry-run"))).stdout
        for flag in ("-s workspace-write", 'approval_policy="never"',
                     "--disable apps", "--disable plugins", "--disable computer_use",
                     "--disable browser_use", "--disable in_app_browser",
                     "exclude_slash_tmp=true", "exclude_tmpdir_env_var=true",
                     "mcp_servers.docs.enabled=false"):
            self.assertIn(flag, out)
        self.assertNotIn("--ephemeral", out)  # sealed runs keep sessions so subagents can spawn
        self.assertNotIn("app-server", out)  # not defined in config.toml
        self.assertNotIn("danger", out)
        self.assertFalse(self.out.exists())

    def test_failed_server_listing_still_turns_off_config_servers(self):
        out = self.ok(self.replay("codex", *self.run_args("--dry-run"), FAKE_MCP_LIST_FAILS="1")).stdout
        self.assertIn("mcp_servers.docs.enabled=false", out)
        self.assertNotIn("app-server", out)

    def test_model_effort_and_verbosity_flags(self):
        out = self.ok(self.replay("codex", *self.run_args(
            "--model", "gpt-6-sol", "--effort", "high", "--verbosity", "low", "--dry-run"))).stdout
        for flag in ("-m gpt-6-sol", 'model_reasoning_effort="high"', 'model_verbosity="low"'):
            self.assertIn(flag, out)


class SealedRun(ReplayCase):
    def test_run_is_sealed_to_its_copy(self):
        self.ok(self.replay("codex", *self.run_args()))
        log = self.fake_log()
        copy = Path(log["cwd"])
        self.assertNotEqual(copy, self.root)
        self.assertTrue(str(copy).startswith(str(self.home / ".cache" / "mnemorph-replay")))
        self.assertEqual(log["home"]["mnemorph"], str(copy))
        self.assertEqual(log["home"]["auth.json"], str(self.home / ".codex" / "auth.json"))
        self.assertEqual(log["home"]["config.toml"], "file")
        self.assertEqual(log["skills"], {"mnemorph": str(copy / "integrations/codex/skills/mnemorph")})
        self.assertEqual(log["agents_md"], "Global Codex rule.\n")
        self.assertTrue(log["cwd_agents"])
        # live paths point at the copy, but not a longer sibling path
        self.assertEqual(log["note"], f"See {copy}/src/x.md and {self.root}-Shared/y.md\n")
        self.assertEqual(log["prompt"], f"Please read {copy}/src/x.md\n")
        self.assertEqual(log["parent_vars"], [])
        self.assertFalse(copy.exists())  # removed without --keep

    def test_outputs_and_manifest(self):
        r = self.ok(self.replay("codex", *self.run_args("--effort", "medium"),
                                FAKE_READ=str(self.root / "later.md")))
        self.assertIn("1 contaminating", r.stdout)
        self.assertEqual((self.out / "last.md").read_text(), "fake codex reply\n")
        bad = json.loads((self.out / "contaminated.json").read_text())
        self.assertEqual(bad, [{"path": str(self.root / "later.md"), "class": "post-dated"}])
        diff = (self.out / "changes.diff").read_text()
        changed = {line.split(" b/")[-1] for line in diff.splitlines() if line.startswith("diff --git")}
        # the run's files, ignored state included; the seal's rewrite of note.md is setup
        self.assertEqual(changed, {"made-by-run.txt", ".mnemorph-local/run-note.md"})
        m = self.manifest()
        self.assertEqual((m["host"], m["subcommand"], m["model"], m["effort"]),
                         ("codex", "codex", "gpt-test", "medium"))
        self.assertNotIn("verbosity", m)
        self.assertEqual(m["cli_version"], "codex-cli 9.9.9")
        self.assertEqual(m["commit"], self.base)
        self.assertEqual(m["root"], str(self.root))
        self.assertEqual(m["flags"][:2], ["--root", str(self.root)])
        self.assertIn("--effort", m["flags"])
        self.assertEqual(m["usage"], {"input_tokens": 100, "cached_input_tokens": 40,
                                      "output_tokens": 7, "reasoning_output_tokens": 3})
        self.assertEqual(m["exit"], 0)
        self.assertEqual(m["command"][:2], ["codex", "exec"])

    def test_copied_state_is_setup_not_change(self):
        state = self.home / "state.md"
        state.write_text("working state\n")
        self.ok(self.replay("codex", *self.run_args("--copy", f"{state}=.mnemorph-local/state.md",
                                                    "--verbosity", "high")))
        self.assertNotIn("state.md", (self.out / "changes.diff").read_text())
        self.assertEqual(self.manifest()["verbosity"], "high")

    def test_bare_arm(self):
        extra = self.home / "simple.md"
        extra.write_text("Be autonomous.\n")
        with (self.home / ".codex" / "AGENTS.md").open("a") as f:
            f.write("Use the mnemorph skill on every task.\n")
        self.ok(self.replay("codex", *self.run_args("--no-mnemorph", "--global-agents", extra)))
        log = self.fake_log()
        self.assertNotIn("mnemorph", log["home"])
        self.assertEqual(log["skills"], {})
        self.assertFalse(log["cwd_agents"])
        self.assertEqual(log["agents_md"], "Global Codex rule.\n\nBe autonomous.\n")
        self.assertFalse(self.manifest()["mnemorph"])

    def test_project_run(self):
        project = self.home / "src" / "app"
        self.write(project / "main.py", "print('hi')\n")
        self.write(project / "node_modules" / "dep" / "index.js", "x\n")
        (project / ".gitignore").write_text("node_modules/\n")
        pcommit = self.commit(project, "app")
        self.prompt.write_text(f"Fix {project}/main.py\n")
        self.ok(self.replay("codex", *self.run_args("--project", project, "--project-dir", "node_modules")))
        log = self.fake_log()
        clone = Path(log["cwd"])
        self.assertEqual(clone.name, "app")
        self.assertEqual(log["prompt"], f"Fix {clone}/main.py\n")
        self.assertEqual(Path(log["home"]["mnemorph"]).name, "Mnemorph")
        self.assertIn("made-by-run.txt", (self.out / "changes.diff").read_text())
        self.assertNotIn("node_modules", (self.out / "changes.diff").read_text())
        self.assertEqual((self.out / "memory.diff").read_text(), "")
        self.assertEqual(self.manifest()["project"], {"path": str(project), "commit": pcommit})

    def test_no_seal_is_refused_with_bare_arm(self):
        r = self.replay("codex", *self.run_args("--no-mnemorph", "--no-seal"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("needs a sealed run", r.stderr)


class ForkRun(ReplayCase):
    def test_fork_keeps_tmp_closed_and_writes_manifest(self):
        session = self.home / "rollout.jsonl"
        lines = [{"type": "session_meta", "timestamp": "2099-01-01T00:00:00Z",
                  "payload": {"id": "orig", "cwd": "/live"}},
                 {"type": "response_item", "timestamp": "2099-01-01T00:00:01Z",
                  "payload": {"type": "message", "role": "user",
                              "content": [{"type": "input_text", "text": "first"}]}},
                 {"type": "response_item", "timestamp": "2099-01-01T00:00:02Z",
                  "payload": {"type": "message", "role": "user",
                              "content": [{"type": "input_text", "text": "second"}]}}]
        session.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        self.ok(self.replay("codex-fork", "--root", self.root, "--session", session, "--line", 3,
                            "--out", self.out, "--model", "gpt-6-astra"))
        log = self.fake_log()
        self.assertEqual(log["argv"][:2], ["exec", "fork"])
        joined = " ".join(log["argv"])
        for flag in ("exclude_slash_tmp=true", "exclude_tmpdir_env_var=true", 'approval_policy="never"',
                     "mcp_servers.docs.enabled=false", "-m gpt-6-astra"):
            self.assertIn(flag, joined)
        self.assertEqual(log["prompt"], "second")
        m = self.manifest()
        self.assertEqual((m["subcommand"], m["model"], m["commit"], m["line"]),
                         ("codex-fork", "gpt-6-astra", self.later, 3))
        self.assertEqual(m["usage"]["output_tokens"], 7)
        self.assertTrue((self.out / "replayed.json").exists())


if __name__ == "__main__":
    unittest.main()
