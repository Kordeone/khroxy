"""Routing/listener behavior through real cores and loopback fixture origins."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from khroxy_lib import engines, networking
from khroxy_lib.storage import Store
from tests_support.engines import paths, process, free_port

ROOT=Path(__file__).parent
loader=importlib.machinery.SourceFileLoader('khroxy_network_tests',str(ROOT/'khroxy'))
spec=importlib.util.spec_from_loader(loader.name,loader);app=importlib.util.module_from_spec(spec);loader.exec_module(app)
AUTH={'username':'fixture','password':'fictional-only-password'}


def state():
    return {'version':1,'profiles':{'one':app.parse_share('http://127.0.0.1:12345',allow_plaintext=True)},
            'apps':{},'subscriptions':{},'active_profile':'one','fastest_profile':'one'}


class NetworkingUnitTests(unittest.TestCase):
    def test_rejects_public_colliding_privileged_or_unsafe_listeners(self):
        base=state();base['apps']['old']={'port':20820,'profile':'one','command':['fixture']}
        for values in ({'listen':'0.0.0.0'},{'listen':'localhost'},{'port':80},{'port':20820},{'port':True},
                       {'protocol':'shadowsocks'},{'auth':{'username':'fixture','password':''}}):
            with self.subTest(values=values):
                item=dict(protocol='socks',listen='127.0.0.1',port=12346,profile='one');item.update(values)
                with self.assertRaises(ValueError):networking.validate(dict(base,listeners={'extra':item}))

    def test_validates_rule_matchers_and_dns_without_silent_options(self):
        for rule in ({'id':'test','action':'direct'},{'id':'test','action':'proxy','all':True},
                     {'id':'test','action':'block','cidrs':['127.0.0.1/8']},
                     {'id':'test','action':'block','domains':['regexp:secret']},
                     {'id':'test','action':'direct','profile':'one','all':True}):
            with self.assertRaises(ValueError):networking.validate(dict(state(),routing_rules=[rule]))
        for hosts in ({'fixture.invalid':['not-an-ip']},{'https://private.invalid':['127.0.0.1']}):
            with self.assertRaises(ValueError):networking.validate(dict(state(),dns_policy={'mode':'static','hosts':hosts}))

    def test_session_safe_listener_and_rule_use_fastest_balancer(self):
        value=state();value.update(session_safe_fastest=True,
            listeners={'extra':{'protocol':'socks','port':12346,'profile':'@fastest'}},
            routing_rules=[{'id':'other','action':'proxy','profile':'@fastest','ports':[443]}])
        config=app.build_runtime(value)
        self.assertTrue(all(r.get('balancerTag')=='khroxy-fastest' for r in config['routing']['rules']))
        self.assertEqual(config['outbounds'][0]['protocol'],'blackhole')
        value['engine']='sing-box';engines.prepare_management(value)
        self.assertFalse(app.build_runtime(value)['outbounds'][0]['interrupt_exist_connections'])

    def test_cli_transaction_failure_and_running_runtime_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'ROOT',Path(tmp)),patch.object(app,'STATE',Path(tmp)/'state.json'),patch.object(app,'RUNTIME',Path(tmp)/'runtime.json'):
            app.write_file(app.STATE,app.serial(state()));Store(app.STATE).migrate();before=Store(app.STATE).load()
            app.RUNTIME.write_bytes(b'previous')
            with patch.object(app,'running',return_value=False),patch.object(app,'xray_test',side_effect=ValueError('native validation failed')):
                with self.assertRaises(ValueError):networking.cli(SimpleNamespace(**vars(app)),'listener',['add','extra','--protocol','socks','--port','12346','--profile','one'])
            self.assertEqual(Store(app.STATE).load(),before);self.assertEqual(app.RUNTIME.read_bytes(),b'previous')
            with patch.object(app,'running',return_value=True):
                with self.assertRaisesRegex(ValueError,'active sessions'):networking.cli(SimpleNamespace(**vars(app)),'dns',['set','system'])
            self.assertEqual(Store(app.STATE).load(),before)

    def test_assigned_profile_lifecycle_and_backup_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';value=state();value.pop('active_profile');value.pop('fastest_profile')
            value['listeners']={'extra':{'protocol':'http','port':12346,'profile':'one','auth':AUTH}}
            path.write_text(json.dumps(value));store=Store(path);store.migrate()
            with self.assertRaises(ValueError):store.lifecycle('one','delete')
            backup=Path(tmp)/'backup';store.export(backup);store.restore(backup)
            self.assertEqual(store.load()['listeners'],value['listeners'])
            self.assertEqual(backup.stat().st_mode&0o777,0o600)
            bad=copy.deepcopy(value);bad['profiles']['one']['settings']['servers'][0]['address']='upstream.invalid'
            bad['dns_policy']={'mode':'static','hosts':{}}
            with self.assertRaisesRegex(ValueError,'literal proxy-server IPs'):app.build_runtime(bad)


class Origin(BaseHTTPRequestHandler):
    marker=b'direct-origin'
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Length',str(len(self.marker)));self.end_headers();self.wfile.write(self.marker)
    def log_message(self,*args):pass

class Upstream(Origin):
    marker=b'assigned-upstream'
    def do_CONNECT(self):
        # Deterministic HTTP tunnel fixture; never resolves or dials a target.
        self.send_response(200);self.end_headers()
        while self.rfile.readline(8192) not in (b'\r\n',b'\n',b''): pass
        self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(self.marker)).encode()+b'\r\nConnection: close\r\n\r\n'+self.marker)
        self.wfile.flush()



@unittest.skipUnless(len(paths())==3,'Set all three KHROXY_TEST_* paths')
class NetworkingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.servers=[]
        for handler in (Origin,Upstream):
            server=ThreadingHTTPServer(('127.0.0.1',0),handler);threading.Thread(target=server.serve_forever,daemon=True).start()
            self.servers.append(server);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.origin,self.upstream=self.servers
    def request(self,port,scheme='http',host='127.0.0.1',target='127.0.0.1',auth=False):
        proxy_host='['+host+']' if ':' in host else host
        args=['curl','--silent','--show-error','--fail','--noproxy','','--max-time','2','--proxy',f'{scheme}://{proxy_host}:{port}']
        if auth:args+=['--proxy-user',AUTH['username']+':'+AUTH['password']]
        return subprocess.run([*args,f'http://{target}:{self.origin.server_address[1]}/'],capture_output=True,timeout=4)
    def value(self,name):
        value=state();value['engine']=name
        value['profiles']['one']=app.parse_share(f'http://127.0.0.1:{self.upstream.server_address[1]}',allow_plaintext=True)
        value['apps']={'old':{'port':free_port(),'profile':'one','command':['fixture']}}
        value['listeners']={'socks':{'protocol':'socks','port':free_port(),'profile':'@active','auth':AUTH},
                            'http':{'protocol':'http','port':free_port(),'profile':'one','auth':AUTH}}
        return value
    def run_config(self,value,tag):
        config=app.build_runtime(value);name=value['engine'];engines.validate(paths()[name],name,config,self.root)
        path=self.root/(name+'-'+tag+'.json');path.write_text(json.dumps(config))
        return process(paths()[name],name,path,port=value['apps']['old']['port'])

    def test_http_socks_auth_mapping_precedence_proxy_loss_no_fallback(self):
        for name in paths():
            with self.subTest(engine=name):
                value=self.value(name)
                with self.run_config(value,'mapping'):
                    for kind in ('http','socks'):
                        port=value['listeners'][kind]['port'];scheme='socks5h' if kind=='socks' else 'http'
                        self.assertEqual(self.request(port,scheme,auth=True).stdout,b'assigned-upstream')
                        denied=self.request(port,scheme)
                        self.assertNotEqual(denied.stdout,b'assigned-upstream')
                    self.assertEqual(self.request(value['apps']['old']['port']).stdout,b'assigned-upstream')
                value['routing_rules']=[{'id':'deny','action':'block','inbounds':['listener-socks'],'ports':[self.origin.server_address[1]]},
                                        {'id':'allow','action':'direct','cidrs':['127.0.0.0/8']}]
                with self.run_config(value,'precedence'):
                    self.assertEqual(self.request(value['apps']['old']['port']).stdout,b'direct-origin')
                    blocked=self.request(value['listeners']['socks']['port'],'socks5h',auth=True)
                    self.assertNotEqual(blocked.stdout,b'direct-origin');self.assertNotEqual(blocked.returncode,0)
                value['routing_rules']=[]
                value['profiles']['one']=app.parse_share(f'http://127.0.0.1:{free_port()}',allow_plaintext=True)
                with self.run_config(value,'unavailable'):
                    result=self.request(value['apps']['old']['port'])
                    self.assertNotEqual(result.stdout,b'direct-origin');self.assertNotEqual(result.returncode,0)

    def test_static_dns_domain_rules_and_ipv6_loopback(self):
        for name in paths():
            with self.subTest(engine=name):
                value=self.value(name);value['dns_policy']={'mode':'static','hosts':{'fixture.invalid':['127.0.0.1']}}
                value['listeners']['socks']['listen']='::1'
                value['routing_rules']=[{'id':'resolve','action':'direct','domains':['fixture.invalid']},
                                        {'id':'deny','action':'block','suffixes':['invalid']}]
                with self.run_config(value,'dns'):
                    result=self.request(value['apps']['old']['port'],target='fixture.invalid')
                    self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,b'direct-origin')
                    ipv6=self.request(value['listeners']['socks']['port'],'socks5h',host='::1',target='fixture.invalid',auth=True)
                    self.assertEqual(ipv6.returncode,0,ipv6.stderr);self.assertEqual(ipv6.stdout,b'direct-origin')
                    denied=self.request(value['apps']['old']['port'],target='unknown.invalid')
                    self.assertNotEqual(denied.returncode,0)
                    # All listener fds bind only the two explicit loopback addresses.
                    tcp=Path('/proc/net/tcp').read_text()+Path('/proc/net/tcp6').read_text()
                    self.assertNotIn('00000000:%04X'%value['apps']['old']['port'],tcp)

    def test_static_dns_unknown_host_fails_without_system_fallback(self):
        for name in paths():
            with self.subTest(engine=name):
                value=self.value(name)
                value['dns_policy']={'mode':'static','hosts':{'fixture.invalid':['127.0.0.1']}}
                value['routing_rules']=[{'id':'resolve','action':'direct','all':True}]
                with self.run_config(value,'dns-missing'):
                    result=self.request(value['apps']['old']['port'],target='unknown.invalid')
                    self.assertNotEqual(result.stdout,b'direct-origin')
                    self.assertNotEqual(result.returncode,0)

    def test_optional_shadowsocks_inbounds_with_real_sing_box_client(self):
        for name in paths():
            with self.subTest(engine=name):
                value=self.value(name);port=free_port()
                value['listeners']={'ss':{'protocol':'shadowsocks','port':port,'profile':'one','method':'aes-128-gcm','auth':AUTH}}
                with self.run_config(value,'ss'):
                    client_port=free_port()
                    client={'log':{'level':'error'},'inbounds':[{'type':'http','listen':'127.0.0.1','listen_port':client_port}],
                            'outbounds':[{'type':'shadowsocks','server':'127.0.0.1','server_port':port,'method':'aes-128-gcm','password':AUTH['password']}]}
                    path=self.root/'ss-client.json';path.write_text(json.dumps(client))
                    with process(paths()['sing-box'],'sing-box',path,port=client_port):
                        result=self.request(client_port)
                        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,b'assigned-upstream')

    def test_real_cli_redacts_auth_and_preserves_existing_mapping(self):
        home=self.root/'home';root=home/'.config/khroxy';root.mkdir(parents=True)
        value=self.value('xray');value.pop('listeners');value['engine_binaries']={'xray':str(paths()['xray'])}
        (root/'state.json').write_text(json.dumps(value));Store(root/'state.json').migrate()
        env=dict(os.environ,HOME=str(home),XDG_RUNTIME_DIR=str(home),DBUS_SESSION_BUS_ADDRESS='unix:path='+str(home/'no-bus'))
        def cli(*args,input=None):
            prefix=['unshare','--user','--map-user','1000','--map-group','1000'] if os.geteuid()==0 else []
            result=subprocess.run([*prefix,str(ROOT/'khroxy'),*args],input=input,capture_output=True,text=True,env=env,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr);return result.stdout
        cli('listener','add','extra','--protocol','socks','--profile','one','--port',str(free_port()),'--auth-stdin',input=json.dumps(AUTH))
        self.assertNotIn(AUTH['password'],cli('listener','list'))
        cli('route','add','deny','--action','block','--domain','blocked.invalid')
        self.assertEqual(Store(root/'state.json').load()['apps'],value['apps'])
        cli('route','remove','deny');cli('listener','remove','extra')
        self.assertFalse(Store(root/'state.json').load()['listeners'])

if __name__=='__main__':unittest.main()
