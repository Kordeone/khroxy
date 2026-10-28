"""Real SQLite persistence, ranking/freshness, offline metadata and redaction."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from khroxy_lib.storage import Store
from khroxy_lib.diagnostics import ranking,GeoData
from khroxy_lib.importing import fingerprint

ROOT=Path(__file__).parent
OUT={'protocol':'socks','settings':{'servers':[{'address':'192.0.2.2','port':1080,'users':[{'user':'fixture','pass':'fictional-password'}]}]}}


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.path=self.root/'state.json'
        self.state={'version':1,'profiles':{name:OUT for name in ('fast','slow','failed','stale','untested')},'apps':{},'subscriptions':{}}
        self.path.write_text(json.dumps(self.state));self.store=Store(self.path);self.store.migrate()
    def sample(self,name,ok=True,ms=10,stamp=None,download=100):
        self.store.append('measurements',name,{'ok':ok,'configuration_hash':fingerprint(OUT),'stages':{'http':{'ok':ok,'ms':ms,'category':'ok' if ok else 'tls'},'download':{'ok':ok,'bytes_per_second':download}}},time.time() if stamp is None else stamp)

    def test_fresh_stale_failed_and_untested_rank_separately(self):
        self.sample('fast',ms=10);self.sample('slow',ms=100);self.sample('failed',ok=False);self.sample('stale',stamp=time.time()-10000)
        rows=ranking(self.store)
        self.assertEqual([v['profile'] for v in rows[:2]],['fast','slow'])
        by_name={row['profile']:row for row in rows}
        self.assertEqual(by_name['failed']['category'],'tls')
        self.assertEqual(by_name['stale']['status'],'stale')
        self.assertEqual(by_name['untested']['status'],'untested')
        self.assertNotIn('fictional-password',json.dumps(rows));self.assertNotIn('192.0.2.2',json.dumps(rows))

    def test_changed_configuration_does_not_inherit_previous_success(self):
        self.sample('fast');updated=copy.deepcopy(self.state)
        updated['profiles']['fast']['settings']['servers'][0]['port']=9999
        self.store.save(updated)
        row=next(v for v in ranking(self.store) if v['profile']=='fast')
        self.assertEqual(row['status'],'untested');self.assertEqual(row['category'],'configuration-changed')

    def test_throughput_ranking_and_disabled_filter(self):
        self.sample('fast',download=100);self.sample('slow',download=1000)
        self.assertEqual(ranking(self.store,'download')[0]['profile'],'slow')
        self.store.lifecycle('slow','disable')
        self.assertNotIn('slow',{v['profile'] for v in ranking(self.store)})
        self.assertIn('slow',{v['profile'] for v in ranking(self.store,include_disabled=True)})

    def test_history_retention_and_pagination(self):
        for index in range(12):self.sample('fast',ms=index,stamp=index+1)
        self.assertEqual(len(self.store.history('measurements','fast',3,3)),3)
        removed=self.store.prune(5,5)
        self.assertEqual(removed['measurements'],7)
        self.assertEqual(len(self.store.history('measurements')),5)
        self.assertEqual(self.store.load()['profiles'],self.state['profiles'])

    def test_results_survive_real_cli_restart(self):
        self.sample('fast');home=self.root/'home';config=home/'.config/khroxy';config.mkdir(parents=True)
        target=Store(config/'state.json');(config/'state.json').write_text(json.dumps(self.state));target.migrate()
        self.store.export(self.root/'backup.json');target.restore(self.root/'backup.json')
        env=dict(os.environ,HOME=str(home),PYTHONDONTWRITEBYTECODE='1')
        for _ in range(2):
            result=subprocess.run([str(ROOT/'khroxy'),'results','rank'],env=env,text=True,capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]['profile'],'fast')

    def test_optional_offline_metadata_and_missing_dataset_leave_proxy_state(self):
        source=self.root/'geo.json'
        source.write_text(json.dumps({'version':1,'networks':[{'cidr':'192.0.2.0/24','country':'ZZ','asn':64500},{'cidr':'192.0.2.2/32','country':'YY','asn':64501}]}))
        data=GeoData(source).lookup('192.0.2.2');self.assertEqual(data['asn'],64501)
        self.assertIsNone(GeoData(source).lookup('proxy.invalid'))
        self.store.update_metadata('fast',data)
        row=next(v for v in ranking(self.store) if v['profile']=='fast')
        self.assertEqual(row['country'],'YY');self.assertEqual(self.store.load()['profiles'],self.state['profiles'])
        with self.assertRaises(ValueError):GeoData(self.root/'missing.json')

    def test_original_profile_options_and_unknown_metadata_preserved(self):
        home=self.root/'other';home.mkdir();path=home/'state.json';state=copy.deepcopy(self.state)
        state['profile_options']={'fast':{'enabled':False,'owner_preference':{'keep':True}}}
        path.write_text(json.dumps(state));store=Store(path);store.migrate()
        self.assertEqual(store.load()['profile_options'],state['profile_options'])
        store.update_metadata('fast',{'country':'ZZ'})
        self.assertEqual(store.load()['profile_options']['fast']['owner_preference'],{'keep':True})

    def test_explicit_empty_profile_options_preserved(self):
        root=self.root/'empty-options';root.mkdir();path=root/'state.json'
        state=dict(self.state,profile_options={});path.write_text(json.dumps(state))
        store=Store(path);store.migrate()
        self.assertEqual(store.load(),state)

    def test_old_writer_divergence_detected_without_losing_either_state(self):
        original=self.path.read_bytes();self.path.write_bytes(b'{"version":1,"profiles":{},"apps":{},"subscriptions":{}}')
        with self.assertRaisesRegex(ValueError,'Legacy JSON changed'):self.store.load()
        self.assertEqual(self.path.with_name('state.json.pre-sqlite').read_bytes(),original)
        self.store.export(self.root/'recovery.json')
        self.assertEqual(json.loads((self.root/'recovery.json').read_text())['state'],self.state)


if __name__=='__main__':unittest.main()
