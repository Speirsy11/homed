import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from homed.auth import AuthStore, AuthError


class AuthTests(unittest.TestCase):
    def test_account_session_survives_reopen_and_logout_revokes_it(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'auth.sqlite3'
            store = AuthStore(path)
            store.create_account('charlie', 'a long test passphrase')
            with self.assertRaises(AuthError) as error:
                store.login('charlie', 'wrong password', '127.0.0.1')
            self.assertEqual(error.exception.status, 401)
            login = store.login('charlie', 'a long test passphrase', '127.0.0.1')
            reopened = AuthStore(path)
            self.assertEqual(reopened.session(login['token'])['user']['username'], 'charlie')
            self.assertTrue(login['csrf_token'])
            reopened.logout(login['token'])
            self.assertIsNone(store.session(login['token']))

    def test_account_created_with_legacy_hashlib_scrypt_digest_can_log_in(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'auth.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                db.execute(
                    'CREATE TABLE accounts (username TEXT PRIMARY KEY, salt BLOB NOT NULL, password_hash BLOB NOT NULL)'
                )
                db.execute(
                    'INSERT INTO accounts VALUES (?,?,?)',
                    (
                        'legacy',
                        bytes.fromhex('00112233445566778899aabbccddeeff'),
                        bytes.fromhex('e06303b7558e22af710ced6e0861688cb4012f19a893d500475fcd5322779fe8'),
                    ),
                )
                db.execute('PRAGMA user_version=1')

            login = AuthStore(path).login('legacy', 'legacy compatible passphrase', 'local')
            self.assertEqual(login['user']['username'], 'legacy')


if __name__ == '__main__':
    unittest.main()


class AuthExpiryTests(unittest.TestCase):
    def test_idle_expiry_and_stale_token_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as td:
            now = [1000.0]
            store = AuthStore(Path(td)/'auth.sqlite3', now=lambda:now[0])
            store.create_account('charlie','a long test passphrase')
            token = store.login('charlie','a long test passphrase','local')['token']
            self.assertIsNone(store.session(token+'x'))
            now[0] += 86401
            self.assertIsNone(store.session(token))

    def test_account_cannot_be_replaced_and_attempts_are_throttled(self):
        with tempfile.TemporaryDirectory() as td:
            store = AuthStore(Path(td)/'auth.sqlite3')
            with self.assertRaises(AuthError):
                store.create_account('charlie','short')
            store.create_account('charlie','a long test passphrase')
            with self.assertRaises(AuthError) as error:
                store.create_account('charlie','a different long passphrase')
            self.assertEqual(error.exception.status,409)
            for _ in range(8):
                with self.assertRaises(AuthError) as error:
                    store.login('charlie','bad','local')
                self.assertEqual(error.exception.status,401)
            with self.assertRaises(AuthError) as error:
                store.login('charlie','a long test passphrase','local')
            self.assertEqual(error.exception.status,429)
