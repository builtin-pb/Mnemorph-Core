"""Exercise the push guard with real `git push` into temporary bare repositories."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PUSH_GUARD = Path(__file__).resolve().parents[1] / "push_guard.py"

# Isolate every git call from the user's own global/system config so the
# tests behave the same on any machine and never touch real credentials.
GIT_ENV = dict(os.environ)
GIT_ENV.update({
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Push Guard Tests",
    "GIT_AUTHOR_EMAIL": "push-guard@example.invalid",
    "GIT_COMMITTER_NAME": "Push Guard Tests",
    "GIT_COMMITTER_EMAIL": "push-guard@example.invalid",
})


def run_git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=str(cwd), env=GIT_ENV,
                       text=True, capture_output=True)
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} in {cwd} failed ({r.returncode}):\n"
                             f"stdout: {r.stdout}\nstderr: {r.stderr}")
    return r


def run_guard(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(PUSH_GUARD), *args], cwd=str(cwd),
                          env=GIT_ENV, text=True, capture_output=True)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class PushGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.local = self.base / "local"
        run_git(self.base, "init", "-q", "-b", "main", str(self.local))

    def bare(self, name: str) -> Path:
        path = self.base / name
        run_git(self.base, "init", "-q", "--bare", "-b", "main", str(path))
        return path

    def commit(self, subject: str) -> str:
        run_git(self.local, "add", "-A")
        run_git(self.local, "commit", "-q", "-m", subject)
        return run_git(self.local, "rev-parse", "HEAD").stdout.strip()

    def install(self, *remotes: str) -> subprocess.CompletedProcess:
        r = run_guard(self.local, "install", *remotes)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    # -- install ------------------------------------------------------------

    def test_install_places_executable_hook_in_common_dir(self):
        out = self.install()
        hook = self.local / ".git" / "hooks" / "pre-push"
        self.assertTrue(hook.exists())
        self.assertTrue(os.access(hook, os.X_OK))
        self.assertIn("Mnemorph push guard", hook.read_text(encoding="utf-8"))
        self.assertIn(str(hook), out.stdout)
        self.assertIn("(none)", out.stdout)

    def test_install_records_private_remotes_without_duplicates(self):
        self.install("origin", "origin")
        self.install("origin")
        r = run_git(self.local, "config", "--get-all", "mnemorph.private")
        self.assertEqual(r.stdout.split(), ["origin"])

    def test_install_refuses_to_clobber_a_foreign_hook(self):
        hook = self.local / ".git" / "hooks" / "pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        foreign = "#!/bin/sh\nexit 0\n"
        write(hook, foreign)
        hook.chmod(0o755)
        r = run_guard(self.local, "install")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(str(hook), r.stderr)
        self.assertEqual(hook.read_text(encoding="utf-8"), foreign)  # untouched

    def test_reinstalling_over_its_own_copy_is_fine(self):
        self.install()
        r = self.install("origin")  # second call, now naming a remote too
        self.assertIn("origin", r.stdout)

    # -- the hook -------------------------------------------------------------

    def test_core_only_history_passes_to_a_guarded_remote(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        write(self.local / "tools" / "thing.py", "print('core')\n")
        self.commit("Add Core tool")
        self.install()
        fork = self.bare("fork.git")
        run_git(self.local, "remote", "add", "fork", str(fork))
        r = run_git(self.local, "push", "fork", "main", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        local_head = run_git(self.local, "rev-parse", "main").stdout.strip()
        remote_head = run_git(fork, "rev-parse", "main").stdout.strip()
        self.assertEqual(local_head, remote_head)

    def test_a_commit_adding_a_personal_path_is_refused(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        self.commit("Add Core file")
        write(self.local / "src" / "personal" / "x.md", "notes\n")
        self.commit("Add personal note")
        self.install()
        fork = self.bare("fork.git")
        run_git(self.local, "remote", "add", "fork", str(fork))
        r = run_git(self.local, "push", "fork", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("fork", r.stderr)
        self.assertIn("src/personal/x.md", r.stderr)
        self.assertIn("Add personal note", r.stderr)
        self.assertIn("install", r.stderr)  # tells how to mark it private
        self.assertEqual(run_git(fork, "rev-parse", "--verify", "main",
                                 check=False).returncode, 128)  # nothing landed

    def test_same_push_to_a_remote_marked_private_passes(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        self.commit("Add Core file")
        write(self.local / "src" / "personal" / "x.md", "notes\n")
        self.commit("Add personal note")
        priv = self.bare("private.git")
        run_git(self.local, "remote", "add", "priv", str(priv))
        self.install("priv")
        r = run_git(self.local, "push", "priv", "main", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        local_head = run_git(self.local, "rev-parse", "main").stdout.strip()
        remote_head = run_git(priv, "rev-parse", "main").stdout.strip()
        self.assertEqual(local_head, remote_head)

    def test_an_older_personal_commit_is_refused_even_when_the_tip_is_clean(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        self.commit("Add Core file")
        write(self.local / "src" / "personal" / "x.md", "notes\n")
        self.commit("Add personal note")
        (self.local / "src" / "personal" / "x.md").unlink()
        self.commit("Remove personal note; tip is Core-only again")
        self.install()
        pub = self.bare("pub.git")
        run_git(self.local, "remote", "add", "pub", str(pub))
        r = run_git(self.local, "push", "pub", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("src/personal/x.md", r.stderr)
        self.assertIn("Add personal note", r.stderr)
        self.assertNotIn("Remove personal note", r.stderr)  # stopped at the first bad commit
        self.assertEqual(run_git(pub, "rev-parse", "--verify", "main",
                                 check=False).returncode, 128)

    def test_deletion_passes_unchecked(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        self.commit("Add Core file")
        self.install()
        fork = self.bare("fork.git")
        run_git(self.local, "remote", "add", "fork", str(fork))
        run_git(self.local, "checkout", "-q", "-b", "topic")
        write(self.local / "tools" / "topic.py", "print('topic')\n")
        self.commit("Add topic tool")
        run_git(self.local, "push", "fork", "topic")
        run_git(self.local, "checkout", "-q", "main")
        run_git(self.local, "branch", "-D", "topic")
        r = run_git(self.local, "push", "fork", "--delete", "topic", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(run_git(fork, "rev-parse", "--verify", "topic",
                                 check=False).returncode, 128)

    def test_worktree_pushes_are_guarded(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        self.commit("Add Core file")
        self.install()
        fork = self.bare("fork.git")
        run_git(self.local, "remote", "add", "fork", str(fork))
        worktree = self.base / "wt"
        run_git(self.local, "worktree", "add", "-q", "-b", "wtbranch", str(worktree))
        write(worktree / "src" / "personal" / "y.md", "notes\n")
        run_git(worktree, "add", "-A")
        run_git(worktree, "commit", "-q", "-m", "Add personal note from worktree")
        r = run_git(worktree, "push", "fork", "wtbranch", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("src/personal/y.md", r.stderr)


if __name__ == "__main__":
    unittest.main()
