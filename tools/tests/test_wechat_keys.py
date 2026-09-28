import hashlib
import hmac
import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest

_module_path = Path(__file__).resolve().parents[1] / 'wechat_keys.py'
wechat_keys = None
if _module_path.exists():
    spec = importlib.util.spec_from_file_location('wechat_keys', _module_path)
    wechat_keys = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wechat_keys)


def _fixture_page(key, salt=None, tamper_key=False):
    """A synthetic 4096-byte SQLCipher page whose trailing HMAC matches the
    same construction `valid_key` checks: HMAC-SHA512 of the page body under
    a PBKDF2-derived per-page key, keyed by the page's own salt. No real
    WeChat data or process is involved."""
    salt = salt or bytes(range(16))
    body = bytes((i * 7) % 256 for i in range(4096 - 16 - 64))
    checked_key = (b'\x00' * 32) if tamper_key else key
    hkey = hashlib.pbkdf2_hmac('sha512', checked_key, bytes(x ^ 0x3a for x in salt[:16]), 2, 32)
    mac = hmac.new(hkey, body + b'\x01\x00\x00\x00', hashlib.sha512)
    return salt + body + mac.digest()


@unittest.skipUnless(wechat_keys, 'tools/wechat_keys.py is not present in this checkout')
class ValidKeyTests(unittest.TestCase):
    def test_accepts_the_correct_key(self):
        key = b'k' * 32
        page = _fixture_page(key)
        self.assertTrue(wechat_keys.valid_key(key, page))

    def test_rejects_a_wrong_key(self):
        key = b'k' * 32
        page = _fixture_page(key, tamper_key=True)
        self.assertFalse(wechat_keys.valid_key(key, page))

    def test_rejects_wrong_length_inputs(self):
        self.assertFalse(wechat_keys.valid_key(b'short', b'p' * 4096))
        self.assertFalse(wechat_keys.valid_key(b'k' * 32, b'short'))


@unittest.skipUnless(wechat_keys, 'tools/wechat_keys.py is not present in this checkout')
class SaveKeysTests(unittest.TestCase):
    def test_writes_a_private_file_and_cleans_up_the_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'keys.json'
            wechat_keys.save_keys(out, Path('/example/db_storage'), {'a.db': {'enc_key': 'ab' * 32, 'salt': 'cd' * 16}})
            self.assertTrue(out.exists())
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
            self.assertFalse((Path(tmp) / 'keys.json.new').exists())

    def test_republishing_atomically_replaces_earlier_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'keys.json'
            wechat_keys.save_keys(out, Path('/example/db_storage'), {'a.db': {'enc_key': 'ab' * 32, 'salt': 'cd' * 16}})
            first = out.read_text()
            wechat_keys.save_keys(out, Path('/example/db_storage'), {
                'a.db': {'enc_key': 'ab' * 32, 'salt': 'cd' * 16},
                'b.db': {'enc_key': 'ef' * 32, 'salt': 'gh' * 16}})
            second = out.read_text()
            self.assertNotEqual(first, second)
            self.assertIn('b.db', second)
            self.assertFalse((Path(tmp) / 'keys.json.new').exists())

    def test_a_leftover_temp_file_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'keys.json'
            (Path(tmp) / 'keys.json.new').touch()
            with self.assertRaises(FileExistsError):
                wechat_keys.save_keys(out, Path('/example/db_storage'), {})


if __name__=='__main__':unittest.main()
