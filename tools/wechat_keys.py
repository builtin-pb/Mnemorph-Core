#!/usr/bin/python3
"""Capture Mac WeChat 4.x database keys from one private app copy under LLDB (Apple Silicon).

Launches only the given executable (an ad hoc-signed copy, never the app in
/Applications), breaks on CommonCrypto's PBKDF2, matches each call's salt to a
selected database, bounds every memory read, and saves only keys that verify
against that database's SQLCipher page HMAC, to a mode-0600 file in a private
directory. It never attaches to a running process, writes process memory,
evaluates expressions, signs apps, kills processes, prints keys or opens a
network service; the app itself keeps its ordinary network behaviour, and
breakpoints pause it. Run with Apple's Python and -I (`/usr/bin/python3 -I`).
See wechat.md for the whole procedure and its risk.

Adapted from the capture component of a community WeChat key toolkit, used
under its license:

  MIT License. Copyright (c) 2026 Contributors.
  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:
  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.
  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
  SOFTWARE.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import time


def emit(kind, **fields):
    print(json.dumps(dict(type=kind, **fields)), flush=True)


def valid_key(key, page):
    if len(key) != 32 or len(page) != 4096:
        return False
    hkey = hashlib.pbkdf2_hmac('sha512', key,
                             bytes(x ^ 0x3a for x in page[:16]), 2, 32)
    mac = hmac.new(hkey, page[16:4032] + b'\x01\x00\x00\x00', hashlib.sha512)
    return hmac.compare_digest(mac.digest(), page[4032:])


def save_keys(path, root, found):
    # The output's parent is a new private directory. Atomic publication keeps
    # readers from seeing a truncated manifest; no key is printed.
    tmp = path.with_name(path.name + '.new')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump({'version': 1, 'db_root': str(root), 'keys': found}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--exe', type=Path, required=True)
    ap.add_argument('--db-root', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--timeout', type=int, default=240)
    ap.add_argument('--settle', type=int, default=25)
    args = ap.parse_args()
    if platform.machine() != 'arm64' or os.geteuid() == 0:
        raise RuntimeError('native arm64 and a non-root user are required')
    if not 10 <= args.timeout <= 300 or not 5 <= args.settle <= 60:
        raise RuntimeError('timeout or settle interval out of bounds')
    exe = args.exe.resolve(strict=True)
    root = args.db_root.resolve(strict=True)
    out = args.out.absolute()
    if out.exists() or out.is_symlink():
        raise RuntimeError('output already exists')
    if out.parent.is_symlink() or out.parent.stat().st_uid != os.getuid():
        raise RuntimeError('output parent must be owned by this user')
    if stat.S_IMODE(out.parent.stat().st_mode) & 0o077:
        raise RuntimeError('output parent must be private (0700)')
    if str(exe).startswith('/Applications/'):
        raise RuntimeError('refusing to debug the installed app')
    pages = {}
    for db in sorted(root.rglob('*.db')):
        if db.is_symlink():
            raise RuntimeError('database symlink rejected')
        with db.open('rb') as stream:
            page = stream.read(4096)
        if len(page) == 4096 and not page.startswith(b'SQLite format 3\0'):
            pages[str(db.relative_to(root))] = page
    if not pages:
        raise RuntimeError('no encrypted databases to verify')
    salts = {page[:16] for page in pages.values()}
    macsalts = {bytes(x ^ 0x3a for x in salt): salt for salt in salts}
    required = {p for p in pages if p == 'contact/contact.db' or
                (p.startswith('message/message_') and p.endswith('.db'))}
    if not required:
        required = set(pages)  # synthetic fixture
    lldb_path = subprocess.check_output(['/usr/bin/lldb', '-P'], text=True).strip()
    sys.path.insert(0, lldb_path)
    import lldb
    os.umask(0o077)
    debugger = lldb.SBDebugger.Create(False)
    debugger.SkipLLDBInitFiles(True)
    debugger.SetAsync(True)
    command_result = lldb.SBCommandReturnObject()
    debugger.GetCommandInterpreter().HandleCommand(
        'settings set target.load-script-from-symbol-file false', command_result)
    if not command_result.Succeeded():
        raise RuntimeError('could not disable symbol-file scripts')
    listener = debugger.GetListener()
    target = debugger.CreateTarget(str(exe))
    if not target.IsValid() or not target.GetTriple().startswith('arm64'):
        raise RuntimeError('invalid ARM64 target')
    bp = target.BreakpointCreateByName('CCKeyDerivationPBKDF')
    launch = lldb.SBLaunchInfo([])
    launch.AddSuppressFileAction(0, True, False)
    launch.AddSuppressFileAction(1, False, True)
    launch.AddSuppressFileAction(2, False, True)
    launch.SetWorkingDirectory(str(exe.parent))
    # Keep ASLR enabled; avoid the debugger's default disable-ASLR launch flag.
    launch.SetLaunchFlags(launch.GetLaunchFlags() & ~lldb.eLaunchFlagDisableASLR)
    process = None
    found = {}
    counts = {'pbkdf_calls': 0, 'salt_matches': 0}
    last_found = None
    error = lldb.SBError()
    try:
        process = target.Launch(launch, error)
        if error.Fail() or not process.IsValid():
            emit('launch_failed', reason=error.GetCString())
            return 2
        emit('launched', pid=process.GetProcessID(), databases=len(pages),
             breakpoint_locations=bp.GetNumLocations())
        deadline = time.monotonic() + args.timeout
        last_report = time.monotonic()

        def memory(addr, size):
            err = lldb.SBError()
            data = process.ReadMemory(addr, size, err)
            return bytes(data) if err.Success() and len(data) == size else b''

        while time.monotonic() < deadline:
            event = lldb.SBEvent()
            if listener.WaitForEvent(1, event) and lldb.SBProcess.EventIsProcessEvent(event):
                state = lldb.SBProcess.GetStateFromEvent(event)
                if state in (lldb.eStateExited, lldb.eStateCrashed, lldb.eStateDetached):
                    emit('child_ended', state=state)
                    break
                if state == lldb.eStateStopped:
                    for thread in process:
                        reason = thread.GetStopReason()
                        if reason in (lldb.eStopReasonSignal, lldb.eStopReasonException):
                            raise RuntimeError('child stopped for signal or exception')
                        if reason != lldb.eStopReasonBreakpoint:
                            continue
                        if thread.GetStopReasonDataCount() < 2 or thread.GetStopReasonDataAtIndex(0) != bp.GetID():
                            continue
                        counts['pbkdf_calls'] += 1
                        frame = thread.GetFrameAtIndex(0)
                        reg = lambda n: frame.FindRegister('x' + str(n)).GetValueAsUnsigned()
                        alg, ptr, length, saltptr, saltlen, prf, rounds = [reg(i) for i in range(7)]
                        if not (alg == 2 and saltlen == 16 and 0 < length <= 256
                                and prf == 5 and rounds in (2, 256000)):
                            continue
                        saltarg = memory(saltptr, 16)
                        salt = saltarg if rounds == 256000 else macsalts.get(saltarg)
                        if salt not in salts:
                            continue
                        counts['salt_matches'] += 1
                        secret = memory(ptr, length)
                        if len(secret) != length:
                            continue
                        key = (hashlib.pbkdf2_hmac('sha512', secret, salt, 256000, 32)
                               if rounds == 256000 else secret)
                        changed = False
                        for name, page in pages.items():
                            if name not in found and page[:16] == salt and valid_key(key, page):
                                found[name] = {'enc_key': key.hex(), 'salt': salt.hex()}
                                changed = True
                        if changed:
                            save_keys(out, root, found)
                            last_found = time.monotonic()
                            emit('keys_verified', count=len(found), total=len(pages), **counts)
                    if process.Continue().Fail():
                        raise RuntimeError('could not resume child')
            if len(found) == len(pages) or (required <= found.keys() and last_found is not None
                    and time.monotonic() - last_found >= args.settle):
                break
            if time.monotonic() - last_report >= 15:
                emit('waiting', count=len(found), required_missing=len(required - found.keys()), **counts)
                last_report = time.monotonic()
        emit('finished', verified=len(found), total=len(pages),
             required_missing=sorted(required - found.keys()), **counts)
        return 0 if required <= found.keys() else 3
    finally:
        if process is not None and process.IsValid() and process.GetState() not in (
                lldb.eStateExited, lldb.eStateDetached, lldb.eStateInvalid):
            if process.GetState() == lldb.eStateRunning:
                if process.Stop().Fail():
                    raise RuntimeError('could not stop child for clean detach; inspect LLDB child')
            target.DeleteAllBreakpoints()
            error = process.Detach(False)
            emit('detached', pid=process.GetProcessID(), success=error.Success())
            if error.Fail():
                raise RuntimeError('detach failed; child needs manual recovery')
        lldb.SBDebugger.Destroy(debugger)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        emit('capture_error', reason=str(error))
        raise SystemExit(2)
