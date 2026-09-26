"""Exercise the record of the user's own words against synthetic Codex and Claude Code transcripts."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("record_tool", Path(__file__).parents[1] / "record.py")
record = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(record)

# Synthetic credentials, assembled at runtime so no literal looks like a real key.
SK = "sk-" + "a1B2c3D4" * 4
GH = "gh" + "p_" + "Zy9Xw8Vu7" * 4
SVC = "Qm7" + "tR2vX9pL4kW8" * 4
CR = "cr" + "\\_" + "0f1e2d3c" * 8
PEM = "-----BEGIN " + "PRIVATE KEY-----\nMIIEv" + "Q" * 40 + "\n-----END " + "PRIVATE KEY-----"
SECRETS = [SK, GH, SVC, CR.replace("\\", ""), "0f1e2d3c" * 8, "MIIEv"]


def jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def claude_user(uuid, time, content, **extra):
    row = {"type": "user", "uuid": uuid, "timestamp": time, "sessionId": "s1", "isSidechain": False,
           "entrypoint": "claude-desktop", "cwd": "/Users/u/project", "message": {"role": "user", "content": content},
           "origin": {"kind": "human"}}
    row.update(extra)
    return row


def claude_assistant(uuid, time, text, message_id=None, **extra):
    row = {"type": "assistant", "uuid": uuid, "timestamp": time, "sessionId": "s1", "isSidechain": False,
           "message": {"id": message_id or "m-" + uuid, "role": "assistant", "content": [{"type": "text", "text": text}]}}
    row.update(extra)
    return row


def queued(uuid, time, prompt, kind):
    return {"type": "attachment", "uuid": uuid, "timestamp": time, "sessionId": "s1", "isSidechain": False,
            "attachment": {"type": "queued_command", "prompt": prompt, "commandMode": "prompt",
                           "origin": {"kind": kind} if kind else None}}


def codex_meta(thread, time, source="vscode", thread_source="user", **extra):
    payload = {"id": thread, "timestamp": time, "cwd": "/Users/u/project", "source": source,
               "thread_source": thread_source, "originator": "Codex Desktop"}
    payload.update(extra)
    return {"timestamp": time, "type": "session_meta", "payload": payload}


def codex_item(time, ordinal, item):
    return {"timestamp": time, "ordinal": ordinal, "type": "event_msg",
            "payload": {"type": "item_completed", "item": item}}


def codex_user(time, ordinal, item_id, text, *parts):
    return codex_item(time, ordinal, {"type": "UserMessage", "id": item_id,
                                      "content": [{"type": "text", "text": text}, *parts]})


def codex_agent(time, ordinal, item_id, text):
    return codex_item(time, ordinal, {"type": "AgentMessage", "id": item_id,
                                      "content": [{"type": "Text", "text": text}]})


def goal(time, ordinal, objective, created=1790000000, status="active"):
    return {"timestamp": time, "ordinal": ordinal, "type": "event_msg",
            "payload": {"type": "thread_goal_updated", "goal": {"objective": objective, "createdAt": created,
                                                                 "status": status}}}


class RecordTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "repo"
        self.claude = self.base / "claude" / "projects"
        self.codex = self.base / "codex" / "sessions"
        self.archived = self.base / "codex" / "archived_sessions"
        self.state = self.base / "codex" / "state.sqlite"
        self.build_claude()
        self.build_codex()

    # -- fixtures --------------------------------------------------------
    def build_claude(self):
        project = self.claude / "-Users-u-project"
        reminder = "<system-reminder>\nThe user started this session without choosing a folder.\n</system-reminder>"
        jsonl(project / "s1.jsonl", [
            {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-09-24T20:00:00.000Z",
             "content": "Please restructure the memory folders"},
            claude_user("u1", "2026-09-24T20:00:00.000Z",
                        [{"type": "text", "text": reminder}, {"type": "text", "text": "Please restructure the memory folders"}]),
            claude_user("u2", "2026-09-24T20:00:01.000Z", [{"type": "text", "text": "Base directory for this skill: x"}],
                        isMeta=True),
            claude_assistant("a1", "2026-09-24T20:01:00.000Z", "Shall I (a) merge them or (b) keep both? My key: " + GH),
            {"type": "user", "uuid": "t1", "timestamp": "2026-09-24T20:01:05.000Z", "isSidechain": False,
             "message": {"content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}},
            queued("q1", "2026-09-24T20:02:00.000Z", "4. b", "human"),
            queued("q2", "2026-09-24T20:02:30.000Z", "Guard finding: agents disagree", "peer"),
            queued("q3", "2026-09-24T20:02:40.000Z", "<task-notification>done</task-notification>", None),
            claude_user("u3", "2026-09-24T20:03:00.000Z", "<task-notification>\n<task-id>x</task-id>",
                        origin={"kind": "task-notification"}),
            claude_user("u4", "2026-09-24T20:04:00.000Z", "Another Claude session sent a message", isMeta=True,
                        origin={"kind": "peer"}),
            claude_user("u5", "2026-09-24T20:05:00.000Z", "sub-agent words", isSidechain=True),
            claude_user("u6", "2026-09-24T20:06:00.000Z", record._APP_RESUME),
            claude_assistant("a2", "2026-09-24T20:07:00.000Z", "You've hit your limit", isApiErrorMessage=True),
            claude_user("u7", "2026-09-24T20:08:00.000Z", "Try again"),
            claude_user("u8", "2026-09-24T20:09:00.000Z",
                        [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
                         {"type": "text", "text": "what about this one? password: hunter22"}], promptId="p8"),
            claude_user("u9", "2026-09-24T20:09:00.000Z", [{"type": "text", "text": "[Image: source: /tmp/img/1.png]"}],
                        isMeta=True, promptId="p8"),
            claude_user("u10", "2026-09-24T20:10:00.000Z", "<scheduled-task name=\"nightly\">run</scheduled-task>"),
            claude_user("u11", "2026-09-24T20:11:00.000Z", "Summary of the conversation", isCompactSummary=True),
            {"type": "user", "uuid": "partial", "timestamp": "2026-09-24T20:12:00.000Z"},
        ])
        with (project / "s1.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"type": "user", "truncated')  # a line still being written
        # A resumed copy of a message in another file keeps its uuid: recorded once.
        jsonl(project / "s1-copy.jsonl", [claude_user("u1", "2026-09-24T20:00:00.000Z", "Please restructure the memory folders")])
        jsonl(project / "s1" / "subagents" / "agent-a.jsonl", [claude_user("sa", "2026-09-24T20:00:00.000Z", "delegate prompt")])
        jsonl(project / "sdk.jsonl", [claude_user("k1", "2026-09-24T21:00:00.000Z", "claude -p prompt", entrypoint="sdk-cli")])
        jsonl(self.claude / "-private-tmp-eval" / "e.jsonl",
              [claude_user("e1", "2026-09-24T21:00:00.000Z", "eval prompt", cwd="/private/tmp/eval")])

    def build_codex(self):
        day = self.codex / "2026" / "09" / "10"
        main = "01a0-main"
        jsonl(day / f"rollout-2026-09-10T10-00-00-{main}.jsonl", [
            codex_meta(main, "2026-09-10T14:00:00.000Z"),
            {"timestamp": "2026-09-10T14:00:00.500Z", "type": "response_item",
             "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>x"}]}},
            codex_user("2026-09-10T14:00:01.000Z", 1, "c1", "Can you check my taxes?\n"),
            codex_agent("2026-09-10T14:01:00.000Z", 2, "g1", "I need an API key for DeepSeek."),
            codex_user("2026-09-10T14:02:00.000Z", 3, "c2", "Here, use this: " + SK + "\n"),
            codex_user("2026-09-10T14:03:00.000Z", 4, "c3", "My service api key is " + SVC + ". Also API key is " + CR),
            codex_user("2026-09-10T14:04:00.000Z", 5, "c4",
                       "<heartbeat>\n  <automation_id>collect</automation_id>\n</heartbeat>"),
            codex_user("2026-09-10T14:05:00.000Z", 6, "c5", "<send_user_message_question_reply>\n" + json.dumps(
                [{"questionItemId": "q", "question": "Did you finish the login?", "answer": "Done"}])
                + "\n</send_user_message_question_reply>"),
            codex_user("2026-09-10T14:06:00.000Z", 7, "c6",
                       "\n# Response annotations:\nEach item contains text selected.\n<response-annotations>\n"
                       + json.dumps([{"text": "the agent's selected sentence", "source": {}}])
                       + "\n</response-annotations>\n\n## My request:\nWhat does this mean\n"),
            codex_user("2026-09-10T14:07:00.000Z", 8, "c7",
                       "\n# Diff comments:\n\n## User Comment 1\nFile: pdf:poster.pdf\nSide: R\nComment:\nremove this\n\n## My request:\n\n",
                       {"type": "text", "text": "The next image shows PDF page 1 at the time of Comment 1. Outlined."},
                       {"type": "image", "image_url": "data:image/png;base64,AAAA"}),
            codex_user("2026-09-10T14:08:00.000Z", 9, "c8",
                       "\n# Files mentioned by the user:\n\n## scan.png: /Users/u/scan.png\n\nDistinguish instructions.\n\n## My request:\nHelp me check?\n",
                       {"type": "local_image", "path": "/Users/u/scan.png"}),
            codex_user("2026-09-10T14:09:00.000Z", 10, "c9",
                       "<in-app-browser-context source=\"ambient-ui-state\">\n# In app browser:\n- Current URL: https://x.test/?token="
                       + SVC + "\n</in-app-browser-context>\n\n## My request:\nWrong page. Never mind.\n"),
            goal("2026-09-10T14:10:00.000Z", 11, "Keep working for 5 hours on the benchmark."),
            goal("2026-09-10T14:20:00.000Z", 12, "Keep working for 5 hours on the benchmark.", status="paused"),
            codex_user("2026-09-10T14:30:00.000Z", 13, "c10", "Step 2 is slower than expected... why?\n"),
        ])
        # A continuation segment whose first message replays the previous one.
        jsonl(day / f"rollout-2026-09-10T10-40-00-{main}_seg2.jsonl", [
            codex_meta(main, "2026-09-10T14:00:00.000Z"),
            codex_user("2026-09-10T14:31:00.000Z", 14, "c11", "Step 2 is slower than expected... why?\n"),
            codex_user("2026-09-10T14:32:00.000Z", 15, "c12", "Done"),
            codex_user("2026-09-10T14:33:00.000Z", 16, "c13", "Done"),
        ])
        fork = "01a0-fork"
        jsonl(day / f"rollout-2026-09-10T11-00-00-{fork}.jsonl", [
            codex_meta(fork, "2026-09-10T15:00:00.000Z", forked_from_id=main, forked_from_ordinal_exclusive=12),
            codex_user("2026-09-10T15:00:05.000Z", 20, "f1", "Step 2 is slower than expected... why?\n"),
            codex_user("2026-09-10T15:01:00.000Z", 21, "f2", "You are forked. Compare the two."),
        ])
        for name, meta in [("exec", codex_meta("t-exec", "2026-09-10T16:00:00.000Z", source="exec")),
                           ("sub", codex_meta("t-sub", "2026-09-10T16:00:00.000Z", source={"subagent": {"thread_spawn": {}}},
                                              thread_source="subagent")),
                           ("guard", codex_meta("t-guard", "2026-09-10T16:00:00.000Z", source={"subagent": {"other": "guardian"}},
                                                thread_source="guardian_review")),
                           ("auto", codex_meta("t-auto", "2026-09-10T16:00:00.000Z", thread_source="automation")),
                           ("arch", codex_meta("t-arch", "2026-09-10T16:00:00.000Z"))]:
            jsonl(day / f"rollout-2026-09-10T12-00-00-t-{name}.jsonl",
                  [meta, codex_user("2026-09-10T16:00:01.000Z", 1, f"x-{name}", f"not the user's typing {name}")])
        jsonl(self.archived / "rollout-2026-09-10T12-00-00-t-old.jsonl",
              [codex_meta("t-old", "2026-09-10T16:00:00.000Z"), codex_user("2026-09-10T16:00:01.000Z", 1, "x-old", "archived")])
        october = self.codex / "2026" / "10" / "01"
        jsonl(october / "rollout-2026-10-01T09-00-00-01a0-oct.jsonl", [
            codex_meta("01a0-oct", "2026-10-01T13:00:00.000Z", source="cli", originator="codex-tui"),
            codex_user("2026-10-01T13:00:01.000Z", 1, "o1", "hi"),
        ])
        connection = sqlite3.connect(self.state)
        connection.execute("create table threads (id text primary key, archived integer)")
        connection.execute("insert into threads values ('t-arch', 1), ('01a0-main', 0)")
        connection.commit()
        connection.close()

    # -- helpers ---------------------------------------------------------
    def run_tool(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = record.main(["--root", str(self.root), *args])
        return code, out.getvalue()

    def append(self):
        code, out = self.run_tool("append", "--claude-projects", str(self.claude), "--codex-sessions", str(self.codex),
                                  "--codex-state", str(self.state))
        self.assertEqual(code, 0)
        return json.loads(out), out

    def entries(self):
        rows = []
        for path in sorted((self.root / "src" / "record").glob("*.jsonl")):
            rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return {row["message"]: row for row in rows}

    # -- tests -----------------------------------------------------------
    def test_includes_only_human_typed_messages(self):
        summary, _ = self.append()
        rows = self.entries()
        self.assertEqual(rows["u1"]["text"], "Please restructure the memory folders")
        self.assertEqual(rows["q1"]["kind"], "queued")
        self.assertEqual(rows["u8"]["attachments"], [{"type": "image", "media_type": "image/png", "path": "/tmp/img/1.png"}])
        for message in ("u2", "u3", "u4", "u5", "u6", "u7", "u10", "u11", "q2", "q3", "sa", "k1", "e1", "t1"):
            self.assertNotIn(message, rows, message)
        self.assertEqual(rows["c1"]["text"], "Can you check my taxes?\n")
        self.assertEqual((rows["c5"]["kind"], rows["c5"]["text"]), ("question_reply", "Done"))
        self.assertEqual(rows["c5"]["context"]["excerpt"], "Did you finish the login?")
        self.assertEqual((rows["c6"]["kind"], rows["c6"]["text"]), ("annotation", "What does this mean"))
        self.assertEqual(rows["c6"]["context"]["type"], "selection")
        self.assertEqual((rows["c7"]["kind"], rows["c7"]["text"]), ("diff_comment", "remove this"))
        self.assertEqual(rows["c7"]["attachments"], [{"type": "image"}, {"type": "file", "name": "pdf:poster.pdf"}])
        self.assertEqual((rows["c8"]["kind"], rows["c8"]["text"]), ("attachment", "Help me check?"))
        self.assertIn({"type": "file", "name": "scan.png", "path": "/Users/u/scan.png"}, rows["c8"]["attachments"])
        self.assertEqual(rows["c9"]["text"], "Wrong page. Never mind.")
        goals = [row for row in rows.values() if row["kind"] == "goal"]
        self.assertEqual([g["text"] for g in goals], ["Keep working for 5 hours on the benchmark."])
        for message in ("c4", "x-exec", "x-sub", "x-guard", "x-auto", "x-arch", "x-old"):
            self.assertNotIn(message, rows, message)
        excluded = summary["excluded"]
        self.assertEqual(excluded["codex"]["heartbeat"], 1)
        self.assertEqual(excluded["codex"]["archived_thread"], 1)
        self.assertEqual(excluded["claude"]["app_generated"], 2)
        self.assertEqual(excluded["claude"]["sdk_or_print_session"], 1)
        self.assertEqual(excluded["claude"]["temporary_directory_session"], 1)

    def test_context_is_separate_agent_text(self):
        self.append()
        rows = self.entries()
        context = rows["q1"]["context"]
        self.assertEqual((context["by"], context["type"], context["message"]), ("agent", "preceding_message", "a1"))
        self.assertIn("(b) keep both", context["excerpt"])
        self.assertEqual(rows["q1"]["text"], "4. b")
        self.assertEqual(rows["c2"]["context"]["excerpt"], "I need an API key for DeepSeek.")
        self.assertEqual(rows["f2"]["context"]["session"], "01a0-main")  # fork falls back to the parent

    def test_replays_and_copies_are_recorded_once(self):
        summary, _ = self.append()
        rows = self.entries()
        self.assertIn("c10", rows)
        self.assertNotIn("c11", rows)   # first message of a continuation segment repeating c10
        self.assertNotIn("f1", rows)    # fork replaying the parent's message
        self.assertIn("f2", rows)
        self.assertIn("c12", rows)
        self.assertIn("c13", rows)      # the same short reply typed twice stays twice
        self.assertEqual(summary["excluded"]["codex"]["fork_or_resume_replay"], 2)
        self.assertEqual(sum(1 for row in rows.values() if row["message"] == "u1"), 1)

    def test_append_is_idempotent_and_partitions_by_month(self):
        self.append()
        before = {p.name: p.read_bytes() for p in (self.root / "src" / "record").glob("*.jsonl")}
        self.assertEqual(sorted(before), ["2026-09.jsonl", "2026-10.jsonl"])
        summary, _ = self.append()
        self.assertEqual(summary["added"], 0)
        after = {p.name: p.read_bytes() for p in (self.root / "src" / "record").glob("*.jsonl")}
        self.assertEqual(before, after)
        code, out = self.run_tool("check")
        self.assertEqual((code, json.loads(out)["problem_count"]), (0, 0))

    def test_new_messages_are_appended_without_rewriting(self):
        self.append()
        path = self.root / "src" / "record" / "2026-09.jsonl"
        before = path.read_bytes()
        session = self.codex / "2026" / "09" / "10" / "rollout-2026-09-10T10-40-00-01a0-main_seg2.jsonl"
        with session.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(codex_user("2026-09-10T14:40:00.000Z", 17, "c14", "One more thing.")) + "\n")
        summary, _ = self.append()
        self.assertEqual(summary["added"], 1)
        self.assertTrue(path.read_bytes().startswith(before))

    def test_secrets_never_reach_record_or_output(self):
        _, stdout = self.append()
        text = "".join(p.read_text(encoding="utf-8") for p in (self.root / "src" / "record").glob("*.jsonl"))
        for secret in SECRETS:
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, stdout)
        rows = self.entries()
        self.assertEqual(rows["c2"]["text"], "Here, use this: [REDACTED:api_key]\n")
        self.assertEqual(rows["c3"]["text"],
                         "My service api key is [REDACTED:credential]. Also API key is [REDACTED:api_key]")
        self.assertIn("[REDACTED:github_token]", rows["q1"]["context"]["excerpt"])
        self.assertTrue(rows["u8"]["text"].endswith("password: [REDACTED:credential]"))
        self.assertEqual(rows["c2"]["redacted"], ["api_key"])
        self.assertEqual(record.redact("key\n" + PEM + "\nend"), "key\n[REDACTED:private_key]\nend")

    def test_redaction_keeps_ordinary_text(self):
        for text in ("Token is expensive.", "commit 3f9a2b7c4d5e6f708192a3b4c5d6e7f8091a2b3c",
                     "~/.codex/sessions/2030/01/01/rollout-2030-01-01T00-00-00-00000000-0000-7000-8000-000000000000.jsonl",
                     "https://github.com/o/r/commit/3f9a2b7c4d5e6f708192a3b4c5d6e7f8091a2b3c",
                     "(No password needed.) Password set"):
            self.assertEqual(record.redact(text), text)

    def test_default_sources_follow_the_hosts_directories(self):
        with mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(self.base / "c"), "CODEX_HOME": str(self.base / "x")}):
            args = record.parser().parse_args(["append"])
        self.assertEqual((args.claude_projects, args.codex_sessions, args.codex_state),
                         (self.base / "c" / "projects", self.base / "x" / "sessions", self.base / "x" / "state_5.sqlite"))
        with mock.patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": "", "CODEX_HOME": ""}):
            args = record.parser().parse_args(["append"])
        self.assertEqual(args.claude_projects, Path.home() / ".claude" / "projects")

    def test_stance_is_conservative(self):
        cases = {
            "ok": "acceptance", "4. b": "acceptance", "Let's go with your suggestion.": "acceptance",
            "yes, begin with the first.": "mixed", "Either is ok. And I want shorter prompts everywhere.": "mixed",
            "Why did the reviewer stop...?": "question", "Schedule the daily job for 3am, if not already.": "own",
            "Can you set up the printer for me?": "own", "No, the second option. That sounds better.": "mixed",
            "No.": "own", "Done": "unknown", "hi": "unknown", "": "unknown",
        }
        for text, expected in cases.items():
            self.assertEqual(record.stance(text), expected, text)
        self.assertEqual(record.stance("keeping the machine on overnight.",
                                       "Choose: morning scheduled runs, or keeping the machine on overnight."), "acceptance")



if __name__ == "__main__":
    unittest.main()
