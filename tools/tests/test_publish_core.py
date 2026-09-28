"""Exercise publish_core.py against a temporary bare "public" repository and a private clone."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PUBLISH = Path(__file__).resolve().parents[1] / "publish_core.py"

ENV = dict(os.environ)
ENV.update({
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Publish Tests",
    "GIT_AUTHOR_EMAIL": "publish@example.invalid",
    "GIT_COMMITTER_NAME": "Publish Tests",
    "GIT_COMMITTER_EMAIL": "publish@example.invalid",
    "MNEMORPH_PUBLISH_QUIET": "1",
    "MNEMORPH_PUBLISH_CHECKS": "0",
    # Blocks any patch that contains SECRET; accepts everything else.
    "MNEMORPH_PUBLISH_REVIEWER": (
        f"{sys.executable} -c \"import sys, json; p = sys.stdin.read().split('## Patch', 1)[1]; "
        "print(json.dumps({'verdict': 'blocked' if 'SECRET' in p else 'clean', "
        "'accept_scan': True, 'note': 'test', 'particulars': []}))\""
    ),
})


def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, env=ENV, text=True, capture_output=True)
    if r.returncode:
        raise AssertionError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


def commit(repo: Path, path: str, text: str, message: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class PublishCoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.public = root / "public.git"
        git(root, "init", "-q", "--bare", "-b", "main", str(self.public))
        seed = root / "seed"
        git(root, "init", "-q", "-b", "main", str(seed))
        commit(seed, "tools/a.py", "a = 1\n", "Seed Core")
        git(seed, "push", "-q", str(self.public), "main")
        self.private = root / "private"
        git(root, "clone", "-q", "-o", "upstream", str(self.public), str(self.private))
        self.start = git(self.private, "rev-parse", "HEAD")
        git(self.private, "config", "mnemorph.publishFrom", self.start)

    def run_publish(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(PUBLISH), *args], cwd=self.private, env=ENV,
                              text=True, capture_output=True)

    def public_log(self) -> list[str]:
        return git(self.public, "log", "--format=%s", "main").splitlines()

    def state(self) -> dict:
        return json.loads((self.private / ".git" / "mnemorph-publish" / "state.json").read_text())

    def test_publishes_core_commits_in_order_and_skips_memory(self):
        commit(self.private, "tools/a.py", "a = 2\n", "Change a")
        commit(self.private, "src/personal/notes.md", "private\n", "Private note")
        commit(self.private, "tools/b.py", "b = 1\n", "Add b")
        r = self.run_publish("run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.public_log(), ["Add b", "Change a", "Seed Core"])
        self.assertNotIn("src/personal/notes.md", git(self.public, "ls-tree", "-r", "--name-only", "main"))
        self.assertEqual(len(self.state()["published"]), 2)
        again = self.run_publish("run")
        self.assertIn("nothing to publish", again.stdout)

    def test_blocked_commit_is_folded_with_its_fix(self):
        commit(self.private, "tools/a.py", "a = 'SECRET'\n", "Leak")
        r = self.run_publish("run")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.public_log(), ["Seed Core"])
        commit(self.private, "tools/a.py", "a = 3\n", "Remove the leak")
        r = self.run_publish("run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.public_log()[0], "Publish 2 instance commits together")
        self.assertEqual(git(self.public, "show", "main:tools/a.py"), "a = 3")
        self.assertNotIn("SECRET", git(self.public, "log", "-p", "main"))

    def test_mixed_commit_publishes_only_its_core_part(self):
        (self.private / "tools").mkdir(exist_ok=True)
        (self.private / "src").mkdir(exist_ok=True)
        (self.private / "tools/c.py").write_text("c = 1\n")
        (self.private / "src/x.md").write_text("x\n")
        git(self.private, "add", "tools/c.py", "src/x.md")
        git(self.private, "commit", "-q", "-m", "Mixed")
        r = self.run_publish("run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        files = git(self.public, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertIn("tools/c.py", files)
        self.assertNotIn("src/x.md", files)

    def test_conflict_with_public_core_stops_without_pushing(self):
        other = self.private.parent / "other"
        git(self.private.parent, "clone", "-q", str(self.public), str(other))
        commit(other, "tools/a.py", "a = 'theirs'\n", "Public change")
        git(other, "push", "-q", "origin", "main")
        commit(self.private, "tools/a.py", "a = 'ours'\n", "Private change")
        r = self.run_publish("run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("does not apply on public Core", r.stdout)
        self.assertEqual(self.public_log(), ["Public change", "Seed Core"])

    def test_a_rerun_reuses_recorded_verdicts(self):
        commit(self.private, "tools/a.py", "a = 5\n", "Change a")
        counter = self.private.parent / "reviews"
        env_reviewer = ENV["MNEMORPH_PUBLISH_REVIEWER"]
        ENV["MNEMORPH_PUBLISH_REVIEWER"] = f"echo x >> {counter}; " + env_reviewer
        self.addCleanup(ENV.__setitem__, "MNEMORPH_PUBLISH_REVIEWER", env_reviewer)
        self.assertEqual(self.run_publish("run", "--dry-run").returncode, 0)
        self.assertEqual(self.run_publish("run", "--dry-run").returncode, 0)
        self.assertEqual(counter.read_text().count("x"), 1)

    def test_dry_run_reviews_without_pushing(self):
        commit(self.private, "tools/a.py", "a = 4\n", "Change a")
        r = self.run_publish("run", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("dry run: would push 1 commit(s)", r.stdout)
        self.assertEqual(self.public_log(), ["Seed Core"])
        self.assertEqual(self.state()["published"], {})


if __name__ == "__main__":
    unittest.main()
