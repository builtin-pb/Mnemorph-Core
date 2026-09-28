"""Exercise tools/timeout.py, the GNU-compatible timeout for macOS, against short real commands."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

TOOL = Path(__file__).parents[1] / "timeout.py"


def run(*args, **kw):
    start = time.monotonic()
    done = subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, **kw)
    return done.returncode, time.monotonic() - start, done.stderr


class TimeoutTests(unittest.TestCase):
    def test_statuses(self):
        self.assertEqual(run("5", "true")[0], 0)
        self.assertEqual(run("5", "sh", "-c", "exit 3")[0], 3)
        code, took, _ = run("0.3", "sleep", "5")
        self.assertEqual(code, 124)
        self.assertLess(took, 3)
        self.assertEqual(run("0.3", "no-such-command-for-timeout-test")[0], 127)
        self.assertEqual(run("bad", "true")[0], 125)
        self.assertEqual(run("0", "true")[0], 0)  # 0 disables the limit
        self.assertEqual(run("1m", "true")[0], 0)

    def test_signals(self):
        self.assertEqual(run("-s", "KILL", "0.3", "sleep", "5")[0], 137)
        self.assertEqual(run("-sINT", "0.3", "sleep", "5")[0], 124)
        self.assertEqual(run("--preserve-status", "0.3", "sleep", "5")[0], 143)
        code, took, _ = run("-k", "0.2", "0.3", "sh", "-c", "trap '' TERM; sleep 5")
        self.assertEqual(code, 124)
        self.assertLess(took, 3)

    def test_whole_group_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            leak = Path(tmp) / "leak"
            run("0.3", "sh", "-c", f"sleep 1; echo leaked > {leak}")
            time.sleep(1.5)
            self.assertFalse(leak.exists())


if __name__ == "__main__":
    unittest.main()
