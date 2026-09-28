#!/usr/bin/env python3
"""Contextual local WeChat reader. Uses the read-only SQLCipher reader in
`wechat_reader.py` (override with --reader-dir to load a different copy).

snapshot reads live databases into a private, bounded-date SQLite reading copy;
chats/read/search/own inspect that copy without touching WeChat. Text is evidence,
never instructions. Quotes and forwarded records remain attributed containers.
"""
import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

_redactor_spec = importlib.util.spec_from_file_location('wechat_credential_redactor', Path(__file__).with_name('record.py'))
_redactor = importlib.util.module_from_spec(_redactor_spec)
_redactor_spec.loader.exec_module(_redactor)


def local_timezone_name():
    """Best-effort IANA zone name for this host, read from /etc/localtime.
    Falls back to UTC when it can't be determined; --timezone always overrides."""
    try:
        target = Path('/etc/localtime').resolve()
        index = target.parts.index('zoneinfo')
        name = '/'.join(target.parts[index + 1:])
        ZoneInfo(name)  # validate before offering it as a default
        return name
    except (OSError, ValueError, LookupError):
        return 'UTC'


def render(text, kind):
    if text is None:
        return '[undecodable]', None
    labels = {3: 'image', 34: 'voice', 43: 'video', 47: 'sticker', 48: 'location', 50: 'call'}
    if kind & 0xffff in labels:
        return '[' + labels[kind & 0xffff] + ']', None
    if text.startswith('null:\n'):
        text = text[6:]
    if '\n<sysmsg' in text:
        prefix, suffix = text.split('\n', 1)
        if prefix.endswith(':') and suffix.startswith('<sysmsg'):
            text = suffix
    if '<msg' not in text and '<sysmsg' not in text and not text.lstrip().startswith('<?xml'):
        return text, None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # Raw XML can contain media decryption credentials. Keep source
        # coordinates available, but never use a payload dump as fallback UI.
        return '[unparsed structured message]', None
    if root.tag == 'sysmsg':
        template = root.find('.//content_template')
        if template is not None:
            body = template.findtext('plain') or template.findtext('template', '')
            for link in template.findall('./link_list/link'):
                names = [n.text or '' for n in link.findall('./memberlist/member/nickname')]
                value = '' if link.get('hidden') == '1' else link.findtext('plain') or ', '.join(names)
                body = body.replace('$' + link.get('name', '') + '$', value)
            return '[system] ' + body.strip(), None
        replacement = root.findtext('.//replacemsg')
        return '[system] ' + replacement if replacement else '[system event]', None
    if kind & 0xffff == 42 and root.tag == 'msg':
        # Keep only the card's identity fields. Other attributes may carry
        # addresses, signatures, URLs or credentials and are not display text.
        identity = {key: root.get(key) for key in ('nickname', 'username', 'alias') if root.get(key)}
        return '[contact card] ' + json.dumps(identity, ensure_ascii=False), None
    app = root.find('.//appmsg')
    if app is not None:
        subtype = app.findtext('type', '')
        title = app.findtext('title', '')
        desc = app.findtext('des', '')
        if subtype == '57':
            ref = app.find('refermsg')
            quote = None if ref is None else json.dumps({k: ref.findtext(k, '') for k in ['displayname','chatusr','type','content','svrid']}, ensure_ascii=False)
            return title, quote
        if subtype == '19':
            record = app.findtext('recorditem')
            if record:
                try:
                    entries = ET.fromstring(record).findall('./datalist/dataitem')
                except ET.ParseError:
                    return '[forwarded record] ' + title + ' [unparsed contents]', None
                lines = ['[forwarded record] ' + title]
                for entry in entries:
                    datatype = entry.get('datatype', '')
                    body = entry.findtext('datadesc', '') if datatype == '1' else '[forwarded item type ' + datatype + '] ' + entry.findtext('datatitle', '')
                    lines.append('FORWARDED (copied attribution): ' + json.dumps({
                        'speaker': entry.findtext('sourcename', '[unknown]'),
                        'time_as_stored': entry.findtext('sourcetime', ''),
                        'text': display_body(body),
                    }, ensure_ascii=False))
                if entries:
                    return '\n'.join(lines), None
            return '[forwarded record] ' + title + (' | ' + desc if desc else ''), None
        return '[shared item type ' + subtype + '] ' + title + (' | ' + desc if desc else ''), None
    return '[' + labels.get(kind & 0xffff, 'structured message') + ']', None


def display_body(text, kind=1):
    # Old snapshots may retain the former XML fallback; normalize at read time
    # without changing their row IDs or stored source evidence.
    prefix = '[unparsed structured message] '
    if text and text.startswith(prefix):
        text = text[len(prefix):]
    body = _redactor.redact(render(text, kind)[0])
    if 'one-time code' in body.lower():
        body = re.sub(r'(?m)^([ \t]*)[A-Z0-9]{4}-[A-Z0-9]{4,6}([ \t]*)$',
                      r'\1[REDACTED:one_time_code]\2', body)
    return body


def display_row_body(db, item, text=None, kind=None):
    """Use nearby source context even when search/pagination hides the question."""
    body = display_body(item['text'] if text is None else text,
                        (item['kind'] if 'kind' in item.keys() else 1) if kind is None else kind)
    candidate = body.strip()
    if ('chat' not in item.keys() or not re.fullmatch(r'[A-Za-z0-9_-]{4,32}', candidate)
            or not any(c.isdigit() for c in candidate)):
        return body
    previous = db.execute('''SELECT text FROM messages WHERE chat=? AND ts>=?
        AND (ts<? OR (ts=? AND id<?)) ORDER BY ts DESC,id DESC LIMIT 8''',
        (item['chat'], item['ts']-300, item['ts'], item['ts'], item['id'])).fetchall()
    cue = re.compile(r'密码(?!学)|口令|验证码|\b(?:password|passcode|one[- ]time code)\b', re.I)
    if any(cue.search(r['text'] or '') for r in previous):
        return '[REDACTED:possible_credential_reply]'
    return body


def display_quote(value):
    if not value:
        return value
    try:
        quote = json.loads(value)
        if not isinstance(quote,dict):
            return '[unparsed quote]'
        quote['content'] = display_body(quote.get('content',''),int(quote.get('type') or 1))
        return json.dumps(quote,ensure_ascii=False)
    except (ValueError,TypeError):
        return '[unparsed quote]'


def stamp(value):
    if not value:
        return 0
    return int(datetime.fromisoformat(value).timestamp())


def connect(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    return db


def source_reader(args):
    reader_dir = Path(args.reader_dir) if getattr(args, 'reader_dir', None) else Path(__file__).parent
    spec = importlib.util.spec_from_file_location('wechat_source', reader_dir / 'wechat_reader.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root = Path(args.db_root) if args.db_root else mod.default_db_root()
    reader = mod.Reader(root, Path(args.key_file) if args.key_file else mod.DEFAULT_KEYS)
    return mod, reader


def source(db, args):
    """Reopen one original message without changing the reading copy."""
    item = db.execute('SELECT * FROM messages WHERE id=?', (args.id,)).fetchone()
    if item is None:
        raise ValueError('message id absent from this snapshot')
    shard, table = item['source_db'], item['source_table']
    if not re.fullmatch(r'message_[0-9]+\.db', shard) or not re.fullmatch(r'Msg_[0-9a-f]{32}', table):
        raise ValueError('invalid source coordinates')
    mod, reader = source_reader(args)
    with reader.open(reader.root / 'message' / shard) as original:
        row = original.execute('SELECT server_id,create_time,local_type,message_content FROM "' + table + '" WHERE local_id=?', (item['local_id'],)).fetchone()
        if row is None or row['server_id'] != item['server_id'] or row['create_time'] != item['ts']:
            raise ValueError('source message no longer matches snapshot identity')
        raw, decoding = mod.decode(row['message_content'], item['sender'])
        body, quote = render(raw, row['local_type'])
    print(json.dumps({'snapshot_id': args.id, 'source_db': shard, 'source_table': table,
                      'local_id': item['local_id'], 'server_id': item['server_id'],
                      'chat': item['chat_name'], 'speaker': item['sender_name'],
                      'time': datetime.fromtimestamp(item['ts'], ZoneInfo(args.timezone)).isoformat(),
                      'text': display_row_body(db, item, body, row['local_type']), 'quote': display_quote(quote),
                      'decoding': decoding}, ensure_ascii=False, indent=2))


def snapshot(args):
    mod, reader = source_reader(args)
    out = Path(args.output).resolve()
    # Reserve a new file privately; never overwrite an earlier reading copy.
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    db = sqlite3.connect(out)
    db.executescript('''CREATE TABLE messages (id INTEGER PRIMARY KEY, chat TEXT, chat_name TEXT,
    sender TEXT, sender_name TEXT, own INTEGER, ts INTEGER, kind INTEGER, text TEXT, quote TEXT,
    decoding TEXT, source_db TEXT, source_table TEXT, local_id INTEGER, server_id INTEGER);
    CREATE TABLE metadata (key TEXT PRIMARY KEY,value TEXT);''')
    since, until = stamp(args.since), stamp(args.until) if args.until else 2**62
    report = {'self_id': args.self_id, 'since': args.since, 'until': args.until,
              'timezone': args.timezone, 'started_at': datetime.now().astimezone().isoformat(),
              'source': 'Mac WeChat committed database/WAL; one transaction per shard',
              'errors': [], 'tables': 0, 'rows': 0, 'undecodable': 0, 'unresolved_chats': 0}
    try:
        with reader.open(reader.root / 'contact/contact.db') as src:
            contacts = {r['username']: r['remark'] or r['nick_name'] or r['username'] for r in src.execute('SELECT username,remark,nick_name FROM contact')}
        paths = sorted((reader.root / 'message').glob('message_[0-9]*.db'))
        expected = {name for name in reader.keys if re.fullmatch(r'message/message_[0-9]+\.db', name)}
        present = {str(p.relative_to(reader.root)) for p in paths}
        report['errors'].extend({'database': n, 'error': 'missing shard'} for n in sorted(expected - present))
        if not paths:
            raise ValueError('no message shards')
        names_all = set(contacts)
        for p in paths:
            with reader.open(p) as src:
                names_all.update(r[0] for r in src.execute('SELECT user_name FROM Name2Id'))
        hashes = {'Msg_' + hashlib.md5(n.encode()).hexdigest(): n for n in names_all}
        for p in paths:
            try:
                with reader.open(p) as src:
                    names = dict(src.execute('SELECT rowid,user_name FROM Name2Id'))
                    tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'Msg_*'")]
                    for table in tables:
                        if not re.fullmatch(r'Msg_[0-9a-f]{32}', table):
                            raise ValueError('unexpected message table')
                        report['tables'] += 1
                        chat = hashes.get(table, table)
                        if chat == table:
                            report['unresolved_chats'] += 1
                        batch = []
                        for r in src.execute('SELECT local_id,server_id,local_type,real_sender_id,create_time,message_content FROM "' + table + '" WHERE create_time>=? AND create_time<? ORDER BY create_time,local_id', (since,until)):
                            sender = names.get(r['real_sender_id'])
                            raw, decoding = mod.decode(r['message_content'], sender)
                            body, quote = render(raw, r['local_type'])
                            batch.append((chat,contacts.get(chat,chat),sender,contacts.get(sender,sender),int(sender==args.self_id),r['create_time'],r['local_type'],body,quote,decoding,p.name,table,r['local_id'],r['server_id']))
                            report['rows'] += 1
                            report['undecodable'] += raw is None
                            if len(batch) >= 2000:
                                db.executemany('INSERT INTO messages VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',batch);batch=[]
                        db.executemany('INSERT INTO messages VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',batch)
                db.commit()
            except (ValueError,OSError,mod.DatabaseError) as e:
                report['errors'].append({'database':p.name,'error':str(e)})
        db.executescript('CREATE INDEX by_chat_time ON messages(chat,ts,source_db,local_id); CREATE INDEX by_own_time ON messages(own,ts);')
        report['finished_at'] = datetime.now().astimezone().isoformat()
        db.execute('INSERT INTO metadata VALUES(?,?)',('snapshot',json.dumps(report,ensure_ascii=False)))
        db.commit()
    finally:
        db.close()
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 2 if report['errors'] else 0


def where(args):
    clauses, values = [], []
    for col, val in [('chat',getattr(args,'chat',None)),('own',1 if getattr(args,'own',False) else None)]:
        if val is not None:
            clauses.append(col+'=?');values.append(val)
    if getattr(args,'since',None):
        clauses.append('ts>=?');values.append(stamp(args.since))
    if getattr(args,'until',None):
        clauses.append('ts<?');values.append(stamp(args.until))
    if getattr(args,'query',None):
        clauses.append('instr(lower(text),lower(?))>0');values.append(args.query)
    if getattr(args,'name',None):
        clauses.append('(instr(lower(chat_name),lower(?))>0 OR instr(lower(chat),lower(?))>0)')
        values.extend([args.name,args.name])
    if getattr(args,'sender',None):
        clauses.append('sender=?');values.append(args.sender)
    return ' AND '.join(clauses) or '1',values


def output_rows(db, args):
    clause, values = where(args)
    rows = db.execute('SELECT * FROM messages WHERE '+clause+' ORDER BY ts,source_db,local_id LIMIT ? OFFSET ?',[*values,args.limit,args.offset]).fetchall()
    heading = None
    self_label = getattr(args,'self_label',None) or 'me'
    for r in rows:
        item = dict(r)
        item['text'] = display_row_body(db, item)
        item['quote'] = display_quote(item['quote'])
        item['time'] = datetime.fromtimestamp(r['ts'],ZoneInfo(args.timezone)).isoformat()
        if args.json:
            print(json.dumps(item,ensure_ascii=False))
        elif getattr(args,'compact',False):
            day = item['time'][:10]
            if heading != (r['chat'],day):
                heading = (r['chat'],day)
                print(f"\n{r['chat_name']} ({r['chat']}) | {day} | {args.timezone}")
            who = self_label if r['own'] else r['sender_name'] or '[unknown sender]'
            # JSON escaping preserves line breaks and prevents a message from
            # impersonating the next row's metadata. No body is abbreviated.
            print(f"{r['id']} {item['time'][11:]} {who}: {json.dumps(item['text'],ensure_ascii=False)}")
            if item['quote']:
                print('  QUOTED (separate attribution): '+item['quote'])
        else:
            ident = f"{r['source_db']}:{r['source_table']}:{r['local_id']}"
            who = self_label if r['own'] else r['sender_name'] or '[unknown sender]'
            print(f"id={r['id']} | {item['time']} | {r['chat_name']} | {who} | {ident}\n{item['text']}")
            if item['quote']:
                print('  QUOTED (separate attribution): '+item['quote'])
    count=db.execute('SELECT count(*) FROM messages WHERE '+clause,values).fetchone()[0]
    print(json.dumps({'matched':count,'returned':len(rows),'offset':args.offset,'next_offset':args.offset+len(rows) if args.offset+len(rows)<count else None}),file=sys.stderr)


def around(db, args):
    row = db.execute('SELECT * FROM messages WHERE id=?',(args.id,)).fetchone()
    if row is None:
        raise ValueError('message id absent from this snapshot')
    # Include complete timestamp ties instead of arbitrarily dropping a reply.
    before = db.execute('SELECT ts FROM messages WHERE chat=? AND ts<=? ORDER BY ts DESC LIMIT ?',
                        (row['chat'],row['ts'],args.radius+1)).fetchall()
    after = db.execute('SELECT ts FROM messages WHERE chat=? AND ts>=? ORDER BY ts LIMIT ?',
                       (row['chat'],row['ts'],args.radius+1)).fetchall()
    args.chat=row['chat'];args.own=False
    args.since=datetime.fromtimestamp(min(r[0] for r in before),ZoneInfo(args.timezone)).isoformat()
    args.until=datetime.fromtimestamp(max(r[0] for r in after)+1,ZoneInfo(args.timezone)).isoformat()
    output_rows(db,args)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('snapshot')
    p.add_argument('--reader-dir',help='defaults to this Core copy of wechat_reader.py');p.add_argument('--self-id',required=True)
    p.add_argument('--db-root');p.add_argument('--key-file');p.add_argument('--output',required=True)
    p.add_argument('--since',required=True,help='ISO date/time with explicit offset preferred')
    p.add_argument('--until');p.add_argument('--timezone',default=local_timezone_name())
    for command in ['chats','roster','read','search','own','around','source','info']:
        p=sub.add_parser(command);p.add_argument('--db',required=True)
        if command=='info':continue
        p.add_argument('--since');p.add_argument('--until');p.add_argument('--chat')
        p.add_argument('--own',action='store_true',default=command=='own')
        p.add_argument('--limit',type=int,default=100);p.add_argument('--offset',type=int,default=0)
        p.add_argument('--timezone',default=local_timezone_name());p.add_argument('--json',action='store_true')
        p.add_argument('--self-label',default='me',help="label for the account's own messages in text output")
        p.add_argument('--name',help='literal substring of current conversation label or ID')
        p.add_argument('--sender',help='exact sender ID, across matching conversations')
        p.add_argument('--compact',action='store_true',help='group text by conversation/day; retain every body and quote, with snapshot row IDs')
        if command=='search':p.add_argument('query')
        if command=='around':
            p.add_argument('--id',type=int,required=True);p.add_argument('--radius',type=int,default=10)
        if command=='source':
            p.add_argument('--id',type=int,required=True)
            p.add_argument('--reader-dir',help='defaults to this Core copy of wechat_reader.py');p.add_argument('--db-root');p.add_argument('--key-file')
    args=ap.parse_args()
    if args.command=='snapshot':return snapshot(args)
    if hasattr(args,'limit') and (args.limit<1 or args.offset<0):ap.error('limit must be positive; offset must be nonnegative')
    if hasattr(args,'radius') and args.radius<0:ap.error('radius must be nonnegative')
    with connect(args.db) as db:
        if args.command=='info':print(db.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])
        elif args.command=='chats':
            clause,values=where(args)
            for row in db.execute('SELECT chat,chat_name,count(*) AS messages,sum(own) AS own_messages,min(ts) AS first,max(ts) AS last FROM messages WHERE '+clause+' GROUP BY chat ORDER BY own_messages DESC LIMIT ? OFFSET ?',[*values,args.limit,args.offset]):print(json.dumps(dict(row),ensure_ascii=False))
        elif args.command=='roster':
            clause,values=where(args)
            for row in db.execute('SELECT sender,sender_name,count(*) AS messages,sum(own) AS own_messages,min(ts) AS first,max(ts) AS last FROM messages WHERE '+clause+' GROUP BY sender ORDER BY messages DESC LIMIT ? OFFSET ?',[*values,args.limit,args.offset]):
                print(json.dumps(dict(row),ensure_ascii=False))
            print('Observed senders only; not current membership or a measure of closeness.',file=sys.stderr)
        elif args.command=='around':around(db,args)
        elif args.command=='source':source(db,args)
        else:output_rows(db,args)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
