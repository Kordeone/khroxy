# KHROXY managed module
"""Transactional local storage with atomic, recoverable legacy migration."""
from contextlib import contextmanager
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time

SCHEMA = 1
NAME = re.compile(r'^[a-z][a-z0-9_-]{0,30}$')
HISTORY = ('measurements', 'rotations', 'sessions', 'metadata')
MAX_BACKUP_BYTES = 16 * 1024 * 1024


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def validate(state):
    if not isinstance(state, dict) or state.get('version') != 1:
        raise ValueError('Unsupported state format')
    for field in ('profiles', 'apps', 'subscriptions'):
        if not isinstance(state.get(field, {}), dict):
            raise ValueError('Invalid state collection: ' + field)
    for name, outbound in state.get('profiles', {}).items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or not isinstance(outbound, dict) or not isinstance(outbound.get('protocol'), str):
            raise ValueError('Invalid profile record; no changes made')
    for app in state.get('apps', {}).values():
        if (not isinstance(app, dict) or not isinstance(app.get('profile'), str) or
                not isinstance(app.get('port'), int) or not 1 <= app['port'] <= 65535 or
                not isinstance(app.get('command'), list) or not app['command'] or
                not all(isinstance(part, str) for part in app['command'])):
            raise ValueError('Invalid application record; no changes made')
    options = state.get('profile_options',{})
    if not isinstance(options,dict) or any(not isinstance(v,dict) or type(v.get('enabled',True)) is not bool for v in options.values()):
        raise ValueError('Invalid profile options; source data retained')
    from .networking import validate
    validate(state)
    encode(state)
    return copy.deepcopy(state)


def exclusive_file(path, data):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


class Store:
    def __init__(self, legacy):
        self.legacy = Path(legacy)
        self.root = self.legacy.parent
        self.path = self.root / 'state.sqlite'

    @property
    def exists(self):
        if self.path.is_symlink():
            raise ValueError('Refusing symlink database')
        return self.path.is_file()

    def prepare(self):
        if self.root.is_symlink():
            raise ValueError('Refusing symlink configuration directory')
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)

    @contextmanager
    def connection(self):
        if not self.exists:
            raise ValueError('Database absent; run khroxy storage migrate')
        self.path.chmod(0o600)
        try:
            db = sqlite3.connect(self.path, timeout=10)
        except sqlite3.Error:
            raise ValueError('Database unavailable; source data retained') from None
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA busy_timeout=10000')
            if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA:
                raise ValueError('Unsupported database schema; source data retained')
            with db:
                yield db
        except sqlite3.Error:
            raise ValueError('Database operation failed; source data retained') from None
        finally:
            db.close()

    @staticmethod
    def schema(db):
        db.executescript('''
          CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
          CREATE TABLE profiles (name TEXT PRIMARY KEY, data TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)), meta TEXT NOT NULL DEFAULT '{}');
          CREATE TABLE subscriptions (name TEXT PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE measurements (id INTEGER PRIMARY KEY, profile TEXT, timestamp REAL, data TEXT NOT NULL);
          CREATE TABLE rotations (id INTEGER PRIMARY KEY, profile TEXT, timestamp REAL, data TEXT NOT NULL);
          CREATE TABLE sessions (id INTEGER PRIMARY KEY, profile TEXT, timestamp REAL, data TEXT NOT NULL);
          CREATE TABLE metadata (id INTEGER PRIMARY KEY, profile TEXT, timestamp REAL, data TEXT NOT NULL);
          PRAGMA user_version=1;
        ''')

    @staticmethod
    def save_in(db, state):
        state = validate(state)
        db.execute('INSERT INTO state(id,data) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (encode(state),))
        current = state.get('profiles', {})
        for (name,) in db.execute('SELECT name FROM profiles').fetchall():
            if name not in current:
                db.execute('UPDATE profiles SET deleted=1 WHERE name=?', (name,))
        for name, outbound in current.items():
            options=state.get('profile_options',{}).get(name,{})
            db.execute('INSERT INTO profiles(name,data,enabled,meta) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET data=excluded.data,deleted=0',
                       (name, encode(outbound), int(options.get('enabled',True)), encode({k:v for k,v in options.items() if k!='enabled'})))
        db.execute('DELETE FROM subscriptions')
        db.executemany('INSERT INTO subscriptions(name,data) VALUES(?,?)', [(n, encode(v)) for n,v in state.get('subscriptions', {}).items()])

    def migrate(self):
        self.prepare()
        lock = os.open(self.root / '.migration-lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.exists:
                self.load()
                return False
            if self.legacy.is_symlink():
                raise ValueError('Refusing symlink legacy state')
            if self.legacy.exists():
                info = self.legacy.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_size > MAX_BACKUP_BYTES:
                    raise ValueError('Unsafe or oversized legacy state; source retained')
                self.legacy.chmod(0o600)
            raw = self.legacy.read_bytes() if self.legacy.exists() else b'{"version":1,"profiles":{},"apps":{},"subscriptions":{}}'
            state = validate(json.loads(raw))
            backup = self.legacy.with_name(self.legacy.name + '.pre-sqlite')
            if backup.exists():
                info = backup.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or
                        info.st_size > MAX_BACKUP_BYTES or backup.read_bytes() != raw):
                    raise ValueError('Existing migration backup differs or is unsafe; review before migrating')
                backup.chmod(0o600)
            else:
                exclusive_file(backup, raw)
            if not self.legacy.exists():
                exclusive_file(self.legacy, raw)
            fd, name = tempfile.mkstemp(prefix='.migration-', dir=self.root)
            os.close(fd)
            try:
                db = sqlite3.connect(name)
                try:
                    self.schema(db)
                    with db:
                        self.save_in(db, state)
                    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Migration integrity check failed')
                finally:
                    db.close()
                with open(name, 'rb') as stream:
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try: os.fsync(directory)
                finally: os.close(directory)
            finally:
                Path(name).unlink(missing_ok=True)
            return True
        except sqlite3.Error:
            raise ValueError('Migration failed; original state and backup retained') from None
        finally:
            os.close(lock)

    def check_legacy(self):
        backup = self.legacy.with_name(self.legacy.name + '.pre-sqlite')
        if self.legacy.is_symlink() or backup.is_symlink():
            raise ValueError('Refusing symlink legacy state/backup')
        if self.legacy.is_file() and backup.is_file() and self.legacy.read_bytes() != backup.read_bytes():
            raise ValueError('Legacy JSON changed after migration; stop older writers and reconcile retained backups')

    def load(self):
        self.check_legacy()
        with self.connection() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT data FROM state WHERE id=1').fetchone()
            if row is None:
                raise ValueError('Database state missing; restore a backup')
            state = json.loads(row[0])
            state['profiles'] = {}
            had_options = 'profile_options' in state
            options = state.setdefault('profile_options', {})
            for name, raw, enabled, meta in db.execute('SELECT name,data,enabled,meta FROM profiles WHERE deleted=0 ORDER BY name'):
                state['profiles'][name] = json.loads(raw)
                if not enabled or meta != '{}':
                    options[name] = dict(options.get(name,{}), **json.loads(meta))
                    options[name]['enabled'] = bool(enabled)
                elif name in options:
                    options[name]['enabled'] = True
            if not options and not had_options:
                state.pop('profile_options', None)
            return state

    def save(self, state):
        self.check_legacy()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.save_in(db, state)

    def lifecycle(self, name, action):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            record = db.execute('SELECT data,deleted FROM profiles WHERE name=?', (name,)).fetchone()
            if record is None:
                raise ValueError('Unknown profile')
            state = json.loads(db.execute('SELECT data FROM state WHERE id=1').fetchone()[0])
            from .networking import assignments
            referenced = name in assignments(state) or name in (state.get('active_profile'), state.get('fastest_profile')) or any(a.get('profile') == name for a in state.get('apps', {}).values())
            if action in ('delete', 'purge') and referenced:
                raise ValueError('Profile is assigned/selected; change assignments first')
            if action in ('enable', 'disable'):
                db.execute('UPDATE profiles SET enabled=? WHERE name=?', (int(action=='enable'), name))
            elif action in ('restore', 'delete'):
                db.execute('UPDATE profiles SET deleted=? WHERE name=?', (int(action=='delete'), name))
                if action == 'restore': state.setdefault('profiles', {})[name] = json.loads(record[0])
                else: state.get('profiles', {}).pop(name, None)
                db.execute('UPDATE state SET data=? WHERE id=1', (encode(state),))
            elif action == 'purge':
                if not record[1]: raise ValueError('Soft-delete a profile before purge')
                db.execute('DELETE FROM profiles WHERE name=?', (name,))
                for table in HISTORY:
                    db.execute(f'DELETE FROM {table} WHERE profile=?', (name,))
            else:
                raise ValueError('Unknown lifecycle action')

    def append(self, table, profile, data, timestamp=None):
        if table not in HISTORY or (profile is not None and not NAME.fullmatch(profile)):
            raise ValueError('Invalid history record')
        timestamp = time.time() if timestamp is None else timestamp
        if not isinstance(timestamp,(int,float)) or not math.isfinite(timestamp) or timestamp<0:
            raise ValueError('Invalid history timestamp')
        with self.connection() as db:
            db.execute(f'INSERT INTO {table}(profile,timestamp,data) VALUES(?,?,?)', (profile, timestamp, encode(data)))

    def history(self, table, profile=None, limit=100, offset=0):
        if table not in HISTORY or not 1 <= limit <= 1000 or offset < 0:
            raise ValueError('Invalid history query')
        with self.connection() as db:
            rows = db.execute(f'SELECT id,profile,timestamp,data FROM {table} WHERE (? IS NULL OR profile=?) ORDER BY id DESC LIMIT ? OFFSET ?', (profile, profile, limit, offset)).fetchall()
            return [dict(id=i, profile=p, timestamp=t, data=json.loads(d)) for i,p,t,d in rows]

    @staticmethod
    def snapshot(db):
        return {'format':'khroxy-backup', 'version':1,
                'state':json.loads(db.execute('SELECT data FROM state').fetchone()[0]),
                'profiles':[list(row) for row in db.execute('SELECT name,data,enabled,deleted,meta FROM profiles ORDER BY name')],
                'history':{table:[list(row) for row in db.execute(f'SELECT profile,timestamp,data FROM {table} ORDER BY id')] for table in HISTORY}}

    @staticmethod
    def write_backup(destination, payload):
        raw = (encode(payload)+'\n').encode()
        if len(raw) > MAX_BACKUP_BYTES: raise ValueError('Backup exceeds size limit; use a reviewed database snapshot')
        exclusive_file(destination, raw)

    def export(self, destination):
        with self.connection() as db:
            db.execute('BEGIN')
            payload = self.snapshot(db)
        self.write_backup(destination, payload)

    def restore(self, source):
        source = Path(source)
        if source.is_symlink() or source.stat().st_size > MAX_BACKUP_BYTES:
            raise ValueError('Unsafe or oversized backup')
        payload = json.loads(source.read_bytes())
        if not isinstance(payload, dict) or payload.get('format') != 'khroxy-backup' or payload.get('version') != 1:
            raise ValueError('Unsupported backup format')
        try:
            state = validate(payload['state'])
            profiles = payload['profiles']
            for name, data, enabled, deleted, meta in profiles:
                validate({'version':1,'profiles':{name:json.loads(data)}})
                if enabled not in (0,1) or deleted not in (0,1) or not isinstance(json.loads(meta), dict):
                    raise ValueError('Invalid profile status in backup')
            for table in HISTORY:
                for profile, stamp, data in payload['history'][table]:
                    if profile is not None and not NAME.fullmatch(profile): raise ValueError('Invalid history profile')
                    if not isinstance(stamp, (int,float)): raise ValueError('Invalid history timestamp')
                    json.loads(data)
            if {n:json.loads(d) for n,d,e,deleted,m in profiles if not deleted} != state.get('profiles', {}):
                raise ValueError('Backup profile projection mismatch')
            if len({row[0] for row in profiles}) != len(profiles):
                raise ValueError('Duplicate backup records')
            encode(payload)
        except (ValueError, TypeError, KeyError):
            raise ValueError('Invalid backup; no changes made') from None
        # Validate before creating the recovery snapshot or changing the database.
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.write_backup(self.root / ('before-restore-' + str(time.time_ns()) + '.json'), self.snapshot(db))
            for table in ('profiles','subscriptions','state',*HISTORY): db.execute(f'DELETE FROM {table}')
            self.save_in(db, state)
            db.execute('DELETE FROM profiles')
            db.executemany('INSERT INTO profiles(name,data,enabled,deleted,meta) VALUES(?,?,?,?,?)', profiles)
            for table in HISTORY:
                db.executemany(f'INSERT INTO {table}(profile,timestamp,data) VALUES(?,?,?)', payload['history'][table])

    def update_metadata(self, name, values):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT meta FROM profiles WHERE name=? AND deleted=0',(name,)).fetchone()
            if row is None: raise ValueError('Unknown profile')
            metadata=json.loads(row[0]);metadata.update(values)
            db.execute('UPDATE profiles SET meta=? WHERE name=?',(encode(metadata),name))

    def prune(self, before, keep):
        if not isinstance(before,(int,float)) or not math.isfinite(before) or before<0 or not 1<=keep<=100000:
            raise ValueError('Invalid retention limits')
        counts={}
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for table in HISTORY:
                counts[table]=db.execute(f'DELETE FROM {table} WHERE timestamp<? OR id NOT IN (SELECT id FROM {table} ORDER BY id DESC LIMIT ?)',(before,keep)).rowcount
        return counts
