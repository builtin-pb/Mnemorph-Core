#!/usr/bin/env python3
"""Type into another terminal agent's session, for supervising a CLI agent from another session.

The target is a tmux session (preferred) or a GNU screen session with that name.

  session_input.py send --session NAME "MESSAGE"   type MESSAGE literally, then press Enter
  session_input.py keys --session NAME KEY...      press named keys only, for dialogs

Keys: Enter, Escape, Tab, BTab, Up, Down, Left, Right and single digits.
Text typed into an open dialog is lost, and its Enter answers the dialog. So for a tmux target,
send first reads the screen and refuses while a numbered choice ("❯ 1. ...", or a rating row
such as "1: Bad    2: Fine") or a "Do you want to" question shows, or when the screen cannot be
read; answer the dialog with keys, or pass --force. Screen targets are not read.
Exit codes: 2 for usage or a missing session, 3 when tmux or screen fails (including an unreadable
pane), 4 when a dialog is open.
"""
import argparse
import re
import subprocess
import sys
import time

CHUNK = 300  # screen drops long `stuff` strings; the same chunks keep tmux input paced
KEY = re.compile(r"^(Enter|Escape|Tab|BTab|Up|Down|Left|Right|[0-9])$")
SCREEN_KEYS = {"Enter": "\r", "Escape": "\x1b", "Tab": "\t", "Up": "\x1b[A", "Down": "\x1b[B",
               "Right": "\x1b[C", "Left": "\x1b[D"}


def output(cmd):
    """Return (succeeded, stdout); a missing program counts as failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return False, ""
    return r.returncode == 0, r.stdout


def run(cmd):
    return output(cmd)[0]


def backend(name):
    if run(["tmux", "has-session", "-t", f"={name}"]):
        return "tmux"
    if re.search(rf"\.{re.escape(name)}\s", output(["screen", "-ls"])[1]):
        return "screen"
    return None


def tmux(name, *args):
    if not run(["tmux", "send-keys", "-t", f"={name}:", *args]):
        sys.exit(3)


def screen(name, text):
    if not run(["screen", "-S", name, "-p", "0", "-X", "stuff", text]):
        sys.exit(3)


DIALOG = re.compile(r"❯\s*\d+\.\s|Do you want to |(?:^|\s)\d: \S+ +\d: ")


def open_dialog(name):
    """Return the dialog line visible in a tmux pane's last 30 lines, None, or False when unreadable."""
    ok, text = output(["tmux", "capture-pane", "-p", "-t", f"={name}:"])
    if not ok:
        return False
    for line in text.splitlines()[-30:]:
        if DIALOG.search(line):
            return line.strip()
    return None


def send(name, kind, message):
    for k in range(0, len(message), CHUNK):
        part = message[k:k + CHUNK]
        tmux(name, "-l", "--", part) if kind == "tmux" else screen(name, part)
        time.sleep(0.3)
    time.sleep(0.5)
    tmux(name, "Enter") if kind == "tmux" else screen(name, "\r")
    print(f"sent {len(message)} chars to {kind} {name}")


def keys(name, kind, names):
    for k in names:
        if kind == "tmux":
            tmux(name, k)
        elif k in SCREEN_KEYS or k.isdigit():
            screen(name, SCREEN_KEYS.get(k, k))
        else:
            sys.exit(f"key {k} is not supported in screen")
        time.sleep(0.4)
    print(f"pressed {' '.join(names)} in {kind} {name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("--session", required=True)
    s.add_argument("message")
    s.add_argument("--force", action="store_true", help="send even while a dialog shows")
    k = sub.add_parser("keys")
    k.add_argument("--session", required=True)
    k.add_argument("key", nargs="+")
    a = ap.parse_args()
    if a.cmd == "send" and (not a.message or "\n" in a.message):
        print("send takes one non-empty line", file=sys.stderr)
        sys.exit(2)
    if a.cmd == "keys" and not all(KEY.match(x) for x in a.key):
        print("keys: only " + KEY.pattern, file=sys.stderr)
        sys.exit(2)
    kind = backend(a.session)
    if kind is None:
        print(f"no tmux or screen session named {a.session}", file=sys.stderr)
        sys.exit(2)
    if a.cmd == "send" and kind == "tmux" and not a.force:
        line = open_dialog(a.session)
        if line is False:
            print(f"not sent: cannot read the screen of {a.session}; check it, or pass --force", file=sys.stderr)
            sys.exit(3)
        if line:
            print(f"not sent: a dialog is open in {a.session}: {line!r}; answer it with keys, or pass --force",
                  file=sys.stderr)
            sys.exit(4)
    send(a.session, kind, a.message) if a.cmd == "send" else keys(a.session, kind, a.key)


if __name__ == "__main__":
    main()
