"""Check that a Codex replay command keeps the run inside its copy."""

import subprocess
import sys
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "codex_replay.py"


class DryRun(unittest.TestCase):
    def test_command_disables_outside_access(self):
        out = subprocess.run([sys.executable, str(TOOL), "--commit", "HEAD",
                              "--prompt-file", "/dev/null", "--out", "/tmp/unused",
                              "--dry-run"], text=True, capture_output=True, check=True).stdout
        for flag in ("--ephemeral", "-s workspace-write", 'approval_policy="never"',
                     "--disable apps", "--disable plugins", "--disable computer_use",
                     "--disable browser_use", "--disable in_app_browser",
                     "exclude_slash_tmp=true", "exclude_tmpdir_env_var=true"):
            self.assertIn(flag, out)
        self.assertNotIn("danger", out)


if __name__ == "__main__":
    unittest.main()
