import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('wechat',Path(__file__).resolve().parents[1]/'wechat.py')
wechat=importlib.util.module_from_spec(spec);spec.loader.exec_module(wechat)


class WechatTests(unittest.TestCase):
    def test_reply_does_not_attribute_quoted_content_to_sender(self):
        raw='<msg><appmsg><type>57</type><title>I am considering it</title><refermsg><displayname>Alice</displayname><content>I accepted the job</content></refermsg></appmsg></msg>'
        body,quote=wechat.render(raw,244813135921)
        self.assertEqual(body,'I am considering it')
        self.assertIn('Alice',quote)
        self.assertIn('I accepted the job',quote)
        self.assertNotIn('accepted',body)

    def test_forward_and_media_stay_marked(self):
        text,_=wechat.render('<msg><appmsg><type>19</type><title>Chat history</title><des>Alice: my degree</des></appmsg></msg>',81604378673)
        self.assertTrue(text.startswith('[forwarded record]'))
        self.assertEqual(wechat.render('<msg><img /></msg>',3),('[image]',None))
        self.assertEqual(wechat.render(None,1),('[undecodable]',None))
        self.assertTrue(wechat.render('<msg>broken',49)[0].startswith('[unparsed'))

    def test_explicit_offset_same_instant(self):
        self.assertEqual(wechat.stamp('2030-03-10T21:15:00-04:00'),wechat.stamp('2030-03-11T01:15:00+00:00'))

    def test_contact_card_preserves_identity_without_other_attributes(self):
        raw='<msg username="wxid_alice" nickname="Alice" alias="alice_card" city="Private city" sign="Personal note" aeskey="synthetic-secret" />'
        body, quote = wechat.render(raw, 42)
        self.assertEqual(json.loads(body.removeprefix('[contact card] ')), {
            'nickname': 'Alice', 'username': 'wxid_alice', 'alias': 'alice_card'})
        self.assertIsNone(quote)
        self.assertNotIn('Private city', body)
        self.assertNotIn('synthetic-secret', body)
        self.assertNotIn('Personal note', body)
        self.assertEqual(wechat.render(raw, 49), ('[structured message]', None))

    def test_multiline_device_code_and_contextual_numeric_reply(self):
        raw='Enter this one-time code after signing in (expires soon)\n\n  TEST-12345\nContinue only if you started this login.'
        self.assertNotIn('TEST-12345', wechat.display_body(raw))
        with contextlib.closing(sqlite3.connect(':memory:')) as db:
            db.row_factory = sqlite3.Row
            db.execute('CREATE TABLE messages(id,chat,ts,text,kind)')
            db.executemany('INSERT INTO messages VALUES(?,?,?,?,1)', [
                (1,'a',100,'What is the bike password?'),
                (2,'b',101,'54321'), (3,'a',102,'hello'),
                (4,'a',103,'54321'), (5,'a',500,'54321'),
                (6,'c',100,'这本密码学教材很厚'), (7,'c',101,'2026')])
            def rendered(i):
                return wechat.display_row_body(db,db.execute('SELECT * FROM messages WHERE id=?',(i,)).fetchone())
            self.assertEqual(rendered(4),'[REDACTED:possible_credential_reply]')
            self.assertEqual(rendered(2),'54321')
            self.assertEqual(rendered(5),'54321')
            self.assertEqual(rendered(7),'2026')

    def test_forwarded_record_reads_beyond_preview_without_media_credentials(self):
        raw='''<msg><appmsg><type>19</type><title>Alice and Bob</title><des>Alice: preview...</des><recorditem><![CDATA[<recordinfo><datalist>
        <dataitem datatype="1"><sourcename>Alice</sourcename><sourcetime>2030-01-01 12:00:00</sourcetime><datadesc>The meeting moved to Thursday afternoon.</datadesc></dataitem>
        <dataitem datatype="2"><sourcename>Bob</sourcename><datatitle>photo</datatitle><datadesc>hidden payload</datadesc><aeskey>synthetic-secret</aeskey></dataitem>
        </datalist></recordinfo>]]></recorditem></appmsg></msg>'''
        text,quote=wechat.render(raw,49)
        self.assertIn('Thursday afternoon',text)
        self.assertIn('Alice',text)
        self.assertIn('2030-01-01 12:00:00',text)
        self.assertIn('[forwarded item type 2]',text)
        self.assertNotIn('synthetic-secret',text)
        self.assertNotIn('hidden payload',text)
        self.assertIsNone(quote)

    def test_group_system_notice_keeps_identity_event_without_raw_payload(self):
        raw='''123@chatroom:\n<sysmsg><sysmsgtemplate><content_template><template>$username$ invited $names$ $revoke$</template><link_list>
        <link name="username"><memberlist><member><nickname>Alice</nickname><username>private-id</username></member></memberlist></link>
        <link name="names"><memberlist><member><nickname>Bob</nickname></member></memberlist></link>
        <link name="revoke" hidden="1"><title>Revoke</title></link>
        </link_list></content_template></sysmsgtemplate></sysmsg>'''
        self.assertEqual(wechat.display_body(raw,10002),'[system] Alice invited Bob')

    def test_legacy_media_and_quoted_media_do_not_dump_payload_keys(self):
        raw='null:\n<msg><img aeskey="synthetic-media-secret" /></msg>'
        self.assertEqual(wechat.display_body('[unparsed structured message] '+raw,3),'[image]')
        quote=wechat.display_quote(json.dumps({'displayname':'Alice','type':'3','content':raw}))
        self.assertIn('Alice',quote)
        self.assertIn('[image]',quote)
        self.assertNotIn('synthetic-media-secret',quote)
        self.assertEqual(wechat.render('<msg broken aeskey="synthetic-media-secret"',49)[0],'[unparsed structured message]')
        self.assertNotIn('example@123',wechat.display_body('密码：example@123'))
        self.assertEqual(wechat.display_body('这本密码学教材很厚，例题也多'),'这本密码学教材很厚，例题也多')

    def test_local_timezone_name_falls_back_to_utc_when_undetectable(self):
        with patch.object(wechat.Path, 'resolve', side_effect=OSError('no /etc/localtime')):
            self.assertEqual(wechat.local_timezone_name(), 'UTC')

    def test_source_reopens_identity_and_uses_safe_final_display(self):
        table='Msg_'+'a'*32
        with contextlib.closing(sqlite3.connect(':memory:')) as snapshot, contextlib.closing(sqlite3.connect(':memory:')) as original:
            snapshot.row_factory=original.row_factory=sqlite3.Row
            snapshot.execute('CREATE TABLE messages(id, source_db, source_table, local_id, server_id, ts, sender, chat_name, sender_name)')
            snapshot.execute('INSERT INTO messages VALUES(1,?,?,?,?,?,?,?,?)',('message_1.db',table,9,42,100,'alice','Friends','Alice'))
            original.execute(f'CREATE TABLE "{table}"(local_id,server_id,create_time,local_type,message_content)')
            original.execute(f'INSERT INTO "{table}" VALUES(9,42,100,1,?)',('The meeting moved to Thursday.',))
            @contextlib.contextmanager
            def opened(path):
                self.assertEqual(path,Path('/source/message/message_1.db'))
                yield original
            reader=SimpleNamespace(root=Path('/source'),open=opened)
            mod=SimpleNamespace(decode=lambda value,sender:(value,'synthetic'))
            args=argparse.Namespace(id=1,timezone='UTC')
            with patch.object(wechat,'source_reader',return_value=(mod,reader)):
                out=io.StringIO()
                with contextlib.redirect_stdout(out):wechat.source(snapshot,args)
                result=json.loads(out.getvalue())
                self.assertEqual(result['text'],'The meeting moved to Thursday.')
                self.assertEqual(result['speaker'],'Alice')
                self.assertEqual(result['server_id'],42)
                raw='<sysmsg><replacemsg><![CDATA[<msg><img aeskey="synthetic-media-secret" /></msg>]]></replacemsg></sysmsg>'
                original.execute(f'UPDATE "{table}" SET local_type=10002,message_content=?',(raw,))
                out=io.StringIO()
                with contextlib.redirect_stdout(out):wechat.source(snapshot,args)
                self.assertNotIn('synthetic-media-secret',out.getvalue())
                self.assertIn('[unparsed structured message]',out.getvalue())
                # Rejection must happen before any message content is printed.
                mutations=[('server_id',43),('create_time',101)]
                for column,value in mutations:
                    with self.subTest(column=column):
                        original.execute(f'UPDATE "{table}" SET "{column}"=?',(value,))
                        out=io.StringIO()
                        with contextlib.redirect_stdout(out),self.assertRaises(ValueError):wechat.source(snapshot,args)
                        self.assertEqual(out.getvalue(),'')
                        original.execute(f'UPDATE "{table}" SET server_id=42,create_time=100')
                for shard,source_table in [('../message_1.db',table),('message_1.db','invalid_table')]:
                    snapshot.execute('UPDATE messages SET source_db=?,source_table=?',(shard,source_table))
                    out=io.StringIO()
                    with contextlib.redirect_stdout(out),self.assertRaises(ValueError):wechat.source(snapshot,args)
                    self.assertEqual(out.getvalue(),'')
                snapshot.execute('UPDATE messages SET source_db=?,source_table=?',('message_1.db',table))
                original.execute(f'DELETE FROM "{table}"')
                out=io.StringIO()
                with contextlib.redirect_stdout(out),self.assertRaises(ValueError):wechat.source(snapshot,args)
                self.assertEqual(out.getvalue(),'')
                args.id=2
                out=io.StringIO()
                with contextlib.redirect_stdout(out),self.assertRaises(ValueError):wechat.source(snapshot,args)
                self.assertEqual(out.getvalue(),'')

    def test_readonly_and_literal_search_and_context_ties(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'test.sqlite'
            d=sqlite3.connect(path)
            d.execute('CREATE TABLE messages(id integer primary key, chat, chat_name, own, ts, source_db, source_table, local_id, sender_name, text, quote)')
            d.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)',[
                (1,'a','A',0,100,'m0','Msg_a',1,'Alice','before',None),
                (2,'a','A',1,101,'m0','Msg_a',2,'Devon','100% literal',None),
                (3,'a','A',0,101,'m1','Msg_a',3,'Alice','same second',None),
                (4,'b','B',1,101,'m0','Msg_b',4,'Devon','unrelated',None),
                (5,'a','A',1,102,'m0','Msg_a',5,'Devon','after',None)])
            d.commit();d.close()
            with wechat.connect(path) as d:
                with self.assertRaises(sqlite3.OperationalError):d.execute('DELETE FROM messages')
                args=argparse.Namespace(chat=None,own=False,since=None,until=None,query='%',limit=10,offset=0,json=True,timezone='UTC')
                out,err=io.StringIO(),io.StringIO()
                with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):wechat.output_rows(d,args)
                self.assertIn('100% literal',out.getvalue());self.assertNotIn('unrelated',out.getvalue())
                args.query=None;args.id=2;args.radius=0
                out=io.StringIO()
                with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):wechat.around(d,args)
                self.assertIn('same second',out.getvalue());self.assertNotIn('unrelated',out.getvalue())
                args.json=False;args.compact=True;args.query=None
                out=io.StringIO()
                with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):wechat.output_rows(d,args)
                self.assertIn('A (a)',out.getvalue())
                self.assertIn('100% literal',out.getvalue())
                self.assertIn('same second',out.getvalue())
                # No --self-label was set; own rows fall back to the generic default.
                self.assertIn(' me: ',out.getvalue())
                self.assertNotIn('Devon',out.getvalue())
                args.self_label='Coach'
                out=io.StringIO()
                with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):wechat.output_rows(d,args)
                self.assertIn(' Coach: ',out.getvalue())


if __name__=='__main__':unittest.main()
