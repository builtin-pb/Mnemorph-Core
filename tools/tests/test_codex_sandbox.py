"""Opt-in, model-free checks of the installed Codex sandbox's actual effects.

Run with MNEMORPH_TEST_CODEX_SANDBOX=1 outside another sandbox. Only fresh
temporary fixtures are accessed; no login, model request or host config is used.
"""

import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_replay as replay


class Options(unittest.TestCase):
    def test_unsealed_runs_keep_legacy_exclusions(self):
        with patch.object(replay, "enabled_mcp_servers", return_value=[]):
            flags = replay.isolation_options([])
        for flag in replay.TMP_EXCLUSIONS:
            self.assertIn(flag, flags)
        self.assertNotIn('default_permissions="replay_sealed"', flags)


@unittest.skipUnless(os.environ.get("MNEMORPH_TEST_CODEX_SANDBOX") == "1",
                     "set MNEMORPH_TEST_CODEX_SANDBOX=1 for native sandbox checks")
class NativeSandbox(unittest.TestCase):
    def test_profile_removes_temp_writes_and_preserves_other_access(self):
        self.assertIsNotNone(shutil.which("codex"))
        self.assertIn("TMPDIR", os.environ)
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="replay-test-") as first, \
                tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"], prefix="replay-test-") as second:
            base, tmpdir = Path(first).resolve(), Path(second).resolve()
            workspace, home, denied = (base / name for name in ("workspace", "codex-home", "denied"))
            for directory in (workspace, home, denied):
                directory.mkdir()
            (home / "config.toml").write_text("")
            for directory in (base, tmpdir, denied):
                (directory / "read.txt").write_text("fixture")
            env = dict(os.environ, CODEX_HOME=str(home))
            script = """import json, pathlib, sys
base, tmpdir = map(pathlib.Path, sys.argv[1:]); result = {}
for name, path in [('workspace_write', base/'workspace'/'write.txt'),
                   ('slash_tmp_write', base/'write.txt'), ('tmpdir_write', tmpdir/'write.txt')]:
    try:
        path.write_text('fixture'); path.unlink(); result[name] = 'ok'
    except OSError as error: result[name] = error.errno
for name, path in [('slash_tmp_read', base/'read.txt'), ('tmpdir_read', tmpdir/'read.txt'),
                   ('denied_read', base/'denied'/'read.txt')]:
    try: result[name] = path.read_text()
    except OSError as error: result[name] = error.errno
print(json.dumps(result))
"""
            child = [sys.executable, "-c", script, str(base), str(tmpdir)]

            def run(prefix):
                result = subprocess.run(prefix + child, cwd=workspace, env=env,
                                        text=True, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            direct = run([])
            self.assertTrue(all(value in ("ok", "fixture") for value in direct.values()), direct)
            candidate = replay.deny_options([str(denied)])
            # The prior profile inherited the temp grants despite legacy exclusions.
            baseline = candidate.copy()
            index = next(i for i, value in enumerate(baseline)
                         if value.startswith("permissions.replay_sealed.filesystem="))
            baseline[index] = 'permissions.replay_sealed.filesystem={' + json.dumps(str(denied)) + '="deny"}'
            # Match exec's default_permissions selection, including a copied
            # user config that still has a legacy sandbox_mode setting.
            sandbox = ["codex", "sandbox"]
            for config in ("", 'sandbox_mode = "workspace-write"\n'):
                with self.subTest(config=config):
                    (home / "config.toml").write_text(config)
                    before = run(sandbox + baseline + list(replay.TMP_EXCLUSIONS) + ["--"])
                    after = run(sandbox + candidate + ["--"])
                    for name in ("slash_tmp_write", "tmpdir_write"):
                        self.assertEqual(before[name], "ok", before)
                        self.assertIn(after[name], (errno.EPERM, errno.EACCES), after)
                    for result in (before, after):
                        self.assertEqual(result["workspace_write"], "ok", result)
                        self.assertEqual(result["slash_tmp_read"], "fixture", result)
                        self.assertEqual(result["tmpdir_read"], "fixture", result)
                        self.assertIn(result["denied_read"], (errno.EPERM, errno.EACCES), result)
            with patch.dict(os.environ, env):
                replay.check_denial([str(denied)])


if __name__ == "__main__":
    unittest.main()
