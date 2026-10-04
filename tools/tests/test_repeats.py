"""Exercise repeated-correction detection against a synthetic record."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("repeats_tool", Path(__file__).parents[1] / "repeats.py")
repeats = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repeats)


def entry(n, time, text, kind="message", stance="own", host="claude"):
    return {"id": f"{host}:m{n}", "time": time, "host": host, "session": "s", "message": f"m{n}",
            "source": "~/nowhere.jsonl", "line": n, "kind": kind, "stance": stance, "text": text,
            "context": {"excerpt": f"agent said {n}"}}


class RepeatsToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.check_call(["git", "init", "-q", str(self.root)])
        (self.root / "tools").mkdir()
        # count reuses record.py's model lookup
        real = Path(__file__).parents[1] / "record.py"
        (self.root / "tools" / "record.py").write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "src" / "record").mkdir(parents=True)
        (self.root / "src" / "research").mkdir(parents=True)
        rows = [
            entry(1, "2026-09-20T10:00:00.000Z", "your summaries are too long, put the decisions first"),
            entry(2, "2026-09-26T10:00:00.000Z", "again this summary is too long, decisions first please"),
            entry(3, "2026-09-26T10:05:00.000Z", "please rename the output folder"),
            entry(4, "2026-09-26T10:06:00.000Z", "yes", kind="question_reply"),
            entry(5, "2026-09-26T10:07:00.000Z", "don't restart the build while it is still running"),
        ]
        (self.root / "src" / "record" / "2026-09.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def run_tool(self, *argv):
        if argv[0] == "verdict":
            argv = (*argv, "--labeller", "test-model")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = repeats.main(["--root", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_trace_packets_hold_pending_work_messages_with_similar_earlier_ones(self):
        code, out, _ = self.run_tool("trace", "--since", "2026-09-25T18:00:00Z")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["pending"], 3)  # question_reply excluded; m1 before start
        packet = json.loads((self.root / repeats.PACKETS / "next.json").read_text())
        first = packet["messages"][0]
        self.assertEqual(first["id"], "claude:m2")
        self.assertIn("claude:m1", [s["id"] for s in first["similar"]])
        self.assertIn("agent said 2", first["context"])

    def test_repeat_of_earlier_correction_catalogues_it_and_counts(self):
        code, _, err = self.run_tool("verdict", "claude:m2", "repeat", "--of", "claude:m1",
                                     "--describe", "keep replies short: lead with decisions")
        self.assertEqual(code, 0, err)
        self.run_tool("verdict", "claude:m3", "none")
        self.run_tool("verdict", "claude:m5", "new", "--describe", "do not restart running work")
        code, _, err = self.run_tool("verdict", "claude:m3", "none")
        self.assertEqual(code, 2)  # already labelled
        _, out, _ = self.run_tool("count", "--since", "2026-09-25T18:00:00Z", "--new-since", "2000-01-01T00:00:00Z")
        result = json.loads(out)
        self.assertEqual(result["work_messages"], 3)
        self.assertEqual(result["labelled"], 3)
        self.assertEqual(result["repeats"], 1)
        self.assertEqual(result["corrections"], 2)
        self.assertEqual(result["repeats_listed"][0]["kind"], "keep replies short: lead with decisions")
        self.assertIn("do not restart running work", [k["describe"] for k in result["new_kinds"]])
        self.assertEqual(result["by_labeller"], {"test-model": {"labelled": 3, "repeat": 1}})
        ledger = (self.root / repeats.LEDGER).read_text()
        self.assertNotIn("summary is too long", ledger)  # ids, never text

    def test_second_correction_and_packet_catalog_grow_between_packets(self):
        self.run_tool("verdict", "claude:m2", "new", "--describe", "keep replies short")
        code, _, err = self.run_tool("verdict", "claude:m2", "new", "--also", "--describe", "check facts")
        self.assertEqual(code, 0, err)
        self.run_tool("verdict", "claude:m5", "repeat", "--of", "claude:m2")  # takes m2's first kind
        _, out, _ = self.run_tool("trace", "--since", "2026-09-25T18:00:00Z")
        packet = json.loads((self.root / repeats.PACKETS / "next.json").read_text())
        self.assertEqual([k["describe"] for k in packet["catalog"]], ["keep replies short", "check facts"])
        self.assertEqual([m["id"] for m in packet["messages"]], ["claude:m3"])

    def test_verdict_needs_the_labeller(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            repeats.main(["--root", str(self.root), "verdict", "claude:m3", "none"])
        self.run_tool("verdict", "claude:m3", "none")
        row = json.loads((self.root / repeats.LEDGER).read_text().splitlines()[-1])
        self.assertEqual(row["labeller"], "test-model")

    def test_repeat_needs_a_kind_or_an_earlier_message(self):
        code, _, err = self.run_tool("verdict", "claude:m2", "repeat")
        self.assertEqual(code, 2)
        code, _, err = self.run_tool("verdict", "claude:m2", "repeat", "--of", "claude:m5", "--describe", "x")
        self.assertEqual(code, 2)  # m5 is later


if __name__ == "__main__":
    unittest.main()
