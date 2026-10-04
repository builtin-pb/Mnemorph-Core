"""Exercise the one-time context-size warning against synthetic transcripts."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("context_warning", Path(__file__).parents[1] / "context_warning.py")
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


def reply(cached, sidechain=False):
    return {"type": "assistant", "isSidechain": sidechain,
            "message": {"usage": {"input_tokens": 2, "cache_creation_input_tokens": 1000,
                                  "cache_read_input_tokens": cached, "output_tokens": 200}}}


class ContextWarningTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        patch = mock.patch.object(tool, "STATE", self.dir / "state")
        patch.start()
        self.addCleanup(patch.stop)

    def transcript(self, *records):
        path = self.dir / "s.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return path

    def hook(self, path, tokens=500000):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"session_id": "s1", "transcript_path": str(path)}))), \
                contextlib.redirect_stdout(out):
            self.assertEqual(tool.main(["--tokens", str(tokens)]), 0)
        return out.getvalue()

    def test_counts_the_last_main_thread_reply(self):
        path = self.transcript(reply(100), {"type": "user"}, reply(498000), reply(900000, sidechain=True))
        self.assertEqual(tool.context_tokens(path), 499202)

    def test_quiet_below_the_limit(self):
        self.assertEqual(self.hook(self.transcript(reply(400000))), "")

    def test_warns_once_past_the_limit(self):
        path = self.transcript(reply(510000))
        out = json.loads(self.hook(path))
        self.assertIn("511k", out["systemMessage"])
        self.assertIn("500k", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.hook(path), "")  # the same session is warned only once

    def test_a_broken_event_stays_quiet(self):
        self.assertEqual(self.hook(self.dir / "missing.jsonl"), "")

    def test_reads_back_across_chunks(self):
        filler = {"type": "user", "message": {"content": "x" * 5000}}
        path = self.transcript(reply(600000), *[filler] * 600)
        with mock.patch.object(tool, "CHUNK", 4096):
            self.assertEqual(tool.context_tokens(path), 601202)


if __name__ == "__main__":
    unittest.main()
