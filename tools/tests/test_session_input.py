"""Exercise session_input's checks and the commands it sends, with tmux and screen faked."""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("session_input", Path(__file__).parents[1] / "session_input.py")
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class SessionInputTests(unittest.TestCase):
    def call(self, argv, sessions=("tmux",), pane=""):
        calls = []

        def fake(cmd):
            calls.append(cmd)
            if cmd[:2] == ["tmux", "has-session"]:
                return "tmux" in sessions, ""
            if cmd[:2] == ["tmux", "capture-pane"]:
                return True, pane
            if cmd == ["screen", "-ls"]:
                return True, "\t123.agent\t(Attached)\n" if "screen" in sessions else ""
            return True, ""

        out = io.StringIO()
        with mock.patch.object(tool, "output", fake), mock.patch.object(tool.time, "sleep"), \
                mock.patch.object(tool.sys, "argv", ["session_input.py", *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                tool.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, calls

    def test_send_tmux_types_literally_then_enter(self):
        code, calls = self.call(["send", "--session", "agent", "x" * 650])
        self.assertEqual(code, 0)
        sent = [c for c in calls if c[:2] == ["tmux", "send-keys"]]
        self.assertEqual([c[-1] for c in sent], ["x" * 300, "x" * 300, "x" * 50, "Enter"])
        self.assertTrue(all(c[3] == "=agent:" for c in sent))
        self.assertTrue(all("-l" in c for c in sent[:-1]))

    def test_keys_screen_fallback(self):
        code, calls = self.call(["keys", "--session", "agent", "Down", "1", "Enter"], sessions=("screen",))
        self.assertEqual(code, 0)
        self.assertEqual([c[-1] for c in calls if c[0] == "screen" and "stuff" in c], ["\x1b[B", "1", "\r"])

    def test_refusals(self):
        self.assertEqual(self.call(["keys", "--session", "agent", "rm -rf"])[0], 2)
        self.assertEqual(self.call(["send", "--session", "agent", "a\nb"])[0], 2)
        self.assertEqual(self.call(["send", "--session", "agent", "hi"], sessions=())[0], 2)

    def test_send_refuses_open_dialog(self):
        pane = "Edit file\n Do you want to make this edit?\n \u276f 1. Yes\n   2. No\n"
        code, calls = self.call(["send", "--session", "agent", "hi"], pane=pane)
        self.assertEqual(code, 4)
        self.assertFalse([c for c in calls if c[:2] == ["tmux", "send-keys"]])
        code, calls = self.call(["send", "--session", "agent", "--force", "hi"], pane=pane)
        self.assertEqual(code, 0)
        code, _ = self.call(["send", "--session", "agent", "hi"], pane="ready\n\u276f \n  \u276f \u25ef helper  compiling\n")
        self.assertEqual(code, 0)

    def test_missing_program_is_failure(self):
        with mock.patch.object(tool.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(tool.output(["tmux", "ls"]), (False, ""))


if __name__ == "__main__":
    unittest.main()
