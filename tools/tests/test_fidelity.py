"""Check the seal workarounds that give a replay what the original agent had
(a local origin, the local state of the time, the sessions the turn names,
plainly named folders), the fidelity digest, the model-judged verdicts and the
orientation evidence. Fake `codex` and `claude` commands record what they saw."""

import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replay_fakes import ReplayCase  # noqa: E402
import replay_check  # noqa: E402

TOKEN = "sk-ant-oat01-fidelity-token-for-tests"
BEFORE, AFTER = "2020-01-01T00:00:00.000Z", "2099-01-01T00:00:00.000Z"


class Workarounds(ReplayCase):
    def setUp(self):
        super().setUp()
        self.cutoff = int(subprocess.run(["git", "-C", str(self.root), "log", "-1", "--format=%ct", self.base],
                                         text=True, capture_output=True).stdout)
        local = self.root / ".mnemorph-local"
        self.write(local / "old.md", "then\n")
        self.write(local / "sealed" / "kept.md", "then\n")
        self.write(local / "new.md", "written after the request\n")
        for name, when in (("old.md", -100), ("sealed/kept.md", -100), ("new.md", 100)):
            os.utime(local / name, (self.cutoff + when, self.cutoff + when))
        (local / "sealed").chmod(0o555)  # carried state keeps its modes; cleanup must cope
        self.addCleanup((local / "sealed").chmod, 0o755)
        self.claude_id, self.codex_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.claude_session = self.home / ".claude" / "projects" / "-live" / f"{self.claude_id}.jsonl"
        self.codex_session = (self.home / ".codex" / "sessions" / "2020" / "01" / "01"
                              / f"rollout-2020-01-01T00-00-00-{self.codex_id}.jsonl")
        for path, meta in ((self.claude_session, {"type": "user"}), (self.codex_session, {"type": "session_meta"})):
            self.write(path, "\n".join(json.dumps({**meta, "timestamp": ts, "text": word})
                                       for ts, word in ((BEFORE, "then"), (AFTER, "later"))) + "\n")
        self.prompt.write_text(f"Compare with {self.claude_session} and ask session {self.codex_id}.\n")

    def run_host(self, host, *extra):
        env = {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN} if host == "claude" else {}
        return self.ok(self.replay(host, *self.run_args(*extra), **env))

    def test_origin_is_a_local_clone_at_the_commit(self):
        self.run_host("codex")
        log = self.fake_log()
        self.assertRegex(log["remote"], r"origin\t.*/remote/Mnemorph\.git \(push\)")
        self.assertEqual(log["origin_main"], self.base)
        self.out = self.out.parent / "no-origin"
        self.run_host("codex", "--no-origin")
        self.assertEqual(self.fake_log()["remote"], "")

    def test_local_state_of_the_time(self):
        self.run_host("codex")
        self.assertEqual(self.fake_log()["local_files"], ["old.md", "sealed/kept.md"])
        diff = (self.out / "changes.diff").read_text()
        self.assertIn(".mnemorph-local/run-note.md", diff)
        self.assertNotIn("old.md", diff)
        self.assertEqual(self.manifest()["fidelity"]["local_state_files"], 2)
        self.assertEqual(list((self.home / ".cache" / "mnemorph-replay").iterdir()), [])  # read-only parts removed
        self.out = self.out.parent / "without"
        self.run_host("codex", "--no-local-state")
        self.assertIsNone(self.fake_log()["local_files"])

    def test_sessions_cut_at_the_request_codex(self):
        self.run_host("codex")
        log = self.fake_log()
        codex = log["sealed_codex"]["2020/01/01/" + self.codex_session.name]
        self.assertIn('"then"', codex)
        self.assertNotIn('"later"', codex)
        claude = log["sealed_claude"][f"-live/{self.claude_id}.jsonl"]
        self.assertNotIn('"later"', claude)
        self.assertNotIn(str(self.claude_session), log["prompt"])  # repointed to the sealed store
        self.assertEqual(len(self.manifest()["fidelity"]["sessions"]), 2)
        self.out = self.out.parent / "without"
        self.run_host("codex", "--no-sessions")
        self.assertEqual(self.fake_log()["sealed_claude"], None)

    def test_sessions_cut_at_the_request_claude(self):
        self.run_host("claude")
        log = self.fake_log()
        self.assertIn('"then"', log["sealed_codex"]["2020/01/01/" + self.codex_session.name])
        self.assertNotIn('"later"', log["sealed_claude"][f"-live/{self.claude_id}.jsonl"])
        self.assertIn(f"-live/{self.claude_id}.jsonl", log["home_claude_projects"])  # ~/.claude/projects works
        self.assertNotIn("CLAUDE.md", log["home_claude_projects"])
        self.assertIn(f"{log['config_dir']}/projects/-live/{self.claude_id}.jsonl", log["prompt"])

    def test_plain_folder_names(self):
        self.run_host("codex")
        self.assertTrue(self.fake_log()["cwd"].endswith("/mnemorph-replay/codex/Mnemorph"))
        held = self.home / ".cache" / "mnemorph-replay" / "codex"
        held.mkdir(parents=True)
        (held / ".lock").write_text(str(os.getpid()))  # a live run holds it
        self.run_host("codex")
        self.assertTrue(self.fake_log()["cwd"].endswith("/mnemorph-replay/codex-2/Mnemorph"))
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                              text=True, capture_output=True).stdout.strip()
        (held / ".lock").write_text(dead)  # an abandoned one is reused
        self.run_host("codex")
        self.assertTrue(self.fake_log()["cwd"].endswith("/mnemorph-replay/codex/Mnemorph"))
        self.run_host("codex", "--no-plain-paths")
        self.assertRegex(self.fake_log()["cwd"], r"/mnemorph-replay/codex-[a-z0-9_]{6,}/Mnemorph$")
        self.run_host("claude")
        self.assertTrue(self.fake_log()["cwd"].endswith("/mnemorph-replay/claude/Mnemorph"))

    def test_digest_is_saved_without_a_verdict(self):
        self.run_host("codex")
        check = json.loads((self.out / "check.json").read_text())
        self.assertNotIn("verdict", check)
        kinds = {(d["kind"], d["covered_by"]) for d in check["missing"] + check["covered"]}
        self.assertIn(("session", "sessions"), kinds)
        self.assertIn(("live-session", None), kinds)
        self.assertEqual(self.manifest()["fidelity"]["missing"], ["live-session"])


class Digest(ReplayCase):
    """A Claude session whose turn asks a live Codex session."""

    def setUp(self):
        super().setUp()
        self.cid = str(uuid.uuid4())
        rollout = self.home / ".codex" / "sessions" / "2099" / "01" / "01" / f"rollout-2099-01-01T00-00-00-{self.cid}.jsonl"
        self.write(rollout, json.dumps({"type": "session_meta", "timestamp": BEFORE}) + "\n")
        sid = str(uuid.uuid4())
        base = {"sessionId": sid, "cwd": str(self.root)}
        use = lambda name, **inp: {**base, "type": "assistant", "timestamp": AFTER, "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": str(uuid.uuid4()), "name": name, "input": inp}]}}
        records = [{**base, "type": "user", "uuid": "u1", "timestamp": "2099-01-01T00:00:01Z",
                    "message": {"role": "user", "content": "maybe ask it"}},
                   use("Bash", command=f"which codex; codex exec resume {self.cid} 'what went wrong?'"),
                   use("Read", file_path=str(rollout)),
                   use("WebFetch", url="https://example.com"),
                   use("mcp__slack__post", text="hi"),
                   use("Bash", command="cat /private/tmp/claude-501/x/scratchpad/notes.md"),
                   use("SendMessage", to="guard", message="cap delegates at 6"),
                   use("Bash", command="git push origin main"),
                   {**base, "type": "user", "uuid": "u2", "timestamp": AFTER, "message": {"role": "user", "content": "next"}}]
        self.session = self.home / ".claude" / "projects" / "-live" / f"{sid}.jsonl"
        self.write(self.session, "\n".join(json.dumps(x) for x in records) + "\n")

    def test_digest_names_what_is_missing_and_what_covers_it(self):
        d, turn = replay_check.digest_fork(self.session, 1, self.root, "claude", self.home)
        self.assertNotIn("verdict", d)
        self.assertEqual(d["request"], "maybe ask it")
        self.assertEqual(d["original_turn"]["calls"], 7)
        missing = {x["kind"] for x in d["missing"]}
        self.assertTrue({"cli", "web", "mcp", "temp-path", "live-session"} <= missing, missing)
        covered = {(x["kind"], x["covered_by"]) for x in d["covered"]}
        self.assertIn(("session", "sessions"), covered)
        self.assertIn(("git-remote", "origin"), covered)
        self.assertEqual(len(turn.calls), 7)

    def test_cases_digests_packets_and_model_verdicts(self):
        cases = self.home / "cases"
        cases.mkdir()
        (cases / "GEN.json").write_text(json.dumps({"positive": [{"host": "claude", "session_file": str(self.session),
                                                                  "line": 1}], "negative": []}))
        r = self.ok(self.replay("check", "--root", self.root, "--cases", cases / "GEN.json"))
        self.assertIn("GEN-positive-0", r.stdout)
        digests = json.loads((cases / "digests.json").read_text())
        self.assertEqual(digests["cases"][0]["id"], "GEN-positive-0")
        packet = (cases / "judge-packets" / "GEN-positive-0.md").read_text()
        self.assertIn("replay_judge.md", packet)
        self.assertFalse((cases / "verdicts.json").exists())
        judged = self.home / "judged.json"
        judged.write_text(json.dumps([{"id": "GEN-positive-0", "verdict": "unusable",
                                       "reasons": "It asks a live Codex session.", "would_help": ""}]))
        self.ok(self.replay("check", "--record-verdicts", judged, "--cases-dir", cases, "--judge", "test judge"))
        v = json.loads((cases / "verdicts.json").read_text())
        self.assertEqual((v["judged_by"], v["counts"]["unusable"], v["cases"]["GEN-positive-0"]["judge"]),
                         ("model", 1, "test judge"))
        judged.write_text(json.dumps([{"id": "GEN-positive-0", "verdict": "fine", "reasons": "x"}]))
        self.assertNotEqual(self.replay("check", "--record-verdicts", judged, "--cases-dir", cases,
                                        "--judge", "j").returncode, 0)

    def test_fork_dry_run_prints_the_digest_and_workarounds(self):
        r = self.ok(self.replay("claude-fork", "--root", self.root, "--session", self.session, "--line", 1,
                                "--out", self.out, "--dry-run"))
        self.assertIn("digest:", r.stdout)
        self.assertIn("workarounds: origin on; local-state on; sessions on (1 referenced)", r.stdout)

    def test_orientation_is_evidence(self):
        events = self.home / "events.jsonl"
        steps = ["git remote -v", "pwd; ls ~", "cat src/x.md"]
        events.write_text("\n".join(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": c}}]}}) for c in steps) + "\n")
        o = replay_check.orientation([("Bash", "cat src/x.md")], events)
        self.assertEqual((o["replay_orienting"], o["original_orienting"]), (2, 0))
        self.assertIn("not a gate", o["note"])


if __name__ == "__main__":
    unittest.main()
