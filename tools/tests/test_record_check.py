"""Exercise the record check: passages, removal detection, retrieval, verdicts and the commit hook."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("record_check", Path(__file__).parents[1] / "record_check.py")
rc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rc)

GUIDE = """---
id: core.x
summary: A guide.
---

# Guide

Keep at most six workers active at once during a scheduled run.

- Budget a scheduled run at five hours and look for room to improve.
- Never rebase the release branch.

```sh
python3 tools/modules.py check
```

| Case | Rule |
|---|---|
| Guard | The Guard's inquiry has priority over the worker's plan. |

Short.
"""

MESSAGES = [
    ("2030-01-15T12:00:00.000Z", "Feel free to change it: give a five-hour budget and look for improvement."),
    ("2030-01-23T12:00:00.000Z", "The Guard outranks the worker, and the worker must not steer it."),
    ("2030-01-24T10:00:00.000Z", "Can you set up the printer for me?"),
    ("2030-01-24T11:00:00.000Z", "我想要咖啡机，办公用"),
]


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


class RecordCheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "repo"
        (self.root / "src" / "core").mkdir(parents=True)
        (self.root / "src" / "record").mkdir()
        (self.root / "src" / "core" / "guide.md").write_text(GUIDE, encoding="utf-8")
        (self.root / "notes.txt").write_text("Not Markdown, so out of scope for the check.\n", encoding="utf-8")
        rows = [{"id": f"codex:m{i}", "time": t, "host": "codex", "session": "s", "message": f"m{i}", "source": "x",
                 "line": 1, "kind": "message", "stance": "own", "text": text} for i, (t, text) in enumerate(MESSAGES)]
        (self.root / "src" / "record" / "2030-01.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "t@example.com")
        git(self.root, "config", "user.name", "T")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")

    def edit(self, text, extra=None):
        (self.root / "src" / "core" / "guide.md").write_text(text, encoding="utf-8")
        for path, body in (extra or {}).items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(body, encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "edit")

    def run_tool(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rc.main(["--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def test_passages_skip_front_matter_headings_code_and_short_lines(self):
        found = rc.passages(GUIDE)
        self.assertEqual(found, [
            "Keep at most six workers active at once during a scheduled run.",
            "- Budget a scheduled run at five hours and look for room to improve.",
            "- Never rebase the release branch.",
            "| Guard | The Guard's inquiry has priority over the worker's plan. |",
        ])

    def test_scope(self):
        self.assertTrue(rc.in_scope("src/core/learn/learn.md"))
        self.assertTrue(rc.in_scope("taste-guard.md"))
        self.assertTrue(rc.in_scope("integrations/claude/skills/taste/SKILL.md"))
        self.assertTrue(rc.in_scope("src/personal/everyday.md"))
        self.assertFalse(rc.in_scope("src/inbox.md"))
        self.assertFalse(rc.in_scope("src/research/taste.md"))
        self.assertFalse(rc.in_scope("src/record/README.md"))
        self.assertFalse(rc.in_scope("tools/README.md"))
        self.assertFalse(rc.in_scope("src/core/x.json"))

    def test_removed_and_rewritten_passages_but_not_moved_or_kept_ones(self):
        moved = "Keep at most six workers active at once during a scheduled run."
        self.edit(GUIDE.replace(moved + "\n\n", "")
                  .replace("- Budget a scheduled run at five hours and look for room to improve.\n",
                           "- Budget a scheduled run generously.\n")
                  .replace("| Guard | The Guard's inquiry has priority over the worker's plan. |\n", ""),
                  {"src/core/other.md": "# Other\n\n" + moved + "\n"})
        removed = {i["old"]: i for i in rc.removed_passages(self.root, "HEAD~1", "HEAD")}
        self.assertEqual(set(removed), {
            "- Budget a scheduled run at five hours and look for room to improve.",
            "| Guard | The Guard's inquiry has priority over the worker's plan. |"})
        rewritten = removed["- Budget a scheduled run at five hours and look for room to improve."]
        self.assertEqual(rewritten["new"], "- Budget a scheduled run generously.")
        self.assertGreater(rewritten["overlap"], 0)
        self.assertFalse(rewritten["file_deleted"])

    def test_deleted_file_and_record_changes(self):
        git(self.root, "rm", "-q", "src/core/guide.md")
        with (self.root / "src" / "record" / "2030-01.jsonl").open("a") as handle:
            handle.write("\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "delete")
        removed = rc.removed_passages(self.root, "HEAD~1", "HEAD")
        self.assertEqual(len(removed), 4)
        self.assertTrue(all(i["file_deleted"] and i["path"] == "src/core/guide.md" for i in removed))

    def test_candidates_find_the_source_and_cited_dates(self):
        index = rc.Index(rc.load_words(self.root))
        found = rc.candidates(index, "The Guard's inquiry has priority over the worker's plan.", "2030")
        self.assertEqual(found[0]["id"], "codex:m1")
        # A passage citing January 15 also gets that day's best matches, even with weak wording overlap.
        found = rc.candidates(index, "The January 15 design gives the launcher a budget.", "2030")
        self.assertIn("codex:m0", [e["id"] for e in found])
        self.assertEqual(rc.mentioned_days("user, 2030-01-24; January 18–19", "2030"),
                         [{"2030-01-24", "2030-01-25"}, {"2030-01-18", "2030-01-19"}, {"2030-01-19", "2030-01-20"}])
        self.assertEqual(rc.terms("办公用"), ["办公", "公用"])
        self.assertEqual(rc.terms("unmaintained prompts"), rc.terms("maintained prompt"))

    def test_cutoff_hides_later_words(self):
        entries = rc.load_words(self.root, "2030-01-20T00:00:00.000Z")
        self.assertEqual([e["id"] for e in entries], ["codex:m0"])

    def test_trace_packets_unjudged_passages_and_reports_lost_ones(self):
        base = git(self.root, "rev-parse", "HEAD").strip()
        self.edit(GUIDE.replace("| Guard | The Guard's inquiry has priority over the worker's plan. |\n", "")
                  .replace("- Never rebase the release branch.\n", ""))
        code, out, _ = self.run_tool("trace", f"{base}..HEAD")
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual((result["passages"], result["pending"], result["judged"], result["lost"]), (2, 2, {}, []))
        self.assertEqual(len(result["packets"]), 1)
        text = Path(result["packets"][0]).read_text()
        self.assertIn("must not steer it", text)
        self.assertIn("python3 tools/record_check.py verdict", text)
        head = git(self.root, "rev-parse", "--short=12", "HEAD").strip()
        self.assertEqual(result["range"], f"{base[:12]}..{head}")
        self.assertIn(f"git show {head}:<path>", text)
        self.assertIn("/.git/mnemorph-record-check/packets/", result["packets"][0])
        ids = {i["old"]: i["id"] for i in rc.removed_passages(self.root, base, "HEAD")}
        guard = ids["| Guard | The Guard's inquiry has priority over the worker's plan. |"]
        self.run_tool("verdict", guard, "lost", "User, 2030-01-23: 'the Guard outranks the worker'")
        result = json.loads(self.run_tool("trace", f"{base}..HEAD")[1])
        self.assertEqual((result["pending"], result["judged"]), (1, {"lost": 1}))
        self.assertEqual((result["lost"][0]["id"], result["lost"][0]["old"]),
                         (guard, "| Guard | The Guard's inquiry has priority over the worker's plan. |"))
        self.run_tool("verdict", ids["- Never rebase the release branch."], "not-from-user", "No message asked for it")
        result = json.loads(self.run_tool("trace", f"{base}..HEAD")[1])
        self.assertEqual((result["pending"], result["packets"]), (0, []))
        # Restoring the lost passage removes it from the audit.
        self.edit(GUIDE.replace("- Never rebase the release branch.\n", ""))
        result = json.loads(self.run_tool("trace", f"{base}..HEAD")[1])
        self.assertEqual((result["passages"], result["lost"]), (1, []))
        self.assertEqual(self.run_tool("verdict", guard, "fine", "x")[0], 2)

    def test_cutoff_limits_the_words_a_replay_sees(self):
        self.edit(GUIDE.replace("| Guard | The Guard's inquiry has priority over the worker's plan. |\n", ""))
        result = json.loads(self.run_tool("trace", "HEAD~1..HEAD", "--cutoff", "2030-01-20T00:00:00Z")[1])
        self.assertNotIn("must not steer it", Path(result["packets"][0]).read_text())
        self.assertEqual(self.run_tool("trace", "nonsense..HEAD")[0], 2)

if __name__ == "__main__":
    unittest.main()
