"""Real SQLite migration/rollback/concurrency tests using private temporary homes."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from khroxy_lib.storage import Store

OUT = {'protocol':'socks','settings':{'servers':[{'address':'127.0.0.1','port':1080}]}}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'state.json'
        self.state = {'version':1,'profiles':{'main':OUT,'spare':OUT},'apps':{
            'fixed':{'port':20820,'profile':'main','command':['fixture']},
            'dynamic':{'port':20821,'profile':'@fastest','command':['fixture']}},
            'subscriptions':{'source':{'profiles':['main','spare'],'url':'https://fixture.invalid/private-test-source'}},
            'active_profile':'main','fastest_profile':'main','unknown_setting':{'keep':True}}
        self.raw = json.dumps(self.state).encode(); self.path.write_bytes(self.raw)
        self.store = Store(self.path)

    def test_unsafe_linked_source_and_fifo_backup_refused_without_data_loss(self):
        linked = self.root / 'linked-source'
        os.link(self.path, linked)
        with self.assertRaisesRegex(ValueError, 'Unsafe'): self.store.migrate()
        self.assertEqual(self.path.read_bytes(), self.raw)
        linked.unlink()
        backup = self.path.with_name('state.json.pre-sqlite')
        os.mkfifo(backup)
        with self.assertRaisesRegex(ValueError, 'unsafe'): self.store.migrate()
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.store.path.exists())

    def test_migration_preserves_all_fields_and_original_bytes(self):
        self.assertTrue(self.store.migrate())
        self.assertEqual(self.store.load(), self.state)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.path.with_name('state.json.pre-sqlite').read_bytes(), self.raw)
        for path in (self.path,self.store.path,self.path.with_name('state.json.pre-sqlite')):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_repeat_and_concurrent_migration_do_not_duplicate(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: Store(self.path).migrate(), range(4)))
        self.assertEqual(sum(results), 1)
        self.assertFalse(self.store.migrate())
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM profiles').fetchone()[0], 2)

    def test_fresh_initialization(self):
        self.path.unlink()
        self.store.migrate()
        self.assertEqual(self.store.load()['profiles'], {})
        self.assertTrue(self.path.exists())

    def test_interrupted_migration_retains_source_and_backup(self):
        with patch('khroxy_lib.storage.os.replace', side_effect=OSError('injected interruption')):
            with self.assertRaises(OSError): self.store.migrate()
        self.assertFalse(self.store.exists)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.path.with_name('state.json.pre-sqlite').read_bytes(), self.raw)
        self.assertTrue(self.store.migrate())

    def test_corrupt_legacy_never_creates_database(self):
        self.path.write_bytes(b'{invalid')
        with self.assertRaises(ValueError): self.store.migrate()
        self.assertFalse(self.store.exists)
        self.assertEqual(self.path.read_bytes(), b'{invalid')

    def test_database_transaction_failure_rolls_back_all_collections(self):
        self.store.migrate()
        with self.store.connection() as db:
            db.execute("CREATE TRIGGER fail_profile BEFORE INSERT ON profiles WHEN NEW.name='broken' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        updated = copy.deepcopy(self.state); updated['profiles']['broken'] = OUT
        with self.assertRaises(ValueError): self.store.save(updated)
        self.assertEqual(self.store.load(), self.state)

    def test_backup_restore_roundtrip_preserves_archived_profiles_and_history(self):
        self.store.migrate(); self.store.lifecycle('spare','disable'); self.store.lifecycle('spare','delete')
        self.store.append('measurements','main',{'ok':True,'ms':10})
        dest = self.root / 'backup.json'; self.store.export(dest)
        self.store.lifecycle('spare','restore'); self.store.lifecycle('spare','enable')
        self.store.restore(dest)
        self.assertNotIn('spare',self.store.load()['profiles'])
        self.store.lifecycle('spare','restore')
        self.assertFalse(self.store.load()['profile_options']['spare']['enabled'])
        self.assertEqual(self.store.history('measurements')[0]['data']['ms'],10)
        self.assertEqual(dest.stat().st_mode & 0o777,0o600)
        self.assertEqual(len(list(self.root.glob('before-restore-*.json'))),1)

    def test_invalid_backup_preserves_database(self):
        self.store.migrate(); bad = self.root/'bad.json'
        for payload in ({}, {'format':'khroxy-backup','version':1,'state':self.state,'profiles':[],'history':{}}):
            bad.write_text(json.dumps(payload))
            with self.assertRaises(ValueError): self.store.restore(bad)
            self.assertEqual(self.store.load(), self.state)
        self.assertEqual(list(self.root.glob('before-restore-*')),[])

    def test_backup_never_overwrites_existing_file_or_follows_symlink(self):
        self.store.migrate(); target = self.root/'keep'; target.write_text('keep')
        link = self.root/'link'; link.symlink_to(target)
        for path in (target,link):
            with self.assertRaises(FileExistsError): self.store.export(path)
        self.assertEqual(target.read_text(),'keep')

    def test_database_symlink_refused(self):
        self.store.path.symlink_to(self.path)
        with self.assertRaises(ValueError): self.store.migrate()
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_selected_or_fixed_profile_not_deleted_or_purged(self):
        self.store.migrate()
        for action in ('delete','purge'):
            with self.assertRaisesRegex(ValueError,'assigned/selected'):
                self.store.lifecycle('main',action)

    def test_purge_requires_soft_delete_and_removes_only_target_history(self):
        self.store.migrate(); self.store.append('measurements','spare',{'ok':False})
        self.store.append('measurements','main',{'ok':True})
        with self.assertRaises(ValueError): self.store.lifecycle('spare','purge')
        self.store.lifecycle('spare','delete'); self.store.lifecycle('spare','purge')
        self.assertEqual(len(self.store.history('measurements')),1)
        with self.assertRaises(ValueError): self.store.lifecycle('spare','restore')

    def test_concurrent_history_writes_and_reads(self):
        self.store.migrate()
        def work(index):
            self.store.append('measurements','main',{'index':index})
            self.assertEqual(self.store.load()['apps'],self.state['apps'])
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(work,range(40)))
        self.assertEqual(len(self.store.history('measurements')),40)
        with self.store.connection() as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_real_cli_migration_and_legacy_command_compatibility(self):
        repo = Path(__file__).parent
        home = self.root/'home'; config = home/'.config/khroxy'; config.mkdir(parents=True)
        (config/'state.json').write_bytes(self.raw)
        env = dict(os.environ,HOME=str(home),PYTHONDONTWRITEBYTECODE='1')
        def cli(*args):
            return subprocess.run([str(repo/'khroxy'),*args],env=env,capture_output=True,text=True,timeout=5)
        self.assertEqual(cli('storage','migrate').returncode,0)
        result = cli('profiles'); self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('main',result.stdout); self.assertNotIn('private-test-source',result.stdout)
        self.assertEqual(cli('profile','disable','spare').returncode,0)
        self.assertEqual(cli('storage','status').stdout.strip(),'{"backend": "sqlite", "schema": 1}')
        self.assertEqual((config/'state.json').read_bytes(),self.raw)


if __name__ == '__main__': unittest.main()
