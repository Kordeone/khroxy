"""Rotation precedence and real native engine selector integration."""
import copy
import contextlib
import io
import http.client
import subprocess
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from khroxy_lib import engines,rotation
from tests_support.engines import paths,process,free_port
from test_xray_routing import Server,Proxy

ROOT=Path(__file__).parent
loader=importlib.machinery.SourceFileLoader('khroxy_rotation_tests',str(ROOT/'khroxy'))
spec=importlib.util.spec_from_loader(loader.name,loader);p=importlib.util.module_from_spec(spec);loader.exec_module(p)
OUT={'protocol':'http','settings':{'servers':[{'address':'127.0.0.1','port':8080}]}}


class RotationPolicyTests(unittest.TestCase):
    def setUp(self):
        now=time.time();self.state={'profiles':{'one':OUT,'two':OUT,'three':OUT},'apps':{},'fastest_profile':'one',
          'health':{name:{'ok':True,'ms':ms,'time':now} for name,ms in [('one',100),('two',20),('three',50)]}}
    def test_disabled_profiles_never_selected(self):
        self.state['profile_options']={'two':{'enabled':False}}
        self.assertEqual(p.choose_best(self.state),'three')
    def test_manual_active_and_fixed_mappings_untouched(self):
        self.state.update(active_profile='one',selection_mode='active',apps={'fixed':{'profile':'one'},'active':{'profile':'@active'},'fast':{'profile':'@fastest'}})
        original=copy.deepcopy(self.state)
        best,reason=rotation.choose(p,self.state)
        self.assertEqual(best,'two');self.assertEqual(self.state,original)
    def test_pinned_selection_retained_and_disabled_overrides_pin(self):
        self.state['profile_options']={'one':{'pinned':True}}
        self.assertEqual(rotation.choose(p,self.state),('one','pinned'))
        self.state['profile_options']['one']['enabled']=False
        self.assertEqual(rotation.choose(p,self.state)[0],'two')
    def test_failure_threshold_and_cooldown(self):
        self.state['rotation_policy']={'enabled':True,'failure_threshold':2,'cooldown':100,'last_switch':time.time()}
        self.state['health']['one']['ok']=False
        rotation.observe(self.state,{'one':{'ok':False}})
        self.assertEqual(rotation.choose(p,self.state)[0],'one')
        rotation.observe(self.state,{'one':{'ok':False}})
        self.assertFalse(rotation.eligible(self.state,'one'))
        self.assertEqual(rotation.choose(p,self.state)[0],'two')
    def test_schedule_and_global_cooldown(self):
        now=time.time();self.state['rotation_policy']={'enabled':True,'interval':30,'cooldown':10,'last_switch':now-100}
        self.state['health']['one']['ms']=10
        self.assertEqual(rotation.choose(p,self.state,now=now),('two','schedule'))
        self.state['rotation_policy']['last_switch']=now
        self.assertEqual(rotation.choose(p,self.state,now=now),('one','cooldown'))
    def test_zero_healthy_and_stale_candidates_not_direct(self):
        self.state['fastest_profile']=None
        for item in self.state['health'].values():item['ok']=False
        self.assertIsNone(rotation.choose(p,self.state)[0])
        self.state['health']['two'].update(ok=True,time=0)
        self.assertIsNone(rotation.choose(p,self.state)[0])
    def test_prepared_runtime_does_not_skip_unapplied_api_switch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p,'ROOT',Path(tmp)),patch.object(p,'STATE',Path(tmp)/'state.json'),patch.object(p,'RUNTIME',Path(tmp)/'runtime.json'),patch.object(p,'EVENTS',Path(tmp)/'events.log'):
            state=copy.deepcopy(self.state);state.update(version=1,subscriptions={},session_safe_fastest=True,
                apps={'dynamic':{'port':20820,'profile':'@fastest','command':['fixture']}})
            p.write_file(p.STATE,p.serial(state))
            p.RUNTIME.write_bytes(p.serial(p.build_runtime(dict(state,fastest_profile='two'))))
            with patch.object(p,'probe_profile',side_effect=[{'ok':True,'ms':ms,'time':int(time.time())} for ms in (100,20,50)]), \
                 patch.object(p,'running',return_value=True),patch.object(p,'switch_fastest_live',return_value=True) as switch,contextlib.redirect_stdout(io.StringIO()):
                p.background_health()
            switch.assert_called_once();self.assertEqual(p.load()['fastest_profile'],'two')

    def test_invalid_policy_rejected(self):
        for settings in ({'interval':0},{'cooldown':-1},{'failure_threshold':100}):
            with self.assertRaises(ValueError):rotation.policy({'rotation_policy':settings})


@unittest.skipUnless(len(paths())==3,'Set all three KHROXY_TEST_* paths for native selectors')
class NativeRotationTests(unittest.TestCase):
    def test_each_engine_switches_new_streams_preserving_existing_and_fixed(self):
        for name,binary in paths().items():
            with self.subTest(engine=name),tempfile.TemporaryDirectory() as tmp,Server(('127.0.0.1',0),Proxy) as one,Server(('127.0.0.1',0),Proxy) as two:
                for server,label in ((one,b'one'),(two,b'two')):
                    server.label=label;threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.shutdown)
                def outbound(server):return {'protocol':'http','settings':{'servers':[{'address':'127.0.0.1','port':server.server_address[1]}]}}
                dynamic,fixed=free_port(),free_port()
                state={'profiles':{'one':outbound(one),'two':outbound(two)},'apps':{'dynamic':{'port':dynamic,'profile':'@fastest','command':['fixture']},'fixed':{'port':fixed,'profile':'one','command':['fixture']}},
                       'fastest_profile':'one','session_safe_fastest':True,'engine':name,'engine_binaries':{name:str(binary)}}
                engines.prepare_management(state)
                with patch.object(p,'ROOT',Path(tmp)),patch.object(p,'RUNTIME',Path(tmp)/'runtime.json'),patch.object(p,'EVENTS',Path(tmp)/'events.log'):
                    config=p.build_runtime(state);engines.validate(binary,name,config,Path(tmp));p.RUNTIME.write_text(json.dumps(config))
                    with process(binary,name,p.RUNTIME,port=dynamic) as child:
                        inodes=set()
                        for fd in Path(f'/proc/{child.pid}/fd').iterdir():
                            try:
                                target=fd.readlink().as_posix()
                                if target.startswith('socket:['):inodes.add(target[8:-1])
                            except OSError:pass
                        listeners={int(line.split()[1].split(':')[1],16) for line in Path(f'/proc/{child.pid}/net/tcp').read_text().splitlines()[1:] if line.split()[3]=='0A' and line.split()[9] in inodes}
                        expected={dynamic,fixed}
                        if name=='sing-box':
                            management=state['engine_management']['sing-box'];expected.add(management['port'])
                            connection=http.client.HTTPConnection('127.0.0.1',management['port'],timeout=2)
                            connection.request('GET','/proxies/khroxy-fastest');response=connection.getresponse();self.assertEqual(response.status,401);response.read();connection.close()
                        self.assertEqual(listeners,expected)
                        def tunnel(port):
                            sock=socket.create_connection(('127.0.0.1',port),timeout=3);self.addCleanup(sock.close)
                            stream=sock.makefile('rwb',buffering=0);self.addCleanup(stream.close)
                            stream.write(b'CONNECT fixture.invalid:443 HTTP/1.1\r\nHost: fixture.invalid:443\r\n\r\n')
                            self.assertIn(b'200',stream.readline())
                            while stream.readline() not in (b'\r\n',b'\n',b''):pass
                            return stream
                        def query(stream):stream.write(b'fixture\n');return stream.readline().strip()
                        before=tunnel(dynamic);self.assertEqual(query(before),b'one')
                        proposed=dict(state,fastest_profile='two')
                        self.assertTrue(p.switch_fastest_live(state,proposed))
                        self.assertEqual(query(tunnel(dynamic)),b'two')
                        self.assertEqual(query(before),b'one');self.assertEqual(query(tunnel(fixed)),b'one')
                        # Apply a target but lose the reply: actual native rollback must
                        # restore the prior selection without breaking existing streams.
                        if name=='sing-box':
                            original=rotation.sing_request;first=[True]
                            def lost_reply(settings,method,target=None):
                                result=original(settings,method,target)
                                if method=='PUT' and first[0]:first[0]=False;raise TimeoutError('injected lost reply')
                                return result
                            with patch.object(rotation,'sing_request',side_effect=lost_reply):
                                self.assertFalse(p.switch_fastest_live(proposed,state))
                        else:
                            original=subprocess.run;first=[True]
                            def lost_reply(command,**kw):
                                result=original(command,**kw)
                                if command[1:3]==['api','bo'] and first[0]:
                                    first[0]=False;return subprocess.CompletedProcess(command,1,'','injected lost reply')
                                return result
                            with patch.object(p.subprocess,'run',side_effect=lost_reply):
                                self.assertFalse(p.switch_fastest_live(proposed,state))
                        self.assertEqual(query(tunnel(dynamic)),b'two')
                        self.assertEqual(query(before),b'one');self.assertEqual(query(tunnel(fixed)),b'one')


if __name__=='__main__':unittest.main()
