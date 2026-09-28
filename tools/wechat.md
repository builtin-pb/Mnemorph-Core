# Reading your own local WeChat 4.x messages

These tools let you read your own Mac WeChat 4.x message history locally:
`wechat_reader.py` is a read-only SQLCipher reader, `wechat.py` builds a
private reading copy from it and browses conversations, and `wechat_keys.py`
captures the database keys WeChat derives when it opens your account. None of
them send messages, modify WeChat's data, or run a background service.

**Risk, stated plainly:** capturing keys means pausing your own WeChat process
under a debugger for up to a few minutes. This is generally detectable by
anti-tampering checks, and account risk from doing it is not zero. Only do
this to an account you own, and only if you accept that risk. Skipping key
capture is fine if you already have a `keys.json` from another trusted source.

## Prerequisites

- An Apple Silicon Mac running WeChat 4.x, with Xcode Command Line Tools
  installed (`xcode-select --install`) so `/usr/bin/lldb` exists.
- Python 3.14 or newer (for the standard-library `compression.zstd` module)
  with the `sqlcipher3` package installed in that interpreter.
- A private working directory for a temporary app copy and for your key file
  and reading copies; keep all of it under `.mnemorph-local/` (git-ignored).

## Capturing keys

1. Quit WeChat from its own menu (not a force-quit), so its database files
   are in a consistent, unlocked state.
2. Copy the whole `WeChat.app` bundle and your account's `db_storage` folder
   from `~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/`
   into a private, mode-0700 directory. This copy is both your capture target
   and your rollback point if anything looks wrong afterward.
3. Ad hoc-sign only the copy, never the original:
   `codesign --force --deep --sign - /path/to/copy/WeChat.app`
4. Run the capture against that copy, pointing `--db-root` at the *copied*
   `db_storage` folder so verification never touches the live one. The
   output directory must be private and the file new:
   ```sh
   mkdir -p -m 700 .mnemorph-local/wechat
   /usr/bin/python3 -I tools/wechat_keys.py \
     --exe "/path/to/copy/WeChat.app/Contents/MacOS/WeChat" \
     --db-root /path/to/copy/db_storage \
     --out .mnemorph-local/wechat/keys.json
   ```
   It launches only that one copy, never attaches to an already-running
   process, reads only as many bytes as the matched key requires, verifies
   every candidate key against a SQLCipher page HMAC before saving it, and
   never prints or logs a key. Log in on the copy (scanning a QR code) if it
   asks; the tool reports each key as it is captured and verified, and exits
   once your required databases are all covered or it times out.
5. Once it finishes, quit the copy from its own menu, then rename or delete
   its app bundle so it can't be reopened by mistake.
6. Reopen the real `/Applications/WeChat.app` and confirm your account works
   normally.
7. Confirm the original was never touched:
   `codesign --verify --deep --strict /Applications/WeChat.app`, and confirm
   the saved keys still open your databases read-only with
   `python3 tools/wechat_reader.py verify`.

Re-run capture only when keys stop working (for example after a WeChat
update); routine reading never needs the debugger again.

## Snapshotting and browsing

`wechat_reader.py` looks for exactly one WeChat account folder under the
standard container path and for its keys under `.mnemorph-local/wechat/`;
pass `--db-root`/`--key-file` to override either. Build a private, bounded
reading copy with your own verified account ID:

```sh
python3 tools/wechat.py snapshot --self-id YOUR_WECHAT_ID \
  --since 2026-01-01T00:00:00 --output .mnemorph-local/wechat/recent.sqlite
```

`--until` is exclusive. The output file is created mode 0600 and the command
refuses to overwrite an existing path; rerun to a new path for fresh data.
Browsing then needs only the standard library:

```sh
python3 tools/wechat.py info --db .mnemorph-local/wechat/recent.sqlite
python3 tools/wechat.py chats --db .mnemorph-local/wechat/recent.sqlite
python3 tools/wechat.py roster --db .mnemorph-local/wechat/recent.sqlite --chat EXACT_CHAT_ID
python3 tools/wechat.py read --db .mnemorph-local/wechat/recent.sqlite --chat EXACT_CHAT_ID --limit 200
python3 tools/wechat.py search --db .mnemorph-local/wechat/recent.sqlite 'some phrase'
python3 tools/wechat.py around --db .mnemorph-local/wechat/recent.sqlite --id 123 --radius 10
```

`read`/`search` include both sides of a conversation; `own`/`--own` filters to
your own messages. `--json` gives machine-readable rows; `--compact` groups
plain-text output by conversation and day. `--timezone` changes display only
(it defaults to your Mac's own local zone); use explicit UTC offsets in
`--since`/`--until` filters. `--self-label` sets the word used for your own
messages in text output (default `me`). Recover one original row, including
text inside an available forwarded record, without trusting the snapshot
copy:

```sh
python3 tools/wechat.py source --db .mnemorph-local/wechat/recent.sqlite --id 123
```

## Limits

- Only messages actually stored on this Mac are covered; history that exists
  only on another device is a separate, unaddressed gap.
- Images, voice, video, stickers, locations and calls always show as a
  placeholder (e.g. `[image]`), never a transcription or description.
- A forwarded "chat history" share shows the text of items whose payload
  includes it; other item types inside the same forward (photos, files, and
  so on) remain placeholders. A preview title alone is not the full record.
- Shared contact cards show only nickname, account ID and alias.
- Credential-shaped text (verification codes, short replies right after a
  password-style question) is heuristically redacted. This can hide a
  message that wasn't actually a secret, and it cannot catch every secret.

## Verification

```sh
python3 -m unittest discover -s tools/tests -p 'test_wechat*.py'
```
