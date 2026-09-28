"""Exercise sessions.py (where a session's effort went) against synthetic Claude Code and Codex transcripts."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("sessions_tool", Path(__file__).parents[1] / "sessions.py")
sessions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sessions)


def jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def c_user(time, content, **extra):
    return {"type": "user", "timestamp": time, "cwd": "/Users/u/project", "entrypoint": "claude-desktop",
            "message": {"role": "user", "content": content}, **extra}


def c_tool(time, mid, name, tool_input, usage=None):
    return {"type": "assistant", "timestamp": time,
            "message": {"id": mid, "model": "claude-opus-5-5", "usage": usage or {},
                        "content": [{"type": "tool_use", "id": "t-" + mid, "name": name, "input": tool_input}]}}


def c_say(time, mid, text):
    return {"type": "assistant", "timestamp": time,
            "message": {"id": mid, "model": "claude-opus-5-5", "content": [{"type": "text", "text": text}]}}


def c_result(time, tool_id, text, error=False):
    return c_user(time, [{"type": "tool_result", "tool_use_id": tool_id, "content": text, "is_error": error}])


def x_meta(thread, time, **extra):
    return {"timestamp": time, "type": "session_meta",
            "payload": {"id": thread, "cwd": "/Users/u/project", "source": "vscode", "thread_source": "user", **extra}}


def x_user(time, text):
    return {"timestamp": time, "type": "event_msg",
            "payload": {"type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": text}]}}}


def x_call(time, cmd):
    return {"timestamp": time, "type": "response_item",
            "payload": {"type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": cmd})}}


def x_output(time, text):
    return {"timestamp": time, "type": "response_item", "payload": {"type": "function_call_output", "output": text}}


class SessionsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.claude_home, self.codex_home = root / "claude", root / "codex"
        self.env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.claude_home),
                                                "CODEX_HOME": str(self.codex_home)})
        self.env.start()
        project = self.claude_home / "projects" / "-Users-u-project"
        jsonl(project / "aaaa1111.jsonl", [
            c_user("2026-09-26T10:00:00Z", "Where is the routine setting?"),
            c_tool("2026-09-26T10:00:05Z", "m1", "Bash", {"command": "grep -r mode app.asar"},
                   {"input_tokens": 100, "cache_read_input_tokens": 1000, "output_tokens": 50}),
            c_result("2026-09-26T10:00:06Z", "t-m1", "Exit code 1", error=True),
            c_tool("2026-09-26T10:00:10Z", "m2", "Bash", {"command": "grep -r mode app.asar"},
                   {"input_tokens": 10, "output_tokens": 5}),
            c_tool("2026-09-26T10:01:00Z", "m3", "Bash", {"command": "sleep 900; ls", "run_in_background": True}),
            c_tool("2026-09-26T10:40:00Z", "m4", "Read", {"file_path": "/Users/u/project/notes.md"}),
            {"type": "attachment", "timestamp": "2026-09-26T10:41:00Z",
             "attachment": {"type": "queued_command", "prompt": "also check effort", "origin": {"kind": "human"}}},
            c_user("2026-09-26T10:42:00Z", "<command-name>/compact</command-name>"),
            c_say("2026-09-26T10:42:30Z", "m5", "The setting is permissions.defaultMode. " + "Detail. " * 60),
        ])
        jsonl(project / "aaaa1111" / "subagents" / "agent-x.jsonl", [
            c_tool("2026-09-26T10:05:00Z", "s1", "Bash", {"command": "ls"}, {"output_tokens": 7}),
        ])
        jsonl(project / "bbbb2222.jsonl", [
            {**c_user("2026-09-26T11:00:00Z", "replayed task"), "entrypoint": "sdk-cli"},
            c_tool("2026-09-26T11:00:01Z", "r1", "Bash", {"command": "ls"}),
        ])
        day = self.codex_home / "sessions" / "2026" / "09" / "26"
        jsonl(day / "rollout-2026-09-26T12-00-00-cccc3333-0000-0000-0000-000000000000.jsonl", [
            x_meta("cccc3333-0000-0000-0000-000000000000", "2026-09-26T12:00:00Z"),
            {"timestamp": "2026-09-26T12:00:00Z", "type": "turn_context", "payload": {"model": "gpt-6-astra"}},
            x_user("2026-09-26T12:00:01Z", "Is everything green?"),
            x_call("2026-09-26T12:00:02Z", "npm test"),
            x_output("2026-09-26T12:00:30Z", "Process exited with code 1"),
            x_call("2026-09-26T12:01:00Z", "npm test"),
            {"timestamp": "2026-09-26T12:01:10Z", "type": "response_item", "payload": {"type": "custom_tool_call_output",
             "output": [{"type": "input_text", "text": "Script completed"},
                        {"type": "input_text", "text": json.dumps({"exit_code": 127, "output": "zsh:1: command not found: timeout"})}]}},
            {"timestamp": "2026-09-26T12:01:30Z", "type": "event_msg", "payload": {"type": "item_completed",
             "item": {"type": "AgentMessage", "content": [{"type": "Text", "text": "One test fails."}]}}},
            {"timestamp": "2026-09-26T12:02:00Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {"total_token_usage": {"total_tokens": 4321}}}},
        ])
        jsonl(day / "rollout-2026-09-26T14-00-00-eeee5555-0000-0000-0000-000000000000.jsonl", [
            x_meta("eeee5555-0000-0000-0000-000000000000", "2026-09-26T14:00:00Z"),
            x_user("2026-09-26T14:00:01Z", "an archived chat"),
            x_call("2026-09-26T14:00:02Z", "ls"),
        ])
        state = sqlite3.connect(self.codex_home / "state_5.sqlite")
        state.execute("create table threads (id text, archived integer)")
        state.execute("insert into threads values ('eeee5555-0000-0000-0000-000000000000', 1)")
        state.commit()
        state.close()
        jsonl(day / "rollout-2026-09-26T13-00-00-dddd4444-0000-0000-0000-000000000000.jsonl", [
            x_meta("dddd4444-0000-0000-0000-000000000000", "2026-09-26T13:00:00Z", source="exec"),
            x_call("2026-09-26T13:00:02Z", "ls"),
        ])

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def run_main(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sessions.main(list(argv)), 0)
        return out.getvalue()

    def test_claude_summary_counts_effort_and_subagents(self):
        s = sessions.find("aaaa1111")
        row = s.summary()
        self.assertEqual(row["users"], 2)  # typed message and mid-turn queued message; the /compact wrapper is not one
        self.assertEqual(row["calls"], 4)
        self.assertEqual(row["failed"], 1)
        self.assertEqual(row["repeated"], 1)
        self.assertEqual((row["subagents"], row["subagent_calls"]), (1, 1))
        self.assertEqual(row["active_min"], 14)  # 1 + 10 (the 39-minute gap, capped as idle) + 1 + 1.5, rounded
        self.assertEqual(s.tokens() + sum(c.tokens() for c in s.children), 1172)

    def test_timeline_marks_background_commands_and_gaps(self):
        out = self.run_main("timeline", "aaaa1111")
        self.assertIn("Bash [bg]: sleep 900; ls", out)
        self.assertIn("(+39m)", out)
        self.assertIn("FAIL Exit code 1", out)
        self.assertIn("USER also check effort", out)
        self.assertNotIn("compact", out.split("\n", 1)[1].split('{"host"')[0])
        self.assertIn("SAY The setting is permissions.defaultMode.", out)
        self.assertNotIn("Detail. " * 30, out)
        self.assertIn("Detail. " * 30, self.run_main("timeline", "aaaa1111", "--full"))

    def test_failures_group_across_sessions_and_subagents(self):
        project = self.claude_home / "projects" / "-Users-u-project"
        for sid, where in (("gggg7777", "main"), ("hhhh8888", "sub")):
            rows = [c_user("2026-09-26T16:00:00Z", "run it"),
                    c_tool("2026-09-26T16:00:01Z", sid + "t", "Bash", {"command": "timeout 60 make"}),
                    c_result("2026-09-26T16:00:02Z", "t-" + sid + "t",
                             f"Exit code 127\n(eval):1: command not found: timeout in /Users/u/{sid}", error=True)]
            if where == "main":
                jsonl(project / f"{sid}.jsonl", rows)
            else:
                jsonl(project / f"{sid}.jsonl", rows[:1])
                jsonl(project / sid / "subagents" / "agent-y.jsonl", rows[1:])
        self.assertIn("FAIL [subagent] Exit code 127", self.run_main("timeline", "hhhh8888"))
        out = self.run_main("failures", "--since", "2026-09-26T00:00:00Z")
        self.assertIn("2 sessions    2 failures (1 in subagents)", out)
        self.assertIn("command not found: timeout", out)
        before = self.run_main("failures", "--since", "2026-09-26T00:00:00Z", "--until", "2026-09-26T16:00:01Z")
        self.assertNotIn("timeout", before)
        self.assertNotIn("gggg7777", out.split("|")[1])  # paths blanked in the signature
        self.assertNotIn("Exit code 1", self.run_main("failures", "--since", "2026-09-26T00:00:00Z", "--min-sessions", "1"))

    def test_silent_signals(self):
        project = self.claude_home / "projects" / "-Users-u-project"
        jsonl(project / "iiii9999.jsonl", [
            c_user("2026-09-26T18:00:00Z", "run the batch overnight and report"),
            c_tool("2026-09-26T18:00:05Z", "p1", "Bash", {"command": "sleep 600; ls out"}),
            c_tool("2026-09-26T18:10:05Z", "p2", "Bash", {"command": "sleep 900", "run_in_background": True}),
            c_user("2026-09-26T19:00:00Z", "well?"),
        ])
        row = sessions.find("iiii9999").summary()
        self.assertEqual((row["nudges"], row["polls"]), (1, 1))  # the background sleep holds no turn
        self.assertIn("nudges=1 polls=1", self.run_main("rank", "--since", "2026-09-26T17:00:00Z", "--min-calls", "1"))

    def test_rank_leaves_out_another_instance(self):
        other = Path(self.dir.name) / "Shared"
        (other / "src" / "core").mkdir(parents=True)
        (other / "src" / "core" / "__entry__.md").write_text("# Core\n")
        project = self.claude_home / "projects" / "-shared"
        jsonl(project / "ffff6666.jsonl", [
            {**c_user("2026-09-26T15:00:00Z", "shared reflection"), "cwd": str(other / "src")},
            c_tool("2026-09-26T15:00:01Z", "sh1", "Bash", {"command": "ls"}),
        ])
        for flags in ([], ["--all"]):
            out = self.run_main("rank", "--since", "2000-01-01T00:00:00Z", "--min-calls", "1", *flags)
            self.assertNotIn("ffff6666", out)
        self.assertIn("aaaa1111", out)

    def test_since_counts_only_new_work(self):
        s = sessions.find("aaaa1111")
        later = sessions.when("2026-09-26T10:30:00Z")
        row = s.summary(later)
        self.assertEqual((row["users"], row["calls"], row["failed"]), (1, 1, 0))  # queued message and the Read
        self.assertEqual(row["subagents"], 0)  # the subagent ran at 10:05
        self.assertEqual(row["start"], "2026-09-26T10:00Z")  # the session's own start
        self.assertEqual(s.tokens(later), 0)
        out = self.run_main("timeline", "aaaa1111", "--since", "2026-09-26T10:30:00Z")
        self.assertIn("Read: /Users/u/project/notes.md", out)
        self.assertNotIn("grep -r mode", out)
        codex = sessions.find("cccc3333")
        self.assertEqual(codex.tokens(), 4321)
        self.assertEqual(codex.tokens(sessions.when("2026-09-26T12:05:00Z")), 0)
        ranked = self.run_main("rank", "--since", "2026-09-26T10:30:00Z", "--min-calls", "1")
        self.assertIn("aaaa1111", ranked)
        self.assertIn("calls=1 ", ranked)

    def test_codex_session(self):
        row = sessions.find("cccc3333").summary()
        self.assertEqual((row["model"], row["users"], row["calls"], row["failed"], row["repeated"]),
                         ("gpt-6-astra", 1, 2, 2, 1))  # plain exit code and code-mode JSON exit_code
        self.assertEqual(row["tokens_m"], 0.0)
        out = self.run_main("timeline", "cccc3333")
        self.assertIn("exec_command: npm test", out)
        self.assertIn("SAY One test fails.", out)

    def test_rank_leaves_out_replays_unless_all(self):
        out = self.run_main("rank", "--since", "2000-01-01T00:00:00Z", "--min-calls", "1")
        self.assertIn("aaaa1111", out)
        self.assertIn("cccc3333", out)
        self.assertNotIn("bbbb2222", out)
        self.assertNotIn("dddd4444", out)
        self.assertLess(out.index("aaaa1111"), out.index("cccc3333"))  # more active minutes first
        everything = self.run_main("rank", "--since", "2000-01-01T00:00:00Z", "--min-calls", "1", "--all")
        self.assertIn("bbbb2222", everything)
        self.assertIn("dddd4444", everything)
        self.assertNotIn("eeee5555", everything)  # archived threads stay out, as in record.py

    def test_archived_codex_thread_is_not_shown(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                sessions.main(["timeline", "eeee5555"])

    def test_transcript_path_and_unknown_session(self):
        path = next((self.claude_home / "projects").glob("*/aaaa1111.jsonl"))
        self.assertIn("aaaa1111", self.run_main("timeline", str(path)))
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                sessions.main(["timeline", "zzzz9999"])


if __name__ == "__main__":
    unittest.main()
