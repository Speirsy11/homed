"""Persisted, ageing observations; this collector never manages service lifecycle."""
from __future__ import annotations

import copy
import json
import os
import plistlib
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .storage import private_database
from . import registry, doctor
from .runtime import status_for


def redact(text):
    text = str(text)
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?(?:-----END [^-]*PRIVATE KEY-----|\Z)', '[redacted private key]', text)
    text = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1[redacted]', text)
    text = re.sub(r'(?i)((?:password|passwd|secret|token|api[_-]?key|authorization|cookie)\s*[=:]\s*)(?:"[^"\n]*"|\x27[^\x27\n]*\x27|[^\s,;]+)', r'\1[redacted]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[redacted token]', text)
    text = re.sub(r'https?://[^\s<>"\x27]+', lambda m: safe_url(m.group()) or '[redacted URL]', text)
    return text[-16000:]


def safe_url(value):
    try:
        url = urlsplit(str(value))
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            return None
        # A query or fragment can contain credentials, including private feeds.
        return urlunsplit((url.scheme, url.netloc, url.path, '', ''))
    except ValueError:
        return None


class Observations:
    def __init__(self, config_path, db_path, now=time.time, storage_paths=None, interval=30):
        self.config_path = Path(config_path)
        self.path = private_database(db_path)
        self.now, self.interval = now, interval
        self.storage_paths = storage_paths
        self._lock = threading.RLock()
        self._collect_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._collecting = False
        with closing(sqlite3.connect(str(self.path))) as db, db:
            if db.execute('PRAGMA user_version').fetchone()[0] > 1:
                raise ValueError('Unsupported observations database version')
            db.execute('CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('PRAGMA user_version=1')
            row = db.execute('SELECT payload FROM snapshot WHERE id=1').fetchone()
        self._snapshot = json.loads(row[0]) if row else {
            'observed_at': None, 'last_success_at': None, 'error': None,
            'services': [], 'issues': [], 'storage': [], 'routines': [], 'changes': [],
            'backup_note': 'Independent backup coverage has not been verified. Volumes on the same physical disk share its failure risk.',
        }

    def snapshot(self):
        with self._lock:
            result = copy.deepcopy(self._snapshot)
            result['stale_after_seconds'] = 90
            result['collecting'] = self._collecting
            observed = result['observed_at']
            result['stale'] = observed is None or self.now() - observed > 90 or bool(result['error'])
            return result

    def _save(self, payload):
        with self._lock:
            # Publish only after the durable write succeeds.
            with closing(sqlite3.connect(str(self.path), timeout=10)) as db, db:
                db.execute('INSERT OR REPLACE INTO snapshot VALUES (1,?)', (json.dumps(payload),))
            self._snapshot = payload

    def collect_once(self):
        if not self._collect_lock.acquire(blocking=False):
            return
        self._collecting = True
        try:
            old = self.snapshot()
            reg = registry.load(self.config_path)
            previous = {s['name']: s for s in old['services']}
            with ThreadPoolExecutor(max_workers=4) as pool:
                services = list(pool.map(lambda s: self._service(s, previous.get(s.name)), reg))
            at = self.now()
            changes = old['changes']
            for service in services:
                prior = previous.get(service['name'])
                if prior and (prior['manager_state'], prior['health_state']) != (service['manager_state'], service['health_state']):
                    changes.insert(0, {'service': service['name'], 'at': service['observed_at'],
                                      'previous': prior['manager_state'] + ' / ' + prior['health_state'],
                                      'current': service['manager_state'] + ' / ' + service['health_state']})
            issues = [{**i.to_dict(), 'message': redact(i.message)} for i in doctor.inspect(reg)]
            partial = any(s.get('collection_error') for s in services)
            self._save({'observed_at': old['observed_at'] if partial else at,
                        'last_success_at': old['last_success_at'] if partial else at,
                        'last_attempt_at': at,
                        'error': 'Some service checks failed; previous observations are retained.' if partial else None,
                        'services': services, 'issues': issues, 'storage': self._storage(),
                        'routines': [self._routine(s, services) for s in reg if s.intent.value == 'cron'],
                        'changes': changes[:200], 'backup_note': old['backup_note']})
        except Exception:
            # Error details from private configuration and command arguments can
            # contain credentials. Keep a stable, actionable public error.
            with self._lock:
                failed = copy.deepcopy(self._snapshot)
                failed['error'] = 'Collection failed. Check the registry and collector access; previous observations are retained.'
                failed['last_attempt_at'] = self.now()
                try:
                    self._save(failed)
                except Exception:
                    self._snapshot = failed
        finally:
            self._collecting = False
            self._collect_lock.release()

    def _service(self, service, previous):
        try:
            status = status_for(service).to_dict()
            status['detail'] = redact(status['detail'])
            links = []
            if service.web_url:
                url = safe_url(service.web_url)
                if url:
                    links.append({'label': 'Open app', 'url': url})
            if service.health.kind.value == 'http':
                url = safe_url(service.health.url)
                if url:
                    links.append({'label': 'Health endpoint', 'url': url})
            status.update(description=service.description, tags=service.tags, after=service.after,
                          requires=service.requires, mode_group=service.mode_group,
                          observed_at=self.now(), links=links,
                          health={'kind': service.health.kind.value},
                          logs_available=bool(service.logs.get('path')), collection_error=None)
            if service.health.kind.value == 'last_success':
                status['health']['max_age_seconds'] = service.health.max_age_seconds
            return status
        except Exception:
            if previous:
                result = copy.deepcopy(previous)
            else:
                result = {'name': service.name, 'driver': service.driver.value,
                          'intent': service.intent.value, 'exposure': service.exposure.value,
                          'manager_state': 'unknown', 'health_state': 'unknown', 'ok': False,
                          'observed_at': None, 'description': service.description, 'tags': service.tags,
                          'links': [], 'after': service.after, 'requires': service.requires,
                          'health': {'kind': service.health.kind.value}, 'logs_available': False}
            result['collection_error'] = 'This service could not be checked; any previous observation is retained.'
            result['ok'] = False
            result['detail'] = result['collection_error']
            return result

    def _routine(self, service, services):
        current = next((s for s in services if s['name'] == service.name), {})
        last_success = None
        if service.health.kind.value == 'last_success' and service.health.path:
            try:
                last_success = Path(service.health.path).expanduser().stat().st_mtime
            except OSError:
                pass
        return {'name': service.name, 'description': service.description,
                'manager_state': current.get('manager_state', 'unknown'),
                'health_state': current.get('health_state', 'unknown'),
                'observed_at': current.get('observed_at'), 'last_success_at': last_success,
                'next_due': None, 'detail': 'Next due and outcome history are not supplied by this registry.'}

    def _storage(self):
        if self.storage_paths is None:
            paths = [Path('/')]
            volumes = Path('/Volumes')
            if volumes.is_dir():
                paths.extend(p for p in volumes.iterdir() if not p.is_symlink() and os.path.ismount(p))
        else:
            paths = [Path(p) for p in self.storage_paths]
        rows, seen = [], set()
        for path in paths:
            try:
                # diskutil establishes that the mount is a local disk. Skip NFS,
                # OrbStack convenience mounts and aliases; never sum volume views.
                physical = None
                if shutil.which('diskutil'):
                    proc = subprocess.run(['diskutil', 'info', '-plist', str(path)], capture_output=True, timeout=5)
                    if proc.returncode:
                        continue
                    info = plistlib.loads(proc.stdout)
                    if not str(info.get('DeviceNode', '')).startswith('/dev/disk'):
                        continue
                    physical = info.get('ParentWholeDisk') or info.get('PartOfWhole') or info.get('DeviceIdentifier')
                device = path.stat().st_dev
                if device in seen:
                    continue
                seen.add(device)
                usage = shutil.disk_usage(path)
                rows.append({'name': 'Internal storage' if str(path) == '/' else path.name,
                             'path': str(path), 'physical_disk': physical,
                             'total_bytes': usage.total, 'free_bytes': usage.free, 'used_bytes': usage.used,
                             'observed_at': self.now(), 'protection': 'unknown'})
            except (OSError, subprocess.TimeoutExpired, ValueError):
                rows.append({'name': path.name or 'Internal storage', 'path': str(path),
                             'error': 'Storage observation unavailable', 'observed_at': None,
                             'total_bytes': None, 'free_bytes': None, 'used_bytes': None, 'protection': 'unknown'})
        return rows

    def logs(self, name):
        service = registry.load(self.config_path).get(name)
        if not service or not service.logs.get('path'):
            raise ValueError('No registered log file for this service')
        path = Path(str(service.logs['path'])).expanduser()
        with path.open('rb') as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 16384))
            data = stream.read(16384)
        if size > 16384:
            # A mid-line tail may have lost the password/token label. Drop the
            # fragment before applying redaction to complete operational lines.
            split = data.find(b'\n')
            data = data[split + 1:] if split >= 0 else b''
        return {'text': redact(data.decode('utf-8', errors='replace')),
                'truncated': size > 16384, 'observed_at': self.now()}

    def start(self):
        if self._thread is not None:
            return
        def loop():
            while not self._stop.is_set():
                self.collect_once()
                self._stop.wait(self.interval)
        self._thread = threading.Thread(target=loop, name='homed-observations', daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
