"""Independent parser and real CLI import regressions; all credentials fictional."""
import base64
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from khroxy_lib import importing

ROOT = Path(__file__).parent
loader=importlib.machinery.SourceFileLoader('khroxy_import_tests',str(ROOT/'khroxy'))
spec=importlib.util.spec_from_loader(loader.name,loader); p=importlib.util.module_from_spec(spec);loader.exec_module(p)
VLESS='vless://00000000-0000-4000-8000-000000000001@fixture.invalid:443?security=tls'
SECRET='fictional-credential-DO-NOT-LOG'


class ImportTests(unittest.TestCase):
    def test_http_https_socks_authentication_retained(self):
        for scheme, protocol in (('http','http'),('https','http'),('socks5','socks')):
            node=p.parse_share(f'{scheme}://fixture:{SECRET}@proxy.invalid:1080',allow_plaintext=True)
            self.assertEqual(node['protocol'],protocol)
            self.assertEqual(node['settings']['servers'][0]['users'][0]['pass'],SECRET)
            self.assertEqual(node['streamSettings']['security'],'tls' if scheme=='https' else 'none')

    def test_plaintext_opt_in_required(self):
        for link in ('http://proxy.invalid:80','socks5://proxy.invalid:1080'):
            with self.assertRaisesRegex(ValueError,'Plaintext'): p.parse_share(link)

    def test_hysteria2_tls_and_obfuscation_retained(self):
        node=p.parse_share(f'hy2://{SECRET}@proxy.invalid:443?sni=cert.invalid&obfs=salamander&obfs-password=fixture')
        self.assertEqual(node['protocol'],'hysteria2')
        self.assertEqual(node['settings']['servers'][0]['password'],SECRET)
        self.assertEqual(node['settings']['obfs']['type'],'salamander')
        self.assertEqual(node['streamSettings']['tlsSettings']['serverName'],'cert.invalid')

    def test_adversarial_options_and_ports_rejected_without_secret_in_error(self):
        for suffix in ('?insecure=1','?sni=a&sni=b','?unknown='+SECRET,':0',':-1',':'+SECRET):
            link=f'hy2://{SECRET}@proxy.invalid'+suffix
            with self.subTest(suffix=suffix), self.assertRaises(ValueError) as error:
                p.parse_share(link)
            self.assertNotIn(SECRET,str(error.exception))

    def test_sip008_and_base64_json(self):
        document={'version':1,'servers':[{'server':'proxy.invalid','server_port':8388,'method':'aes-128-gcm','password':SECRET,'plugin':''}]}
        raw=json.dumps(document).encode()
        nodes=p.parse_subscription(raw)
        self.assertEqual(nodes,p.parse_subscription(base64.b64encode(raw)))
        self.assertEqual(nodes[0]['settings']['servers'][0]['password'],SECRET)

    def test_xray_extracts_outbounds_only_without_exposing_listeners(self):
        outbound=p.parse_share(VLESS)
        nodes=p.parse_subscription(json.dumps({'inbounds':[{'listen':'0.0.0.0','port':9999}],'outbounds':[dict(outbound,tag='source-label')]}).encode())
        self.assertEqual(nodes,[outbound])
        self.assertNotIn('tag',nodes[0])

    def test_security_sensitive_xray_json_and_plugins_rejected(self):
        node=p.parse_share(VLESS)
        node['streamSettings']['tlsSettings']['allowInsecure']=True
        for document in ({'outbounds':[node]}, {'protocol':'freedom','settings':{}},
                         {'servers':[{'server':'proxy.invalid','server_port':80,'method':'aes-128-gcm','password':SECRET,'plugin':'unreviewed'}]}):
            with self.assertRaises(ValueError) as error: p.parse_subscription(json.dumps(document).encode())
            self.assertNotIn(SECRET,str(error.exception))

    def test_multiline_duplicate_import_sources_keep_shared_ownership(self):
        node=p.parse_share(VLESS)
        state={'version':1,'profiles':{},'apps':{},'subscriptions':{}}
        one,summary=importing.merge(state,[node,node],'one')
        two,summary2=importing.merge(one,[node],'two')
        self.assertEqual(len(two['profiles']),1)
        self.assertEqual(two['import_sources']['one'],two['import_sources']['two'])
        self.assertEqual(summary['added'],1); self.assertEqual(summary2['added'],0)

    def test_subscription_dedup_and_removal_keep_other_source(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p,'ROOT',Path(tmp)), patch.object(p,'STATE',Path(tmp)/'state.json'), \
             patch.object(p,'EVENTS',Path(tmp)/'events.log'), patch.object(p,'get_feed_with_mode',return_value=VLESS.encode()), \
             contextlib.redirect_stdout(io.StringIO()):
            state={'version':1,'profiles':{},'apps':{},'subscriptions':{name:{'profiles':[],'mode':'direct','url':'https://fixture.invalid/feed'} for name in ('one','two')}}
            first=p.update_feed(state,'one'); second=p.update_feed(first,'two')
            self.assertEqual(len(second['profiles']),1)
            p.remove_feed(second,'one'); final=p.load()
            self.assertEqual(len(final['profiles']),1)
            self.assertEqual(set(final['subscriptions']['two']['profiles']),set(final['profiles']))

    def test_subscription_reusing_manual_profile_never_deletes_it(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p,'ROOT',Path(tmp)), patch.object(p,'STATE',Path(tmp)/'state.json'), \
             patch.object(p,'EVENTS',Path(tmp)/'events.log'), patch.object(p,'get_feed_with_mode',return_value=VLESS.encode()), \
             contextlib.redirect_stdout(io.StringIO()):
            state={'version':1,'profiles':{'manual':p.parse_share(VLESS)},'apps':{},'subscriptions':{'one':{'profiles':[],'mode':'direct','url':'https://fixture.invalid/feed'}}}
            updated=p.update_feed(state,'one'); p.remove_feed(updated,'one')
            self.assertIn('manual',p.load()['profiles'])

    def test_real_cli_import_dedup_and_failed_batch_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(os.environ,HOME=tmp,PYTHONDONTWRITEBYTECODE='1')
            def cli(*args,raw=''):
                return subprocess.run([str(ROOT/'khroxy'),*args],env=env,input=raw,text=True,capture_output=True,timeout=5)
            self.assertEqual(cli('init').returncode,0)
            for _ in range(2):
                result=cli('import-data','--source','fixture',raw=VLESS+'\nhttps://fixture:'+SECRET+'@proxy.invalid:443\n')
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertNotIn(SECRET,result.stdout+result.stderr)
            self.assertEqual(json.loads(result.stdout)['added'],0)
            before=cli('profiles').stdout
            failed=cli('import-data',raw=VLESS+'\nunsupported://'+SECRET)
            self.assertNotEqual(failed.returncode,0)
            self.assertNotIn(SECRET,failed.stderr)
            self.assertEqual(cli('profiles').stdout,before)


if __name__=='__main__': unittest.main()
