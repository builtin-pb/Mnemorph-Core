"""Exercise the size guard with real `git commit` in temporary repositories."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
GIT_ENV = dict(os.environ)
GIT_ENV.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "Size Guard Tests", "GIT_AUTHOR_EMAIL": "size-guard@example.invalid",
                "GIT_COMMITTER_NAME": "Size Guard Tests", "GIT_COMMITTER_EMAIL": "size-guard@example.invalid"})


def note(chars: int) -> str:
    head = "---\nid: test.note\nsummary: A test note.\n---\n\n"
    return head + "x" * max(0, chars - len(head))


class SizeGuardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve() / "repo"
        (self.repo / "tools").mkdir(parents=True)
        for name in ("memory.py", "size_guard.py"):
            shutil.copy(TOOLS / name, self.repo / "tools" / name)
        (self.repo / "memory-limits.json").write_text(json.dumps(
            {"default": 100, "include": [], "indexed_src": True, "rules": [], "files": {}}))
        self.git("init", "-q", "-b", "main")
        self.write("src/a.md", note(90))
        self.write("src/inbox.md", note(90))
        self.commit_all("start")
        r = subprocess.run([sys.executable, "tools/size_guard.py", "install"], cwd=self.repo,
                           env=GIT_ENV, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def git(self, *args, check=True):
        r = subprocess.run(["git", *args], cwd=self.repo, env=GIT_ENV, text=True, capture_output=True)
        if check and r.returncode:
            raise AssertionError(r.stderr)
        return r

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit_all(self, message):
        self.git("add", "-A")
        return self.git("commit", "-q", "-m", message, check=False)

    def test_refuses_growth_over_the_limit(self):
        self.write("src/a.md", note(150))
        r = self.commit_all("grow")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("src/a.md: 150 characters, limit 100", r.stderr)

    def test_allows_within_limit_and_new_small_files(self):
        self.write("src/a.md", note(99))
        self.write("src/b.md", note(50))
        self.assertEqual(self.commit_all("fits").returncode, 0)

    def test_refuses_a_new_file_over_the_limit(self):
        self.write("src/b.md", note(120))
        self.assertNotEqual(self.commit_all("new").returncode, 0)

    def test_allows_shrinking_an_existing_overage(self):
        self.write("src/a.md", note(200))
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", "over")
        self.write("src/a.md", note(150))
        self.assertEqual(self.commit_all("shrink").returncode, 0)
        self.write("src/a.md", note(160))
        self.assertNotEqual(self.commit_all("regrow").returncode, 0)

    def test_inbox_and_ungoverned_files_are_exempt(self):
        self.write("src/inbox.md", note(500))
        self.write("notes.txt", "x" * 500)
        self.write("src/plain.md", "no header " * 50)
        self.assertEqual(self.commit_all("exempt").returncode, 0)

    def test_path_commit_checks_only_its_paths(self):
        self.write("src/a.md", note(150))
        self.write("src/b.md", note(50))
        self.git("add", "src/b.md")
        r = self.git("commit", "-q", "-m", "only b", "--", "src/b.md", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_allowance_in_limits_is_respected(self):
        self.write("memory-limits.json", json.dumps(
            {"default": 100, "include": [], "indexed_src": True, "rules": [], "files": {"src/a.md": 200}}))
        self.write("src/a.md", note(150))
        self.assertEqual(self.commit_all("allowed").returncode, 0)

    def test_install_refuses_a_foreign_hook(self):
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n")
        r = subprocess.run([sys.executable, "tools/size_guard.py", "install"], cwd=self.repo,
                           env=GIT_ENV, text=True, capture_output=True)
        self.assertNotEqual(r.returncode, 0)


class ProjectGuardTests(unittest.TestCase):
    """A project outside Mnemorph keeps limits for its own instruction files."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve() / "project"
        self.repo.mkdir()
        (self.repo / "memory-limits.json").write_text(json.dumps(
            {"default": 100, "include": ["ops/*.md"], "indexed_src": False, "rules": [], "files": {"ops/BRIEF.md": 150}}))
        self.git("init", "-q", "-b", "main")
        self.write("ops/BRIEF.md", "b" * 140)
        self.write("design/PLAN.md", "p" * 1000)
        self.commit_all("start")
        r = subprocess.run([sys.executable, str(TOOLS / "size_guard.py"), "install"], cwd=self.repo,
                           env=GIT_ENV, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    git = SizeGuardTests.git
    write = SizeGuardTests.write
    commit_all = SizeGuardTests.commit_all

    def test_refuses_growth_of_an_instruction_file(self):
        self.write("ops/BRIEF.md", "b" * 160)
        r = self.commit_all("grow")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ops/BRIEF.md: 160 characters, limit 150", r.stderr)

    def test_ungoverned_designs_may_grow(self):
        self.write("design/PLAN.md", "p" * 5000)
        self.write("ops/NOTE.md", "n" * 90)
        self.assertEqual(self.commit_all("grow design").returncode, 0)

    def test_new_included_file_over_the_default_is_refused(self):
        self.write("ops/NEW.md", "n" * 150)
        self.assertNotEqual(self.commit_all("new long brief").returncode, 0)

    def test_shrinking_an_existing_overage_is_allowed(self):
        self.write("memory-limits.json", json.dumps(
            {"default": 100, "include": ["ops/*.md"], "indexed_src": False, "rules": [], "files": {"ops/BRIEF.md": 100}}))
        self.assertEqual(self.commit_all("lower the limit").returncode, 0)
        self.write("ops/BRIEF.md", "b" * 120)
        self.assertEqual(self.commit_all("shrink").returncode, 0)

    def test_a_missing_guard_warns_and_lets_the_commit_through(self):
        copy = self.repo.parent / "mnemorph-copy" / "tools"
        copy.mkdir(parents=True)
        for name in ("memory.py", "size_guard.py"):
            shutil.copy(TOOLS / name, copy / name)
        r = subprocess.run([sys.executable, str(copy / "size_guard.py"), "install"], cwd=self.repo,
                           env=GIT_ENV, text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        shutil.rmtree(copy.parent)
        self.write("ops/BRIEF.md", "b" * 160)
        r = self.commit_all("grow without a guard")
        self.assertEqual(r.returncode, 0)
        self.assertIn("this commit was not checked", r.stderr)

    def test_install_needs_valid_limits(self):
        (self.repo / "memory-limits.json").write_text("{}")
        r = subprocess.run([sys.executable, str(TOOLS / "size_guard.py"), "install"], cwd=self.repo,
                           env=GIT_ENV, text=True, capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("refusing to install", r.stderr)


if __name__ == "__main__":
    unittest.main()
