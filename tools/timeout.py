#!/usr/bin/env python3
"""A GNU-compatible `timeout` for macOS, which ships none.

Agents trained on Linux habitually run `timeout 60 cmd`, which fails with
"command not found" on macOS. `python3 tools/timeout.py --install` copies this
file to ~/.local/bin/timeout; that directory must be on PATH for the agents'
shells. Standard library only.

usage: timeout [-s SIGNAL] [-k DURATION] [--foreground] [--preserve-status]
               [-v] DURATION COMMAND [ARG]...

DURATION is a number with an optional unit s, m, h or d; 0 disables the limit.
On expiry COMMAND gets SIGNAL (TERM by default), and KILL after -k DURATION if it
is still running. Exit status: 124 if the time ran out (128+9 when the signal
was KILL, as in GNU), unless --preserve-status; 125 if timeout itself failed;
126 if COMMAND could not run; 127 if it was not found; otherwise COMMAND's own.
Without --foreground, COMMAND runs in its own process group and the whole group
is signalled, so a shell's children stop with it.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def fail(message: str) -> int:
    print(f"timeout: {message}", file=sys.stderr)
    return 125


def seconds(text: str) -> float:
    unit = text[-1:] if text[-1:] in UNITS else "s"
    number = float(text[:-1] if text[-1:] in UNITS else text)
    if number < 0:
        raise ValueError(text)
    return number * UNITS[unit]


def signal_number(text: str) -> int:
    if text.isdigit():
        return int(text)
    name = text.upper()
    return int(getattr(signal, name if name.startswith("SIG") else "SIG" + name))


def install() -> int:
    target = Path.home() / ".local" / "bin" / "timeout"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(__file__, target)
    target.chmod(0o755)
    print(f"installed {target} (a copy of {Path(__file__).resolve()})")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["--install"]:
        return install()
    sig, kill_after, foreground, preserve, verbose = signal.SIGTERM, None, False, False, False
    i = 0
    try:
        while i < len(argv) and argv[i].startswith("-") and argv[i] != "-":
            arg = argv[i]
            if arg == "--":
                i += 1
                break
            name, has_value, value = arg.partition("=")
            if not arg.startswith("--") and arg[:2] in ("-s", "-k") and len(arg) > 2:
                name, has_value, value = arg[:2], "=", arg[2:]  # -sKILL, -k5
            if name in ("-s", "--signal", "-k", "--kill-after"):
                if not has_value:
                    i += 1
                    value = argv[i]
                if name in ("-s", "--signal"):
                    sig = signal_number(value)
                else:
                    kill_after = seconds(value)
            elif arg == "--foreground":
                foreground = True
            elif arg == "--preserve-status":
                preserve = True
            elif arg in ("-v", "--verbose"):
                verbose = True
            elif arg in ("-h", "--help"):
                print(__doc__[__doc__.index("usage:"):].split("\n\n")[0])
                return 0
            else:
                return fail(f"unrecognized option '{arg}'")
            i += 1
        if len(argv) - i < 2:
            return fail("missing operand; usage: timeout [OPTION] DURATION COMMAND [ARG]...")
        limit = seconds(argv[i])
    except (ValueError, IndexError, AttributeError) as error:
        return fail(f"invalid argument: {error}")
    command = argv[i + 1:]

    try:
        child = subprocess.Popen(command, preexec_fn=None if foreground else os.setpgrp)
    except FileNotFoundError:
        print(f"timeout: failed to run command '{command[0]}': No such file or directory", file=sys.stderr)
        return 127
    except PermissionError:
        print(f"timeout: failed to run command '{command[0]}': Permission denied", file=sys.stderr)
        return 126

    def send(number: int) -> None:
        try:
            if foreground:
                child.send_signal(number)
            else:
                os.killpg(child.pid, number)
        except ProcessLookupError:
            pass

    for forwarded in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(forwarded, lambda number, frame: send(number))

    timed_out = False
    deadline = time.monotonic() + limit if limit > 0 else None
    while True:
        try:
            wait = None if deadline is None else max(0.0, deadline - time.monotonic())
            status = child.wait(timeout=wait)
            break
        except subprocess.TimeoutExpired:
            if timed_out:  # the kill-after period also ran out
                send(signal.SIGKILL)
                deadline = None
                continue
            timed_out = True
            if verbose:
                print(f"timeout: sending signal {signal.Signals(sig).name} to command '{command[0]}'", file=sys.stderr)
            send(sig)
            deadline = time.monotonic() + kill_after if kill_after else None

    if timed_out and not preserve:
        return 128 + 9 if sig == signal.SIGKILL else 124
    return 128 - status if status < 0 else status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
