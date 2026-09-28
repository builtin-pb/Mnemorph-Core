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
        self.refused("batch", self.prompt, "--out", self.out, "--runner", self.prompt,
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
    def test_canary_then_rest(self):
        jobs = self.home / "jobs.txt"
        common = f"--root {self.root} --commit {self.base} --prompt-file {self.prompt}"
        jobs.write_text(f"# comment\none {common}\ntwo {common} --effort high\n")
        self.ok(self.replay("batch", jobs, "--out", self.out, "--parallel", 1))
        status = [json.loads(line) for line in (self.out / "status.jsonl").read_text().splitlines()]
        done = status[-1]
        self.assertEqual((done["event"], done["ok"], done["failed"]), ("done", 2, 0))
        self.assertEqual(json.loads((self.out / "two" / "manifest.json").read_text())["effort"], "high")
        r = self.ok(self.replay("batch", jobs, "--out", self.out))  # resumable
        self.assertIn("nothing to run", r.stdout)

    def test_claude_batch_stops_on_failed_canary(self):
        jobs = self.home / "jobs.txt"
        common = f"--root {self.root} --commit {self.base} --prompt-file {self.prompt}"
        jobs.write_text(f"one {common}\ntwo {common}\n")
        r = self.replay("batch", jobs, "--out", self.out, "--tool", "claude")  # no token
        self.assertEqual(r.returncode, 1)
        done = json.loads((self.out / "status.jsonl").read_text().splitlines()[-1])
        self.assertEqual((done["failed"], done["skipped"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
