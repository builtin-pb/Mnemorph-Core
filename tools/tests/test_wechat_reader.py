import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

spec=importlib.util.spec_from_file_location('wechat_reader',Path(__file__).resolve().parents[1]/'wechat_reader.py')
wechat_reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(wechat_reader)


def _keyfile(directory, data, mode=0o600):
    path = Path(directory) / 'keys.json'
    path.write_text(json.dumps(data))
    os.chmod(path, mode)
    return path


class DecodeTests(unittest.TestCase):
    """decode() needs no database and no sqlcipher3; it runs on plain values."""

    def test_plain_text_passes_through(self):
        self.assertEqual(wechat_reader.decode('hello', None), ('hello', 'decoded'))

    def test_strips_leading_sender_id_prefix(self):
        self.assertEqual(wechat_reader.decode('wxid_example:\nhi', 'wxid_example'), ('hi', 'decoded'))

    def test_null_payload_is_reported_not_guessed(self):
        self.assertEqual(wechat_reader.decode(None, None), (None, 'null'))

    def test_unexpected_type_is_reported(self):
        self.assertEqual(wechat_reader.decode(12345, None), (None, 'unexpected_type'))

    def test_non_utf8_binary_is_undecodable_not_guessed(self):
        text, decoding = wechat_reader.decode(b'\xff\xfe\x00\x01', None)
        self.assertIsNone(text)
        self.assertEqual(decoding, 'binary_or_missing_compression_dictionary')

    @unittest.skipUnless(wechat_reader.zstd, 'compression.zstd not available on this Python')
    def test_zstd_compressed_payload_round_trips(self):
        payload = 'a longer message body, compressed'
        compressed = wechat_reader.zstd.compress(payload.encode('utf-8'))
        self.assertEqual(wechat_reader.decode(compressed, None), (payload, 'decoded'))

    @unittest.skipUnless(wechat_reader.zstd, 'compression.zstd not available on this Python')
    def test_oversized_decompressed_payload_is_rejected(self):
        huge = b'a' * (16 * 1024 * 1024 + 100)
        compressed = wechat_reader.zstd.compress(huge)
        text, decoding = wechat_reader.decode(compressed, None)
        self.assertIsNone(text)
        self.assertEqual(decoding, 'decoded_payload_too_large')


class EncodeTests(unittest.TestCase):
    def test_bytes_become_base64_others_pass_through(self):
        self.assertEqual(wechat_reader.encode(b'abc'), {'base64': 'YWJj'})
        self.assertEqual(wechat_reader.encode('abc'), 'abc')
        self.assertEqual(wechat_reader.encode(7), 7)


class ReaderInitTests(unittest.TestCase):
    """Reader.__init__ only touches the filesystem; no sqlcipher3 needed."""

    def test_rejects_group_or_world_readable_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'db_storage'; root.mkdir()
            keyfile = _keyfile(tmp, {'keys': {}}, mode=0o644)
            with self.assertRaises(ValueError):
                wechat_reader.Reader(root, keyfile)

    def test_rejects_symlinked_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'db_storage'; root.mkdir()
            real = Path(tmp) / 'real.json'; real.write_text('{"keys": {}}'); os.chmod(real, 0o600)
            link = Path(tmp) / 'keys.json'; link.symlink_to(real)
            with self.assertRaises(ValueError):
                wechat_reader.Reader(root, link)

    def test_rejects_key_manifest_for_a_different_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'db_storage'; root.mkdir()
            other = Path(tmp) / 'other_root'; other.mkdir()
            keyfile = _keyfile(tmp, {'db_root': str(other), 'keys': {}})
            with self.assertRaises(ValueError):
                wechat_reader.Reader(root, keyfile)

    def test_accepts_a_matching_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'db_storage'; root.mkdir()
            keyfile = _keyfile(tmp, {'db_root': str(root), 'keys': {'a.db': {'enc_key': 'ab' * 32}}})
            reader = wechat_reader.Reader(root, keyfile)
            self.assertEqual(reader.keys['a.db']['enc_key'], 'ab' * 32)


class ReaderOpenWithoutSqlcipherTests(unittest.TestCase):
    def setUp(self):
        if wechat_reader.SQLCIPHER_AVAILABLE:
            self.skipTest('sqlcipher3 is installed; this only checks the missing-dependency path')

    def test_open_reports_the_missing_dependency_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'db_storage'; root.mkdir()
            keyfile = _keyfile(tmp, {'keys': {}})
            reader = wechat_reader.Reader(root, keyfile)
            with self.assertRaises(ValueError):
                with reader.open(root / 'a.db'):
                    pass


class DefaultDbRootTests(unittest.TestCase):
    """default_db_root() reads Path.home(), which we point at a scratch
    directory so the test never touches this machine's real WeChat data."""

    def test_selects_the_only_account_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            account = home / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/wxid_example'
            (account / 'db_storage').mkdir(parents=True)
            with mock.patch('pathlib.Path.home', return_value=home):
                self.assertEqual(wechat_reader.default_db_root(), account / 'db_storage')

    def test_raises_when_several_accounts_are_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            base = home / 'Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files'
            for name in ('wxid_one', 'wxid_two'):
                (base / name / 'db_storage').mkdir(parents=True)
            with mock.patch('pathlib.Path.home', return_value=home):
                with self.assertRaises(ValueError):
                    wechat_reader.default_db_root()

    def test_raises_when_no_container_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch('pathlib.Path.home', return_value=Path(tmp)):
                with self.assertRaises(ValueError):
                    wechat_reader.default_db_root()


@unittest.skipUnless(wechat_reader.SQLCIPHER_AVAILABLE, 'sqlcipher3 is not installed')
class ReaderWithSqlcipherTests(unittest.TestCase):
    """Exercised only when sqlcipher3 is installed; builds its own throwaway
    encrypted database rather than touching any real WeChat data."""

    def test_verify_reports_a_freshly_created_encrypted_database(self):
        from sqlcipher3 import dbapi2 as sqlcipher
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'db_storage'
            (root / 'contact').mkdir(parents=True)
            db_file = root / 'contact' / 'contact.db'
            key = 'ab' * 32
            con = sqlcipher.connect(str(db_file))
            con.execute('PRAGMA key="x\'' + key + '\'"')
            con.execute('PRAGMA cipher_compatibility=4')
            con.execute('CREATE TABLE contact(username)')
            con.execute("INSERT INTO contact VALUES ('example')")
            con.commit()
            con.close()
            salt = db_file.open('rb').read(16).hex()
            keyfile = _keyfile(tmp, {'db_root': str(root), 'keys': {
                'contact/contact.db': {'enc_key': key, 'salt': salt}}})
            reader = wechat_reader.Reader(root, keyfile)
            report = reader.verify()
            self.assertEqual(report['verified'], 1)
            self.assertEqual(report['total'], 1)
            self.assertTrue(report['databases'][0]['verified'])


if __name__=='__main__':unittest.main()
