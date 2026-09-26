"""Exercise lesson-test recording and candidate listing against temporary Git histories."""
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest

import importlib.util

SPEC = importlib.util.spec_from_file_location("lessons_tool", Path(__file__).parents[1] / "lessons.py")
lessons = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lessons)

GUIDE = "---\nid: w.compose\nsummary: s\n---\n\n# Compose\n\nRule one.\n"
REF = "---\nid: w.judgment\nrole: reference\nsummary: s\n---\n\n# Judgment\n\nCase.\n"


class LessonsToolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.git("init", "-q")
        self.git("config", "user.name", "Lesson tests")
        self.git("config", "user.email", "lessons@example.invalid")
        self.write("src/writing/compose.md", GUIDE)
        self.write("src/writing/judgment.md", REF)
        self.write("src/personal/everyday.md", GUIDE)
        self.base = self.commit("Initial")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args],
                                       stderr=subprocess.PIPE).decode().strip()

    def write(self, name, text):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, subject):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)
        return self.git("rev-parse", "HEAD")

    def run_tool(self, *argv):
        out = io.StringIO()
        import sys
        old = sys.argv
        sys.argv = ["lessons.py", "--root", str(self.root), *argv]
        try:
            with contextlib.redirect_stdout(out):
                code = lessons.main()
        finally:
            sys.argv = old
        return code, out.getvalue()

    def test_guidance_change_is_candidate_until_recorded(self):
        self.write("src/writing/compose.md", GUIDE + "Rule two.\n")
        self.write("src/writing/judgment.md", REF + "Another case.\n")
        self.write("src/personal/everyday.md", GUIDE + "Private.\n")
        c = self.commit("Add rule two")
        _, out = self.run_tool("candidates", f"{self.base}..HEAD")
        self.assertIn("src/writing/compose.md", out)
        self.assertNotIn("judgment.md", out)  # reference, not guidance
        self.assertNotIn("personal", out)
        self.run_tool("record", "--lesson", "lead with the takeaway for skimmers",
                      "--file", "src/writing/compose.md", "--commit", c,
                      "--kind", "concrete", "--case", "Slack digest item",
                      "--check", "first line states the takeaway", "--result", "pass")
        _, out = self.run_tool("candidates", f"{self.base}..HEAD")
        self.assertIn("no untested guidance changes", out)
        _, out = self.run_tool("list")
        self.assertIn("pass", out)
        self.assertIn("src/writing/compose.md", out)

    def test_later_change_to_tested_file_is_still_candidate(self):
        self.write("src/writing/compose.md", GUIDE + "Rule two.\n")
        c1 = self.commit("Add rule two")
        self.run_tool("record", "--lesson", "x", "--file", "src/writing/compose.md",
                      "--commit", c1, "--kind", "judgment", "--case", "c",
                      "--check", "blind pick", "--result", "unverified")
        self.write("src/writing/compose.md", GUIDE + "Rule two.\nRule three.\n")
        self.commit("Add rule three")
        _, out = self.run_tool("candidates", "--verbose", f"{self.base}..HEAD")
        self.assertIn("untested", out)
        self.assertIn("Add rule three", out)
        self.assertIn("unverified", out)  # a non-pass result stays listed
        self.assertIn("Add rule two", out)

    def test_headerless_markdown_is_not_guidance(self):
        self.write("src/writing/examples/piece.md", "# A produced article\n\nText.\n")
        self.commit("Add example")
        _, out = self.run_tool("candidates", f"{self.base}..HEAD")
        self.assertIn("no untested guidance changes", out)

    def test_record_resolves_head(self):
        self.write("src/writing/compose.md", GUIDE + "Rule two.\n")
        c = self.commit("Add rule two")
        self.run_tool("record", "--lesson", "x", "--file", "src/writing/compose.md",
                      "--commit", "HEAD", "--kind", "concrete", "--case", "c",
                      "--check", "k", "--result", "pass")
        _, out = self.run_tool("candidates", f"{self.base}..HEAD")
        self.assertIn("no untested guidance changes", out)


if __name__ == "__main__":
    unittest.main()
