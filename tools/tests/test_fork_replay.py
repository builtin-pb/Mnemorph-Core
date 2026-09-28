"""Check the fork replays: a real session's earlier turns go along, the message is
found by line or record id, and the fork is sealed like the one-message replays.

Fake `codex` and `claude` commands record what they were given (the sealed
sessions directory, the resumed transcript) and write events."""

import hashlib
import json
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replay_fakes import ReplayCase  # noqa: E402
import claude_fork_replay  # noqa: E402

TOKEN = "sk-ant-oat01-fork-token-for-tests"


def ts(second: int) -> str:
    return f"2099-01-01T00:00:{second:02d}.000Z"


class CodexFork(ReplayCase):
    def rollout(self, name: str, meta: dict, records: list) -> Path:
        path = self.home / ".codex" / "sessions" / "2099" / "01" / "01" / f"rollout-2099-01-01T00-00-00-{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [{"timestamp": ts(0), "type": "session_meta", "payload": meta}] + records
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        return path

    @staticmethod
    def message(text: str, second: int) -> dict:
        return {"timestamp": ts(second), "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}

    @staticmethod
    def event(mid: str, text: str, second: int) -> dict:
        return {"timestamp": ts(second), "type": "event_msg",
                "payload": {"type": "item_completed", "item": {"type": "UserMessage", "id": mid,
                                                               "content": [{"type": "text", "text": text}]}}}

    def paginated(self) -> dict:
        """A thread T forked from P and paginated into T, T_S1, T_S2 (the target) and T_S3."""
        ids = {k: str(uuid.uuid4()) for k in ("P", "T", "S1", "S2", "S3", "U", "msg")}
        base = lambda ref: {"thread_id": ids[ref], "end_ordinal_exclusive": 2, "end_byte_offset": 10}
        self.rollout(ids["P"], {"id": ids["P"], "history_mode": "paginated", "history_base": None},
                     [self.message("parent", 1)])
        self.rollout(ids["T"], {"id": ids["T"], "forked_from_id": ids["P"], "history_base": base("P")},
                     [self.message("base", 2)])
        self.rollout(f"{ids['T']}_{ids['S1']}", {"id": ids["T"], "forked_from_id": ids["P"],
                                                   "history_base": base("T")}, [self.message("one", 3)])
        target = self.rollout(f"{ids['T']}_{ids['S2']}", {"id": ids["T"], "forked_from_id": ids["P"],
                                                            "history_base": base("S1"), "cwd": "/live"},
                              [{"timestamp": ts(4), "type": "turn_context", "payload": {"cwd": str(self.root)}},
                               self.message("earlier", 4), self.event(str(uuid.uuid4()), "earlier", 4),
                               self.message("replay me", 5), self.event(ids["msg"], "replay me", 5)])
        self.rollout(f"{ids['T']}_{ids['S3']}", {"id": ids["T"], "history_base": base("S2")},
                     [self.message("later page", 6)])
        self.rollout(ids["U"], {"id": ids["U"]}, [self.message("unrelated", 1)])
        ids["target"] = target
        return ids

    def test_follows_pages_and_forks(self):
        ids = self.paginated()
        self.ok(self.replay("codex-fork", "--root", self.root, "--session", ids["target"], "--line", 5,
                            "--out", self.out))
        log = self.fake_log()
        names = [Path(s).name for s in log["sessions"]]
        for key in ("P", "T", "S1"):
            self.assertEqual(sum(n.endswith(f"{ids[key]}.jsonl") for n in names), 1, key)
        for key in ("S2", "S3", "U"):  # the target is rewritten as the fork; nothing else comes along
            self.assertFalse(any(n.endswith(f"{ids[key]}.jsonl") for n in names), key)
        self.assertEqual(len(names), 4)
        self.assertEqual(log["prompt"], "replay me")
        self.assertEqual(json.loads((self.out / "replayed.json").read_text())["history_pages"].__len__(), 3)

    def test_record_line_and_id_resolve_to_the_message(self):
        ids = self.paginated()
        self.ok(self.replay("codex-fork", "--root", self.root, "--session", ids["target"], "--line", 6,
                            "--out", self.out))  # the record's line: the UserMessage event
        self.assertEqual((self.fake_log()["prompt"], self.manifest()["line"]), ("replay me", 5))
        display = "~/" + str(ids["target"].relative_to(self.home))
        self.write(self.root / "src" / "record" / "2099-01.jsonl",
                   json.dumps({"id": f"codex:{ids['msg']}", "source": display, "line": 6}) + "\n")
        out = self.out.parent / "by-record"
        self.ok(self.replay("codex-fork", "--root", self.root, "--record", f"codex:{ids['msg']}", "--out", out))
        m = json.loads((out / "manifest.json").read_text())
        self.assertEqual((m["line"], m["record"], m["session"]), (5, f"codex:{ids['msg']}", str(ids["target"])))
        r = self.replay("codex-fork", "--root", self.root, "--record", "claude:abc", "--out", out)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a codex record", r.stderr)

    def test_goal_record(self):
        objective = "Finish the task"
        goal = {"timestamp": ts(3), "type": "event_msg", "payload": {"type": "thread_goal_updated",
                "goal": {"objective": objective, "createdAt": 4102444800}}}
        session = self.rollout(str(uuid.uuid4()), {"id": "g"}, [self.message("hi", 1), goal,
                                                                  {"timestamp": ts(3), "type": "turn_context",
                                                                   "payload": {"cwd": "/elsewhere"}},
                                                                  self.message("Goal: finish the task", 4)])
        digest = hashlib.sha256(objective.encode()).hexdigest()[:12]
        self.ok(self.replay("codex-fork", "--root", self.root, "--session", session,
                            "--record", f"goal:4102444800:{digest}", "--out", self.out))
        log = self.fake_log()
        self.assertEqual(log["prompt"], "Goal: finish the task")
        self.assertEqual(Path(log["cwd"]).name, "elsewhere")  # ran outside --root: an empty folder of that name
        self.assertEqual(self.manifest()["line"], 5)

    def test_works_in_the_patched_copy(self):
        ids = self.paginated()
        patch = self.home / "change.patch"
        patch.write_text("diff --git a/patched.md b/patched.md\nnew file mode 100644\n--- /dev/null\n"
                         "+++ b/patched.md\n@@ -0,0 +1 @@\n+patched\n")
        self.ok(self.replay("codex-fork", "--root", self.root, "--session", ids["target"], "--line", 5,
                            "--patch", patch, "--out", self.out))
        log = self.fake_log()
        copy = Path(log["cwd"])
        self.assertEqual(copy.name, "Mnemorph")
        self.assertIn("patched.md", log["cwd_files"])
        self.assertEqual(log["home"]["mnemorph"], str(copy))
        self.assertIn("made-by-run.txt", (self.out / "changes.diff").read_text())
        self.assertIn("exclude_slash_tmp=true", " ".join(log["argv"]))


class ClaudeFork(ReplayCase):
    def setUp(self):
        super().setUp()
        self.sid = str(uuid.uuid4())
        live_key = claude_fork_replay.project_key(self.root)
        self.live = self.home / ".claude" / "projects" / live_key
        (self.live / self.sid / "tool-results").mkdir(parents=True)
        (self.live / self.sid / "tool-results" / "r1.txt").write_text("long output\n")
        self.uuids = [str(uuid.uuid4()) for _ in range(8)]
        u = self.uuids
        base = {"sessionId": self.sid, "cwd": str(self.root), "isSidechain": False, "version": "9.9.9"}
        records = [
            {"type": "queue-operation", "operation": "enqueue", "sessionId": self.sid},
            {**base, "type": "user", "uuid": u[0], "parentUuid": None, "timestamp": ts(1),
             "message": {"role": "user", "content": f"Look at {self.root}/src/x.md"}},
            {**base, "type": "assistant", "uuid": u[1], "parentUuid": u[0], "timestamp": ts(2), "effort": "xhigh",
             "message": {"model": "claude-opus-5-5", "role": "assistant", "content": [{"type": "text", "text": "ok"}],
                         "usage": {"input_tokens": 1000, "cache_read_input_tokens": 250000}}},
            {**base, "type": "user", "uuid": u[2], "parentUuid": u[1], "timestamp": ts(3),
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                         "content": f"saved to {self.live / self.sid / 'tool-results' / 'r1.txt'}"}]}},
            {**base, "type": "attachment", "uuid": u[3], "parentUuid": u[2], "timestamp": ts(4),
             "attachment": {"type": "queued_command", "prompt": "the queued words<system-reminder>x</system-reminder>",
                            "origin": {"kind": "human"}}},
            {**base, "type": "user", "uuid": u[4], "parentUuid": u[3], "timestamp": ts(5),
             "message": {"role": "user", "content": [{"type": "text", "text":
                         "<command-name>/taste</command-name>\n<command-args>check it</command-args>"}]}},
            {**base, "type": "user", "uuid": u[5], "parentUuid": u[4], "timestamp": ts(6),
             "message": {"role": "user", "content": "later words"}},
        ]
        self.session = self.live / f"{self.sid}.jsonl"
        self.session.write_text("\n".join(json.dumps(x) for x in records) + "\n")

    def fork(self, *extra, **env):
        return self.replay("claude-fork", "--root", self.root, *extra, "--out", self.out,
                           **{"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, **env})

    def test_queued_command_resumes_the_sealed_transcript(self):
        r = self.ok(self.fork("--session", self.session, "--line", 5))
        log = self.fake_log()
        copy = Path(log["cwd"])
        self.assertEqual(copy.name, "Mnemorph")
        self.assertEqual(log["prompt"], "the queued words")
        argv = " ".join(log["argv"])
        for flag in (f"--resume {self.sid}", "--fork-session", "--model claude-opus-5-5[1m]", "--effort xhigh",
                     "--strict-mcp-config", "--setting-sources user,project,local", "--permission-prompts none"):
            self.assertIn(flag, argv)
        key = claude_fork_replay.project_key(copy)
        self.assertEqual(sorted(log["projects"][key]), [f"{self.sid}.jsonl", f"{self.sid}/tool-results/r1.txt"])
        transcript = log["transcript"]
        self.assertEqual(len(transcript.splitlines()), 4)
        self.assertNotIn(f"{self.root}/", transcript)
        self.assertIn(f"{copy}/src/x.md", transcript)
        self.assertIn(f'"cwd": "{copy}"', transcript)
        self.assertIn(f"{log['config_dir']}/projects/{key}/{self.sid}/tool-results/r1.txt", transcript)
        self.assertEqual(log["token_sha"], hashlib.sha256(TOKEN.encode()).hexdigest())
        self.assertNotIn(TOKEN, r.stdout + r.stderr + "".join(f.read_text() for f in self.out.iterdir()))
        m = self.manifest()
        self.assertEqual((m["subcommand"], m["model"], m["effort"], m["line"]),
                         ("claude-fork", "claude-opus-5-5[1m]", "xhigh", 5))
        self.assertEqual(json.loads((self.out / "replayed.json").read_text())["kind"], "attachment")
        self.assertIn("made-by-run.txt", (self.out / "changes.diff").read_text())

    def test_record_id_and_slash_command(self):
        self.write(self.root / "src" / "record" / "2099-01.jsonl", json.dumps(
            {"id": f"claude:{self.uuids[5]}", "source": str(self.session), "line": 7}) + "\n")
        r = self.ok(self.fork("--record", f"claude:{self.uuids[5]}", "--model", "opus"))
        self.assertEqual(self.fake_log()["prompt"], "later words")
        self.assertIn("without a 1M window", r.stderr)
        self.assertEqual(self.manifest()["record"], f"claude:{self.uuids[5]}")
        self.out = self.out.parent / "command"
        self.ok(self.fork("--session", self.session, "--line", 6, "--append", "Restated rule."))
        self.assertEqual(self.fake_log()["prompt"], "/taste check it\n\nRestated rule.")

    def test_lines_that_are_not_messages(self):
        for line in (3, 4, 99):  # assistant, tool result, outside the file
            r = self.fork("--session", self.session, "--line", line)
            self.assertNotEqual(r.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_session_outside_root_works_in_an_empty_directory(self):
        scratch = "/Users/someone/Library/scratch-workspace"
        self.session.write_text(self.session.read_text().replace(str(self.root), scratch))
        self.ok(self.fork("--session", self.session, "--line", 7))
        log = self.fake_log()
        self.assertEqual(Path(log["cwd"]).name, "scratch-workspace")
        self.assertIn(f'"cwd": "{log["cwd"]}"', log["transcript"])
        self.assertNotIn(scratch, log["transcript"])

    def test_dry_run_reads_no_token(self):
        r = self.ok(self.replay("claude-fork", "--root", self.root, "--session", self.session, "--line", 7,
                                "--out", self.out, "--dry-run", FAKE_KEYCHAIN_TOKEN=TOKEN))
        self.assertIn(f"--resume {self.sid} --fork-session", r.stdout)
        self.assertFalse(self.security_log.exists())
        self.assertFalse(self.out.exists())

    def test_long_project_key_matches_claude_code(self):
        path = "/Users/x/" + "very-long-directory-name-é-" * 9  # value computed with Claude Code's JavaScript
        key = claude_fork_replay.project_key(Path(path))
        self.assertTrue(key.endswith("-qixuox"))
        self.assertEqual(len(key), 207)
        self.assertEqual(claude_fork_replay.project_key(Path("/a b/c.d")), "-a-b-c-d")


if __name__ == "__main__":
    unittest.main()


class ProviderTests(unittest.TestCase):
    def test_a_fork_runs_on_the_current_provider_not_the_recorded_one(self):
        import importlib.util, json as _json
        spec = importlib.util.spec_from_file_location(
            "codex_fork_replay", Path(__file__).resolve().parents[1] / "codex_fork_replay.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        line = _json.dumps({"type": "session_meta", "payload": {"id": "x", "model_provider": "crs"}})
        self.assertEqual(_json.loads(m.with_provider(line, "openai"))["payload"]["model_provider"], "openai")
        other = _json.dumps({"type": "event_msg", "payload": {}})
        self.assertEqual(m.with_provider(other, "openai"), other)
