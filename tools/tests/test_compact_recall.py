"""Check that compact_recall.py returns the user's typed words, and nothing else, after compaction."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compact_recall  # noqa: E402


def user(text, **extra):
    return {"type": "user", "timestamp": "2099-01-01T00:00:00Z", "message": {"role": "user", "content": text}, **extra}


class Recall(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "s.jsonl"
        records = [
            user("Please sort these sample shapes by size."),
            user([{"type": "tool_result", "tool_use_id": "t", "content": "tool output"}]),
            user("<command-name>/taste</command-name>\n<command-args>is this general enough?</command-args>"),
            user("<local-command-stdout>ok</local-command-stdout>"),
            user("expanded skill text", isMeta=True),
            user("This session is being continued from a previous conversation...", isCompactSummary=True),
            {"type": "attachment", "timestamp": "2099-01-01T00:01:00Z",
             "attachment": {"type": "queued_command", "prompt": "mid-turn words<system-reminder>x</system-reminder>",
                            "origin": {"kind": "human"}}},
            {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "agent note", "origin": {"kind": "agent"}}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "agent words"}]}},
            user("[Request interrupted by user]"),
        ]
        self.path.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def tearDown(self):
        self.dir.cleanup()

    def test_only_the_users_typed_words_in_order(self):
        text = compact_recall.recall(self.path)
        body = [line.split("UTC: ", 1)[1] for line in text.splitlines()[1:]]
        self.assertEqual(body, ["Please sort these sample shapes by size.",
                                "/taste is this general enough?", "mid-turn words"])

    def test_cap_keeps_the_first_and_latest(self):
        with self.path.open("a") as handle:
            for i in range(50):
                handle.write(json.dumps(user(f"message {i} " + "x" * 200)) + "\n")
        text = compact_recall.recall(self.path, cap=2000)
        self.assertIn("Please sort these sample shapes", text)
        self.assertIn("message 49", text)
        self.assertNotIn("message 10 ", text)
        self.assertIn("earlier messages left out", text)
        self.assertLessEqual(len(text), 2300)

    def test_hook_answers_only_after_compaction(self):
        for source, expect in (("compact", True), ("startup", False)):
            out = io.StringIO()
            sys.stdin = io.StringIO(json.dumps({"source": source, "transcript_path": str(self.path)}))
            with redirect_stdout(out):
                compact_recall.main([])
            sys.stdin = sys.__stdin__
            if expect:
                ctx = json.loads(out.getvalue())["hookSpecificOutput"]
                self.assertEqual(ctx["hookEventName"], "SessionStart")
                self.assertIn("mid-turn words", ctx["additionalContext"])
            else:
                self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
