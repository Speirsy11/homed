"""Local administrator accounts and revocable sessions. No browser registration."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt


class AuthError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


from .storage import private_database


class AuthStore:
    def __init__(self, db_path, now=time.time):
        self.path = private_database(db_path)
        self.now = now
        self._kdf_lock = threading.Lock()
        with self._db() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise ValueError('Unsupported account database version')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS accounts (
                    username TEXT PRIMARY KEY, salt BLOB NOT NULL, password_hash BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, username TEXT NOT NULL,
                    csrf TEXT NOT NULL, expires REAL NOT NULL, last_seen REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    username TEXT NOT NULL, client TEXT NOT NULL, at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attempt_time ON attempts(at);
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _hash(password, salt):
        return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode('utf-8'))

    def has_accounts(self):
        with self._db() as db:
            return bool(db.execute('SELECT 1 FROM accounts LIMIT 1').fetchone())

    def create_account(self, username, password):
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', username):
            raise AuthError('Use 1–64 letters, numbers, dots, hyphens or underscores for the username')
        if not isinstance(password, str) or not 14 <= len(password) <= 1024:
            raise AuthError('Use a passphrase of 14–1024 characters')
        salt = secrets.token_bytes(16)
        with self._kdf_lock:
            digest = self._hash(password, salt)
        try:
            with self._db() as db:
                db.execute('INSERT INTO accounts VALUES (?,?,?)', (username, salt, digest))
        except sqlite3.IntegrityError:
            raise AuthError('That account already exists', 409) from None

    def login(self, username, password, client_id):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 1024:
            raise AuthError('Incorrect username or passphrase', 401)
        now = self.now()
        # Reserve an attempt before the expensive KDF. The same lock bounds
        # memory use and prevents concurrent attempts evading the throttle.
        with self._kdf_lock:
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('DELETE FROM attempts WHERE at < ?', (now - 600,))
                count = db.execute('SELECT count(*) FROM attempts WHERE username=? OR client=?',
                                   (username, client_id)).fetchone()[0]
                total = db.execute('SELECT count(*) FROM attempts').fetchone()[0]
                if count >= 8 or total >= 500:
                    raise AuthError('Too many sign-in attempts. Try again in 10 minutes.', 429)
                db.execute('INSERT INTO attempts VALUES (?,?,?)', (username, client_id, now))
                account = db.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
            salt = account['salt'] if account else b'\x00' * 16
            actual = self._hash(password, salt)
            expected = account['password_hash'] if account else b'\x00' * 32
            valid = hmac.compare_digest(actual, expected) and account is not None
        if not valid:
            raise AuthError('Incorrect username or passphrase', 401)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        expires = now + 7 * 86400
        with self._db() as db:
            db.execute('DELETE FROM attempts WHERE username=? AND client=?', (username, client_id))
            db.execute('DELETE FROM sessions WHERE expires<? OR last_seen<?', (now, now - 86400))
            db.execute('DELETE FROM sessions WHERE digest IN (SELECT digest FROM sessions WHERE username=? ORDER BY last_seen DESC LIMIT -1 OFFSET 19)', (username,))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), username, csrf, expires, now))
        return {'token': token, 'csrf_token': csrf, 'user': {'username': username}, 'expires_at': expires}

    def session(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = self.now()
        with self._db() as db:
            row = db.execute('SELECT * FROM sessions WHERE digest=?', (digest,)).fetchone()
            if not row or row['expires'] <= now or row['last_seen'] <= now - 86400:
                if row:
                    db.execute('DELETE FROM sessions WHERE digest=?', (digest,))
                return None
            db.execute('UPDATE sessions SET last_seen=? WHERE digest=?', (now, digest))
        return {'csrf_token': row['csrf'], 'user': {'username': row['username']}, 'expires_at': row['expires']}

    def logout(self, token):
        with self._db() as db:
            db.execute('DELETE FROM sessions WHERE digest=?', (hashlib.sha256(token.encode()).hexdigest(),))
