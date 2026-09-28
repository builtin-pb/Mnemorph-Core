#!/usr/bin/env python3
"""Read-only local reader for Mac WeChat 4.x SQLCipher databases.

No debugger, network service, message sending, or database modification.
Every connection opens `mode=ro` with `PRAGMA query_only=ON`; SQLite may still
use shared-memory locking to read committed WAL rows. Each database shard gets
its own read transaction; there is no single instant shared across shards.
Keys are accepted only from a private local file this user owns with mode
0600 (see `wechat_keys.py` for how to obtain one). Run with a Python that has
`sqlcipher3` installed; text decoding also needs the standard-library
`compression.zstd` module (Python 3.14+).
"""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

try:
    from compression import zstd
except ImportError:
    zstd = None


class _ZstdErrorFallback(Exception):
    """Stand-in so the decode() except clause is always well-formed, even
    when compression.zstd is unavailable (pre-3.14 Python)."""


ZstdError = zstd.ZstdError if zstd is not None else _ZstdErrorFallback

try:
    from sqlcipher3 import dbapi2 as _sqlcipher
    SQLCIPHER_AVAILABLE = True
except ImportError:
    _sqlcipher = None
    SQLCIPHER_AVAILABLE = False


class DatabaseError(Exception):
    """Raised for SQLCipher/SQLite failures. Aliases the real driver's
    exception when `sqlcipher3` is installed, so callers can always catch
    this name regardless of whether the dependency is present."""


if SQLCIPHER_AVAILABLE:
    DatabaseError = _sqlcipher.DatabaseError  # noqa: F811 - intentional alias


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEYS = ROOT / '.mnemorph-local' / 'wechat' / 'keys.json'

_MISSING_SQLCIPHER = (
    "sqlcipher3 is not installed; install it (e.g. `pip install sqlcipher3-binary`) "
    "to read WeChat databases"
)


def default_db_root():
    """The standard WeChat 4.x database container for this Mac account.

    WeChat stores each signed-in account's databases under a `wxid_...`
    folder inside this fixed container path. If exactly one such folder
    exists, use its `db_storage` directory. With zero or several accounts
    present there is no safe default; pass `--db-root` to choose one.
    """
    base = Path.home() / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
    if not base.is_dir():
        raise ValueError(f'WeChat 4.x container not found at {base}; pass --db-root explicitly')
    candidates = sorted(p for p in base.glob('wxid_*') if (p / 'db_storage').is_dir())
    if len(candidates) == 1:
        return candidates[0] / 'db_storage'
    if not candidates:
        raise ValueError(f'no WeChat account folder found under {base}; pass --db-root explicitly')
    names = ', '.join(p.name for p in candidates)
    raise ValueError(f'multiple WeChat accounts found ({names}); pass --db-root to choose one')


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return {'base64': base64.b64encode(value).decode('ascii')} if isinstance(value, bytes) else value


def decode(raw, sender):
    if raw is None:
        return None, 'null'
    if isinstance(raw, str):
        value = raw
    elif isinstance(raw, bytes):
        try:
            if raw.startswith(b'\x28\xb5\x2f\xfd'):
                if zstd is None:
                    return None, 'compression_zstd_unavailable_need_python_3_14'
                # Bounded streaming output prevents oversized compressed data.
                import io
                with zstd.ZstdFile(io.BytesIO(raw)) as stream:
                    raw = stream.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    return None, 'decoded_payload_too_large'
            value = raw.decode('utf-8')
        except (UnicodeDecodeError, ZstdError, EOFError):
            return None, 'binary_or_missing_compression_dictionary'
    else:
        return None, 'unexpected_type'
    if sender and value.startswith(sender + ':\n'):
        value = value[len(sender) + 2:]
    return value, 'decoded'


class Reader:
    def __init__(self, root, key_file):
        self.root = root.resolve(strict=True)
        if key_file.is_symlink():
            raise ValueError('key-file symlink rejected')
        info = key_file.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('key file must belong to this user with mode 0600')
        data = json.loads(key_file.read_text())
        if data.get('db_root') and Path(data['db_root']).resolve() != self.root:
            raise ValueError('key manifest belongs to a different database root')
        self.keys = data['keys']

    @contextmanager
    def open(self, path):
        if not SQLCIPHER_AVAILABLE:
            raise ValueError(_MISSING_SQLCIPHER)
        relative = str(path.relative_to(self.root))
        if path.is_symlink():
            raise ValueError('database symlink rejected')
        entry = self.keys.get(relative)
        if not isinstance(entry, dict) or not re.fullmatch('[0-9a-fA-F]{64}', entry.get('enc_key', '')):
            raise ValueError('missing verified key: ' + relative)
        with path.open('rb') as stream:
            salt = stream.read(16).hex()
        if entry.get('salt') and salt != entry['salt']:
            raise ValueError('database salt changed; revalidate keys: ' + relative)
        db = _sqlcipher.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)
        db.row_factory = _sqlcipher.Row
        try:
            db.execute('PRAGMA key="x\'' + entry['enc_key'] + '\'"')
            db.execute('PRAGMA cipher_compatibility=4')
            db.execute('PRAGMA query_only=ON')
            db.execute('SELECT count(*) FROM sqlite_master').fetchone()
            db.execute('BEGIN')
            yield db
        finally:
            db.close()

    def verify(self):
        result = []
        for path in sorted(self.root.rglob('*.db')):
            name = str(path.relative_to(self.root))
            try:
                with self.open(path) as db:
                    tables = db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
                    readonly = db.execute('PRAGMA query_only').fetchone()[0] == 1
                result.append({'database': name, 'verified': True, 'table_count': tables, 'query_only': readonly})
            except (ValueError, OSError, DatabaseError) as error:
                result.append({'database': name, 'verified': False, 'error': str(error)})
        return {'checked_at': utc_now(), 'databases': result,
                'verified': sum(r['verified'] for r in result), 'total': len(result)}

    def groups(self, name_filter=''):
        with self.open(self.root / 'contact/contact.db') as db:
            rows = db.execute("SELECT username,nick_name,remark FROM contact WHERE username LIKE '%@chatroom'").fetchall()
        needle = name_filter.casefold()
        return [dict(row) for row in rows if not needle or
                any(needle in str(row[key] or '').casefold() for key in row.keys())]

    def read(self, chat_id, limit, since=0):
        table = 'Msg_' + hashlib.md5(chat_id.encode()).hexdigest()
        messages, sources, errors = [], [], []
        paths = sorted((self.root / 'message').glob('message_[0-9]*.db'))
        if not paths:
            raise ValueError('no message shards found; source is unavailable')
        expected = {name for name in self.keys
                    if re.fullmatch(r'message/message_[0-9]+\.db', name)}
        present = {str(path.relative_to(self.root)) for path in paths}
        for name in sorted(expected - present):
            errors.append({'database': name, 'error': 'previously keyed message shard is missing'})
        for path in paths:
            try:
                with self.open(path) as db:
                    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                        continue
                    columns = {r[1] for r in db.execute('PRAGMA table_info("' + table + '")')}
                    if not {'local_id','create_time','message_content','real_sender_id'} <= columns:
                        raise ValueError('unsupported message schema')
                    scope = db.execute('SELECT count(*),min(create_time),max(create_time) FROM "' + table +
                                       '" WHERE create_time>=?', (since,)).fetchone()
                    senders = dict(db.execute('SELECT rowid,user_name FROM Name2Id').fetchall())
                    rows = db.execute('SELECT rowid AS _source_rowid,* FROM "' + table +
                                      '" WHERE create_time>=? ORDER BY create_time DESC,local_id DESC LIMIT ?',
                                      (since,limit)).fetchall()
                    sources.append({'database': path.name, 'scoped_rows': scope[0],
                                    'first_timestamp': scope[1], 'latest_timestamp': scope[2],
                                    'wal_present': Path(str(path) + '-wal').exists()})
                    for row in rows:
                        sender = senders.get(row['real_sender_id'])
                        text, encoding = decode(row['message_content'], sender)
                        messages.append({'source_database': path.name, 'source_table': table,
                                         'sender_id': sender, 'text': text, 'decoding': encoding,
                                         'row': {key: encode(row[key]) for key in row.keys()}})
            except (ValueError, OSError, DatabaseError) as error:
                errors.append({'database': path.name, 'error': str(error)})
        messages.sort(key=lambda m: (m['row']['create_time'], m['source_database'], m['row']['local_id']))
        selected = messages[-limit:]
        return {'source': 'Mac WeChat local DB + committed WAL', 'queried_at': utc_now(),
                'chat_id': chat_id, 'since_timestamp': since, 'limit': limit,
                'messages': selected, 'undecodable_in_returned_messages': sum(m['text'] is None for m in selected),
                'sources': sources, 'errors': errors, 'complete_shard_access': not errors,
                'history_scope': 'Only messages stored on this Mac; other-device-only history is separate.',
                'snapshot_scope': 'Separate read transaction per database shard.'}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db-root', type=Path, help='defaults to the sole WeChat account folder found on this Mac')
    ap.add_argument('--key-file', type=Path, default=DEFAULT_KEYS)
    ap.add_argument('--output', type=Path, help='New private JSON file; otherwise prints requested results')
    sub = ap.add_subparsers(dest='command', required=True)
    sub.add_parser('verify')
    groups = sub.add_parser('groups')
    groups.add_argument('--filter', default='')
    read = sub.add_parser('read')
    read.add_argument('--chat-id', required=True)
    read.add_argument('--limit', type=int, default=30)
    read.add_argument('--since', type=int, default=0, help='Inclusive Unix seconds')
    args = ap.parse_args()
    root = args.db_root or default_db_root()
    reader = Reader(root, args.key_file)
    if args.command == 'verify':
        value = reader.verify()
        status = 0 if value['total'] and value['verified'] == value['total'] else 2
    elif args.command == 'groups':
        value = {'queried_at': utc_now(), 'groups': reader.groups(args.filter)}
        status = 0
    else:
        if not 1 <= args.limit <= 200 or args.since < 0:
            ap.error('limit must be 1..200 and since must be nonnegative')
        value = reader.read(args.chat_id, args.limit, args.since)
        status = 0 if value['complete_shard_access'] else 2
    if args.output:
        os.umask(0o077)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        print(json.dumps({'output': str(args.output.resolve()), 'status': status}))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    return status


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError, DatabaseError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        raise SystemExit(2)
