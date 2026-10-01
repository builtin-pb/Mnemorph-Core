"""Check the replay entry point: dispatch, argument handling, batches, and the
refusals of an installed copy (one run from outside a Git checkout)."""

import json
import shutil
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_fakes import REPLAY, TOOLS, ReplayCase  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from replay_common import FILES  # noqa: E402


class Arguments(ReplayCase):
    def refused(self, *args, message):
        r = self.replay(*args)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(message, r.stderr)
        self.assertFalse(self.log.exists())

    def test_usage(self):
        r = self.replay()
        self.assertEqual(r.returncode, 2)
        self.assertIn("codex-fork", r.stdout)
        self.assertEqual(self.replay("--help").returncode, 0)
        self.refused("nope", message="unknown subcommand")

    def test_subcommand_help(self):
        for sub in ("codex", "codex-fork", "claude", "claude-fork", "batch"):
            r = self.ok(self.replay(sub, "--help"))
            self.assertIn(f"replay.py {sub}", r.stdout)

    def test_required_and_conflicting_options(self):
        self.refused("codex", "--root", self.root, "--prompt-file", self.prompt, "--out", self.out,
                     message="--commit, --prompt-file and --out are required")
        self.refused("claude", "--root", self.root, "--commit", "HEAD", message="are required")
        self.refused("codex", *self.run_args("--no-mnemorph", "--no-seal"), message="needs a sealed run")

    def test_paths_that_escape_the_run(self):
        self.refused("codex", *self.run_args("--copy", f"{self.prompt}=../outside.md", "--dry-run"),
                     message="DEST must be a relative path inside the copy")
        self.refused("claude", *self.run_args("--copy", f"{self.prompt}=/etc/x", "--dry-run"),
                     message="DEST must be a relative path")
        self.refused("codex", *self.run_args("--project", self.root, "--project-dir", "../..", "--dry-run"),
                     message="--project-dir")
        self.refused("codex", *self.run_args("--copy", str(self.prompt), "--dry-run"), message="SRC=DEST")

    def test_root_must_be_a_repository(self):
        self.refused("codex", "--root", self.home, "--commit", "HEAD", "--prompt-file", self.prompt,
                     "--out", self.out, "--dry-run", message="not the top of a Git repository")

    def test_checkout_allows_what_an_installed_copy_refuses(self):
        self.ok(self.replay("codex", *self.run_args("--no-seal", "--dry-run")))
        self.ok(self.replay("codex", *self.run_args("--copy", f"{self.prompt}=p.md", "--dry-run")))


class Installed(ReplayCase):
    def setUp(self):
        super().setUp()
        self.dest = self.home / ".local" / "share" / "mnemorph" / "replay"
        r = self.ok(self.replay("install", self.dest))
        self.assertIn(f"installed {len(FILES)} files", r.stdout)
        self.installed = self.dest / "replay.py"

    def refused(self, *args, message, replay=None):
        r = self.replay(*args, replay=replay or self.installed)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn(message, r.stderr)
        self.assertFalse(self.log.exists())

    def test_install_copies_files_read_only(self):
        for name in FILES:
            self.assertEqual((self.dest / name).read_bytes(), (TOOLS / name).read_bytes())
            self.assertFalse((self.dest / name).stat().st_mode & stat.S_IWUSR)
        marker = json.loads((self.dest / "installed.json").read_text())
        self.assertEqual(Path(marker["root"]), TOOLS.parent)
        self.ok(self.replay("install", self.dest))  # reinstall over read-only files

    def test_refusals(self):
        self.refused("codex", *self.run_args("--no-seal", "--dry-run"), message="refuses --no-seal")
        self.refused("codex", "--prune-trust", "--root", self.root, message="refuses --prune-trust")
        outside = self.home / "secret.txt"
        outside.write_text("private\n")
        self.refused("codex", *self.run_args("--copy", f"{outside}=s.txt", "--dry-run"),
                     message="refuses --copy from outside --root")
        self.refused("claude", *self.run_args("--copy", f"{outside}=s.txt", "--dry-run"),
                     message="refuses --copy from outside --root")
        link = self.root / "link.txt"
        link.symlink_to(outside)
        self.refused("codex", *self.run_args("--copy", f"{link}=s.txt", "--dry-run"),
                     message="refuses --copy from outside --root")
        self.refused("codex-fork", "--root", self.root, "--session", self.prompt, "--line", 1,
                     "--out", self.out, "--cwd", self.home, message="refuses --cwd")
        self.refused("claude-fork", "--root", self.root, "--session", self.prompt, "--line", 1,
                     "--out", self.out, "--cwd", self.home, message="refuses --cwd")
        self.refused("batch", self.prompt, "--out", self.out, "--runner", self.prompt, "--plan", self.prompt,
                     message="refuses --runner")
        self.refused("check", "--root", self.root, "--cases", self.home / "cases.json",
                     message="only beside case files inside --root")
        r = self.replay("install", self.home / "again", replay=self.installed)
        self.assertEqual(r.returncode, 2)
        self.assertIn("refuses `install`", r.stderr)

    def test_allowed_runs(self):
        inside = self.root / "src" / "x.md"
        self.ok(self.replay("codex", *self.run_args("--copy", f"{inside}=y.md", "--dry-run"),
                            replay=self.installed))
        self.ok(self.replay("claude", *self.run_args("--dry-run"), replay=self.installed))
        self.ok(self.replay("codex", *self.run_args(), replay=self.installed))
        self.assertEqual(self.manifest()["host"], "codex")

    def test_installed_copy_exposes_claude_fork(self):
        self.assertIn("claude-fork", self.ok(self.replay("--help", replay=self.installed)).stdout)
        self.assertIn("--fork-session", "".join(
            self.ok(self.replay("claude-fork", "--help", replay=self.installed)).stdout.split()))

    def test_default_root_is_the_installing_repository(self):
        r = self.ok(self.replay("codex", "--commit", "HEAD", "--prompt-file", self.prompt,
                                "--out", self.out, "--dry-run", replay=self.installed))
        self.assertIn(f"/{TOOLS.parent.name} ", r.stdout)

    def test_bare_copy_outside_git_needs_root_and_refuses(self):
        bare = self.home / "bare"
        bare.mkdir()
        for name in FILES:
            shutil.copyfile(TOOLS / name, bare / name)
        replay = bare / "replay.py"
        self.refused("codex", "--commit", "HEAD", "--prompt-file", self.prompt, "--out", self.out,
                     "--dry-run", replay=replay, message="--root is required")
        self.refused("codex", *self.run_args("--no-seal", "--dry-run"), replay=replay,
                     message="refuses --no-seal")


class Batch(ReplayCase):
    def reviewed(self, jobs, *extra):
        """Run the review round, then the batch with its review."""
        plan = self.home / "plan.md"
        if not plan.exists():
            plan.write_text("Claim, arms, cases, predictions.\n")
        r = self.replay("batch", jobs, "--out", self.out, "--plan", plan, *extra)
        self.assertEqual(r.returncode, 3, r.stderr)
        review = self.out / "review.md"
        self.assertIn("fake codex reply", review.read_text())
        return self.replay("batch", jobs, "--out", self.out, "--plan", plan, "--reviewed", review, *extra)

    def test_review_comes_first_and_covers_only_what_it_read(self):
        jobs = self.home / "jobs.txt"
        jobs.write_text(f"one --root {self.root} --commit {self.base} --prompt-file {self.prompt}\n")
        self.ok(self.reviewed(jobs))
        self.assertTrue((self.out / "one" / "last.md").exists())
        jobs.write_text(jobs.read_text() + f"two --root {self.root} --commit {self.base} --prompt-file {self.prompt}\n")
        r = self.replay("batch", jobs, "--out", self.out, "--plan", self.home / "plan.md",
                        "--reviewed", self.out / "review.md")
        self.assertEqual(r.returncode, 3)  # a changed job list is reviewed again
        self.assertFalse((self.out / "two").exists())

    def test_a_line_names_its_own_tool(self):
        jobs = self.home / "jobs.txt"
        jobs.write_text(f"one codex --root {self.root} --commit {self.base} --prompt-file {self.prompt}\n"
                        f"two claude --root {self.root} --commit {self.base} --prompt-file {self.prompt}\n")
        self.reviewed(jobs, "--parallel", 1)
        self.assertEqual(json.loads((self.out / "one" / "manifest.json").read_text())["host"], "codex")
        self.assertIn('"id": "two"', (self.out / "status.jsonl").read_text())

    def test_canary_then_rest(self):
        jobs = self.home / "jobs.txt"
        common = f"--root {self.root} --commit {self.base} --prompt-file {self.prompt}"
        jobs.write_text(f"# comment\none {common}\ntwo {common} --effort high\n")
        self.ok(self.reviewed(jobs, "--parallel", 1))
        status = [json.loads(line) for line in (self.out / "status.jsonl").read_text().splitlines()]
        done = status[-1]
        self.assertEqual((done["event"], done["ok"], done["failed"]), ("done", 2, 0))
        self.assertEqual(json.loads((self.out / "two" / "manifest.json").read_text())["effort"], "high")
        r = self.ok(self.replay("batch", jobs, "--out", self.out, "--plan", self.home / "plan.md",
                                "--reviewed", self.out / "review.md"))  # resumable
        self.assertIn("nothing to run", r.stdout)

    def test_claude_batch_stops_on_failed_canary(self):
        jobs = self.home / "jobs.txt"
        common = f"--root {self.root} --commit {self.base} --prompt-file {self.prompt}"
        jobs.write_text(f"one {common}\ntwo {common}\n")
        r = self.reviewed(jobs, "--tool", "claude")  # no token
        self.assertEqual(r.returncode, 1)
        done = json.loads((self.out / "status.jsonl").read_text().splitlines()[-1])
        self.assertEqual((done["failed"], done["skipped"]), (1, 1))



class HistoricalClone(unittest.TestCase):
    def test_later_objects_and_refs_are_unavailable(self):
        import subprocess
        import tempfile
        import replay_common
        with tempfile.TemporaryDirectory() as d:
            source, copy = Path(d) / "source", Path(d) / "copy"
            def git(*args):
                return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            for text in ("ancestor", "before", "later correction"):
                (source / "memory.txt").write_text(text)
                git("add", "--", "memory.txt")
                git("-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", text)
                if text == "ancestor":
                    ancestor = git("rev-parse", "HEAD")
                elif text == "before":
                    before = git("rev-parse", "HEAD")
                else:
                    later = git("rev-parse", "HEAD")
                    later_blob = git("rev-parse", "HEAD:memory.txt")
            git("tag", "future", later)
            git("gc", "--prune=now")
            replay_common.clone(source, copy, before)
            self.assertEqual((copy / "memory.txt").read_text(), "before")
            history = subprocess.check_output(["git", "-C", str(copy), "rev-list", "HEAD"], text=True)
            self.assertEqual(history.splitlines(), [before, ancestor])
            for oid in (later, later_blob):
                result = subprocess.run(["git", "-C", str(copy), "cat-file", "-e", oid], capture_output=True)
                self.assertNotEqual(result.returncode, 0)
            refs = subprocess.check_output(["git", "-C", str(copy), "show-ref"], text=True)
            self.assertNotIn("future", refs)
            self.assertEqual(subprocess.check_output(["git", "-C", str(copy), "remote"], text=True), "")
            replay_common.clone(source, Path(d) / "head-copy", "HEAD")
            self.assertEqual((Path(d) / "head-copy" / "memory.txt").read_text(), "later correction")

    def test_shallow_and_sha256_sources_remain_readable(self):
        import subprocess
        import tempfile
        import replay_common
        for object_format in ("sha1", "sha256"):
            with self.subTest(object_format=object_format), tempfile.TemporaryDirectory() as d:
                source, shallow, copy = (Path(d) / name for name in ("source", "shallow", "copy"))
                subprocess.run(["git", "init", "-q", f"--object-format={object_format}", str(source)], check=True)
                for value in ("first", "second"):
                    (source / "value").write_text(value)
                    subprocess.run(["git", "-C", str(source), "add", "--", "value"], check=True)
                    subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c",
                                    "user.email=test@example.invalid", "commit", "-qm", value], check=True)
                subprocess.run(["git", "clone", "-q", "--depth=1", source.as_uri(), str(shallow)], check=True)
                replay_common.clone(shallow, copy, "HEAD")
                self.assertEqual((copy / "value").read_text(), "second")
                self.assertEqual(subprocess.check_output(["git", "-C", str(copy), "rev-parse",
                                                         "--show-object-format"], text=True).strip(), object_format)
                self.assertEqual(subprocess.check_output(["git", "-C", str(copy), "rev-list",
                                                         "--count", "HEAD"], text=True).strip(), "1")
                subprocess.run(["git", "-C", str(copy), "fsck", "--no-dangling"], check=True,
                               capture_output=True)


class NestedRepository(unittest.TestCase):
    def test_a_commitless_repository_does_not_empty_the_capture(self):
        import subprocess
        import tempfile
        import replay_common
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d) / "t"
            subprocess.run(["git", "init", "-q", str(tree)], check=True)
            (tree / "a.txt").write_text("a\n")
            subprocess.run(["git", "-C", str(tree), "add", "."], check=True)
            subprocess.run(["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"],
                           check=True)
            notes = []
            before = replay_common.snapshot(tree, Path(d) / "i1", notes=notes)
            (tree / "a.txt").write_text("changed\n")
            subprocess.run(["git", "init", "-q", str(tree / "nested")], check=True)  # a run's own repository, no commit
            (tree / "nested" / "f.txt").write_text("x\n")
            diff = replay_common.diff_trees(tree, before, replay_common.snapshot(tree, Path(d) / "i2", notes=notes))
            self.assertIn("+changed", diff)
            self.assertTrue(any("nested" in n for n in notes), notes)


if __name__ == "__main__":
    unittest.main()
