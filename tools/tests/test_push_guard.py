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


def run_guard(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(PUSH_GUARD), *args], cwd=str(cwd),
                          env=GIT_ENV if env is None else env, text=True, capture_output=True)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_fake_gh(bin_dir: Path, visibility: str) -> None:
    """A stand-in `gh` on PATH so tests never touch the real GitHub CLI."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        f'echo \'{{"visibility": "{visibility}"}}\'\n',
        encoding="utf-8",
    )
    script.chmod(0o755)


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

    def verdict(self, commit: str, outcome: str, reviewer: str = "tester",
                note: str | None = None, accept_scan: bool = False) -> subprocess.CompletedProcess:
        args = ["verdict", commit, outcome, "--reviewer", reviewer]
        if note is not None:
            args += ["--note", note]
        if accept_scan:
            args.append("--accept-scan")
        return run_guard(self.local, *args)

    def mark_clean(self, commit: str, **kw) -> None:
        r = self.verdict(commit, "clean", **kw)
        self.assertEqual(r.returncode, 0, r.stderr)

    def write_terms(self, *terms: str) -> None:
        write(self.local / ".git" / "mnemorph-scrub" / "terms.txt", "\n".join(terms) + "\n")

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
        run_git(self.local, "remote", "add", "origin", str(self.bare("origin.git")))
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
        run_git(self.local, "remote", "add", "origin", str(self.bare("origin.git")))
        r = self.install("origin")  # second call, now naming a remote too
        self.assertIn("origin", r.stdout)

    def test_install_requires_the_named_remote_to_exist(self):
        r = run_guard(self.local, "install", "ghost")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ghost", r.stderr)
        pinned = run_git(self.local, "config", "--get", "mnemorph.privateurl.ghost", check=False)
        self.assertNotEqual(pinned.returncode, 0)  # never pinned

    # -- the hook -------------------------------------------------------------

    def test_core_only_history_passes_to_a_guarded_remote(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        write(self.local / "tools" / "thing.py", "print('core')\n")
        commit = self.commit("Add Core tool")
        self.install()
        self.mark_clean(commit)
        fork = self.bare("fork.git")
        run_git(self.local, "remote", "add", "fork", str(fork))
        r = run_git(self.local, "push", "fork", "main", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        local_head = run_git(self.local, "rev-parse", "main").stdout.strip()
        remote_head = run_git(fork, "rev-parse", "main").stdout.strip()
        self.assertEqual(local_head, remote_head)

    def test_a_commit_adding_a_personal_path_is_refused(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        core_commit = self.commit("Add Core file")
        write(self.local / "src" / "personal" / "x.md", "notes\n")
        self.commit("Add personal note")
        self.install()
        self.mark_clean(core_commit)  # the bad-path check, not the review gate, is under test here
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
        core_commit = self.commit("Add Core file")
        write(self.local / "src" / "personal" / "x.md", "notes\n")
        self.commit("Add personal note")
        (self.local / "src" / "personal" / "x.md").unlink()
        self.commit("Remove personal note; tip is Core-only again")
        self.install()
        self.mark_clean(core_commit)  # so the walk reaches the personal commit, not the review gate
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
        core_commit = self.commit("Add Core file")
        self.install()
        self.mark_clean(core_commit)
        fork = self.bare("fork.git")
        run_git(self.local, "remote", "add", "fork", str(fork))
        run_git(self.local, "checkout", "-q", "-b", "topic")
        write(self.local / "tools" / "topic.py", "print('topic')\n")
        topic_commit = self.commit("Add topic tool")
        self.mark_clean(topic_commit)
        run_git(self.local, "push", "fork", "topic")
        run_git(self.local, "checkout", "-q", "main")
        run_git(self.local, "branch", "-D", "topic")
        r = run_git(self.local, "push", "fork", "--delete", "topic", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(run_git(fork, "rev-parse", "--verify", "topic",
                                 check=False).returncode, 128)

    def test_worktree_pushes_are_guarded(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        core_commit = self.commit("Add Core file")
        self.install()
        self.mark_clean(core_commit)
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

    # -- the review gate ------------------------------------------------------

    def test_unreviewed_core_commit_is_refused(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        commit = self.commit("Add Core file")
        self.install()
        fork = self.bare("fork.git")
        run_git(self.local, "remote", "add", "fork", str(fork))
        r = run_git(self.local, "push", "fork", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no recorded clean review verdict", r.stderr)
        short = run_git(self.local, "rev-parse", "--short", commit).stdout.strip()
        self.assertIn(short, r.stderr)
        self.assertIn("push_guard.py review", r.stderr)
        self.assertEqual(run_git(fork, "rev-parse", "--verify", "main",
                                 check=False).returncode, 128)

    def test_blocked_verdict_refuses(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        commit = self.commit("Add Core file")
        self.install()
        r = self.verdict(commit, "blocked", reviewer="tester", note="needs a rewrite")
        self.assertEqual(r.returncode, 0, r.stderr)  # recording "blocked" is never refused
        fork = self.bare("fork.git")
        run_git(self.local, "remote", "add", "fork", str(fork))
        r = run_git(self.local, "push", "fork", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        short = run_git(self.local, "rev-parse", "--short", commit).stdout.strip()
        self.assertIn(short, r.stderr)

    def test_later_verdict_overrides_earlier(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        commit = self.commit("Add Core file")
        self.install()
        self.verdict(commit, "blocked", reviewer="r1")
        fork = self.bare("fork.git")
        run_git(self.local, "remote", "add", "fork", str(fork))
        r = run_git(self.local, "push", "fork", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.mark_clean(commit, reviewer="r2", note="reviewed again, it is fine")
        r = run_git(self.local, "push", "fork", "main", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_scan_hit_on_a_home_path_refuses_plain_clean_and_passes_with_accept_scan(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        write(self.local / "tools" / "thing.py",
              "# see /Users/alice/notes for background\nprint('core')\n")
        commit = self.commit("Add Core tool referencing a home path")
        r = self.verdict(commit, "clean", reviewer="tester")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("/Users/alice/", r.stderr)
        self.assertIn("home-path", r.stderr)
        self.mark_clean(commit, accept_scan=True)

    def test_scan_hit_on_a_real_looking_email_refuses_plain_clean_and_passes_with_accept_scan(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        write(self.local / "tools" / "thing.py",
              "# contact alice@gmail.com about this\nprint('core')\n")
        commit = self.commit("Add Core tool referencing an email")
        r = self.verdict(commit, "clean", reviewer="tester")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("alice@gmail.com", r.stderr)
        self.assertIn("email", r.stderr)
        self.mark_clean(commit, accept_scan=True)

    def test_scan_hit_on_a_terms_word_refuses_plain_clean_and_passes_with_accept_scan(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        self.write_terms("codename-falcon")
        write(self.local / "tools" / "thing.py",
              "# relates to codename-falcon\nprint('core')\n")
        commit = self.commit("Add Core tool mentioning a private term")
        r = self.verdict(commit, "clean", reviewer="tester")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("codename-falcon", r.stderr)
        self.assertIn("term", r.stderr)
        self.mark_clean(commit, accept_scan=True)

    def test_placeholder_paths_and_example_domains_do_not_hit(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        write(self.local / "tools" / "thing.py",
              "# see /Users/username/proj and /Users/<you>/proj\n"
              "# contact dev@example.com or bot@example.org or x@sample.invalid\n"
              "print('core')\n")
        commit = self.commit("Add Core tool with only placeholders")
        r = run_guard(self.local, "status", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hits=0", r.stdout)
        self.mark_clean(commit)  # plain "clean" succeeds: nothing to accept

    def test_review_writes_a_packet_with_brief_message_and_patch(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        write(self.local / "tools" / "thing.py", "print('core')\n")
        commit = self.commit("Add Core tool for review")
        r = run_guard(self.local, "review", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        packet_path = self.local / ".git" / "mnemorph-scrub" / "packets" / f"{commit}.md"
        self.assertIn(str(packet_path), r.stdout)
        self.assertIn("0 commit(s) already have a clean verdict", r.stdout)
        text = packet_path.read_text(encoding="utf-8")
        review_brief = (Path(__file__).resolve().parents[1] / "push_review.md").read_text(encoding="utf-8")
        self.assertIn(review_brief.strip().splitlines()[0], text)  # the brief is embedded
        self.assertIn("Add Core tool for review", text)  # the full commit message
        self.assertIn("+print('core')", text)  # the full patch

    def test_review_skips_already_clean_commits(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        commit = self.commit("Add Core file already reviewed")
        self.mark_clean(commit)
        r = run_guard(self.local, "review", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "1 commit(s) already have a clean verdict")
        packets_dir = self.local / ".git" / "mnemorph-scrub" / "packets"
        self.assertEqual(list(packets_dir.glob("*")), [])

    def test_verdict_from_worktree_is_seen_by_push_from_main_checkout(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        commit = self.commit("Add Core file")
        self.install()
        worktree = self.base / "wt-verdict"
        run_git(self.local, "worktree", "add", "-q", "-b", "wtbranch", str(worktree))
        r = run_guard(worktree, "verdict", commit, "clean", "--reviewer", "wt-reviewer")
        self.assertEqual(r.returncode, 0, r.stderr)
        fork = self.bare("fork.git")
        run_git(self.local, "remote", "add", "fork", str(fork))
        r = run_git(self.local, "push", "fork", "main", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)

    # -- private remotes are trusted by URL, not name --------------------------

    def test_a_repointed_remote_is_guarded(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        self.commit("Add Core file")
        trusted = self.bare("trusted.git")
        run_git(self.local, "remote", "add", "origin", str(trusted))
        self.install("origin")
        other = self.bare("other.git")
        run_git(self.local, "remote", "set-url", "origin", str(other))
        r = run_git(self.local, "push", "origin", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no recorded clean review verdict", r.stderr)  # guarded, not bypassed

    def test_a_pinned_private_remote_passes_even_unreviewed(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        self.commit("Add Core file")
        priv = self.bare("priv.git")
        run_git(self.local, "remote", "add", "priv", str(priv))
        self.install("priv")
        r = run_git(self.local, "push", "priv", "main", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)  # bypassed: no verdict needed at all

    def test_legacy_name_only_entry_is_guarded_with_a_repin_hint(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        self.commit("Add Core file")
        legacy = self.bare("legacy.git")
        run_git(self.local, "remote", "add", "legacy", str(legacy))
        self.install()  # installs the hook; pins nothing
        run_git(self.local, "config", "--add", "mnemorph.private", "legacy")  # old-style entry
        r = run_git(self.local, "push", "legacy", "main", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no pinned URL", r.stderr)
        self.assertIn("install legacy", r.stderr)

    def test_visibility_check_refuses_a_mocked_public_repo(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        self.commit("Add Core file")
        run_git(self.local, "remote", "add", "pub",
               "https://github.com/example-owner/example-repo.git")
        fake_bin = self.base / "fakebin"
        make_fake_gh(fake_bin, "PUBLIC")
        env = dict(GIT_ENV)
        env["PATH"] = f"{fake_bin}{os.pathsep}{GIT_ENV.get('PATH', '')}"
        r = run_guard(self.local, "install", "pub", env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PUBLIC", (r.stdout or "") + (r.stderr or ""))
        pinned = run_git(self.local, "config", "--get", "mnemorph.privateurl.pub", check=False)
        self.assertNotEqual(pinned.returncode, 0)  # never pinned

    # -- memory-overlap scan ----------------------------------------------------

    def test_memory_overlap_flags_an_eight_word_run_and_not_seven(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        write(self.local / "src" / "personal" / "diary.md",
              "alpha bravo charlie delta echo foxtrot golf hotel\n")
        self.commit("Add Core file and a personal diary")  # the memory source, not under test
        write(self.local / "tools" / "thing.py",
              "# alpha bravo charlie delta echo foxtrot golf hotel today\n"
              "# alpha bravo charlie delta echo foxtrot golf\n"
              "print('core')\n")
        commit = self.commit("Add Core tool")

        r = run_guard(self.local, "status", f"{commit}~1..{commit}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hits=1", r.stdout)  # only the 8-word run hits; the 7-word run does not

        r = self.verdict(commit, "clean", reviewer="tester")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("memory-overlap", r.stderr)
        self.assertIn("alpha bravo charlie delta echo foxtrot golf hotel", r.stderr)
        self.assertIn("diary.md", r.stderr)
        self.mark_clean(commit, accept_scan=True)

    def test_core_checkout_without_memory_skips_the_overlap_check(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        write(self.local / "src" / "core" / "note.md",
              "the quick brown fox jumps over the lazy dog in the meadow\n")
        write(self.local / "tools" / "thing.py",
              "# the quick brown fox jumps over the lazy dog\nprint('core')\n")
        self.commit("Add Core tool echoing Core's own memory text")
        r = run_guard(self.local, "status", "HEAD")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hits=0", r.stdout)  # src/core/ is never memory, so nothing to compare

    # -- annotated tags -----------------------------------------------------

    def test_annotated_tag_message_is_scanned_and_gates_the_tagged_commit(self):
        write(self.local / "AGENTS.md", "# Mnemorph\n")
        commit = self.commit("Add Core file")
        self.install()
        fork = self.bare("fork.git")
        run_git(self.local, "remote", "add", "fork", str(fork))
        run_git(self.local, "tag", "-a", "v1", "-m",
               "Release notes mention alice@gmail.com directly")

        # unreviewed: refused like any commit, even though it only arrives via a tag
        r = run_git(self.local, "push", "fork", "v1", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no recorded clean review verdict", r.stderr)

        # the commit's own content is clean (0 hits), but the tag's message
        # has a scan hit, and it must gate the same commit
        r = run_guard(self.local, "status", "HEAD")
        self.assertIn("hits=0", r.stdout)  # confirms the hit below comes from the tag, not the commit
        self.mark_clean(commit)
        r = run_git(self.local, "push", "fork", "v1", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("has 1 mechanical scan hit(s) not accepted", r.stderr)

        # accepting the scan (which covers the tag message too) lets it through
        self.mark_clean(commit, accept_scan=True)
        r = run_git(self.local, "push", "fork", "v1", check=False)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
