"""Optional real-engine checks and loopback-only protocol end-to-end tests."""
import copy
import importlib.machinery
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from khroxy_lib import engines
from tests_support.engines import paths, process, free_port

ROOT=Path(__file__).parent
loader=importlib.machinery.SourceFileLoader('khroxy_engine_tests',str(ROOT/'khroxy'))
spec=importlib.util.spec_from_loader(loader.name,loader);p=importlib.util.module_from_spec(spec);loader.exec_module(p)
UUID='00000000-0000-4000-8000-000000000001'
PASSWORD='fictional-test-password'


class EngineUnitTests(unittest.TestCase):
    def test_unrepresentable_tls_and_transport_not_silently_discarded(self):
        node=p.parse_share(f'vless://{UUID}@proxy.invalid:443?security=tls&ech=fixture')
        config={'outbounds':[dict(node,tag='test')],'inbounds':[],'routing':{'rules':[]}}
        for name in ('v2ray','sing-box'):
            with self.assertRaises(ValueError): engines.adapt(config,name)
        self.assertEqual(engines.adapt(config,'xray'),config)

    def test_unknown_binary_and_protocol_rejected(self):
        with self.assertRaises(ValueError): engines.identify('/nonexistent','xray')
        with self.assertRaises(ValueError): engines.command('/bin/false','unknown','fixture')
        node=p.parse_share('hy2://fixture@127.0.0.1:443')
        for name in ('xray','v2ray'):
            with self.assertRaises(ValueError): engines.adapt({'outbounds':[node],'inbounds':[],'routing':{'rules':[]}},name)

    def test_sing_box_defaults_reject_unmatched_traffic(self):
        node=p.parse_share('socks5://127.0.0.1:1080',allow_plaintext=True)
        state={'profiles':{'one':node},'apps':{'test':{'port':20820,'profile':'one','command':['fixture']}},'engine':'sing-box'}
        result=p.build_runtime(state)
        self.assertEqual(result['route']['rules'][-1],{'action':'reject'})
        self.assertNotIn('direct',{n['type'] for n in result['outbounds']})
        self.assertEqual(result['inbounds'][0]['listen'],'127.0.0.1')

    def test_default_preview_hides_nested_credentials(self):
        config={'outbounds':[{'protocol':'http','settings':{'servers':[{'password':PASSWORD}]},'streamSettings':{'xhttpSettings':{'extra':{'opaque':PASSWORD}}}}]}
        self.assertNotIn(PASSWORD,json.dumps(engines.redact(config)))

    def test_auto_engine_selection_failure_preserves_state_and_runtime(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(p,'ROOT',Path(tmp)), patch.object(p,'STATE',Path(tmp)/'state.json'), \
             patch.object(p,'RUNTIME',Path(tmp)/'runtime.json'), patch.object(p,'running',return_value=False):
            state={'version':1,'profiles':{},'apps':{},'subscriptions':{}}
            p.write_file(p.STATE,p.serial(state));p.RUNTIME.write_bytes(b'previous-runtime')
            from types import SimpleNamespace
            app=SimpleNamespace(**vars(p))
            with patch.object(engines,'validate',side_effect=ValueError('unavailable')):
                with self.assertRaises(ValueError): engines.cli(app,['select','auto'])
            self.assertEqual(p.load(),state);self.assertEqual(p.RUNTIME.read_bytes(),b'previous-runtime')


class Origin(BaseHTTPRequestHandler):
    def do_GET(self):
        payload=b'khroxy-local-engine-fixture'
        self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    def log_message(self,*args): pass


@unittest.skipUnless(paths().get('sing-box') and paths().get('xray') and paths().get('v2ray'),'Set all three KHROXY_TEST_* engine paths for integration')
class RealEngineTests(unittest.TestCase):
    def test_real_native_sing_box_install_requires_no_xray(self):
        with tempfile.TemporaryDirectory() as tmp:
            env=dict(os.environ,HOME=tmp,PYTHONDONTWRITEBYTECODE='1',XDG_RUNTIME_DIR=tmp,DBUS_SESSION_BUS_ADDRESS='unix:path='+tmp+'/no-bus')
            result=subprocess.run([str(ROOT/'install.sh'),'--mode','native','--proxy-engine','sing-box','--engine-binary',str(paths()['sing-box'])],
                                  input='',capture_output=True,text=True,env=env,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((Path(tmp)/'.local/bin/xray').exists())
            cli=Path(tmp)/'.local/bin/khroxy'
            result=subprocess.run([str(cli),'engine','check'],capture_output=True,text=True,env=env,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['engine'],'sing-box')
            unit=(Path(tmp)/'.config/systemd/user/khroxy.service').read_text()
            self.assertIn('sing-box',unit);self.assertIn('"-c"',unit)

    def test_real_cli_engine_switch_roundtrip_preserves_configuration(self):
        from khroxy_lib.storage import Store
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);config=home/'.config/khroxy';config.mkdir(parents=True)
            node=p.parse_share('socks5://127.0.0.1:1080',allow_plaintext=True)
            state={'version':1,'profiles':{'fixed':node},'apps':{'fixture':{'port':free_port(),'profile':'fixed','command':['fixture']}},'subscriptions':{}}
            (config/'state.json').write_text(json.dumps(state));Store(config/'state.json').migrate()
            env=dict(os.environ,HOME=tmp,PYTHONDONTWRITEBYTECODE='1',XDG_RUNTIME_DIR=tmp,DBUS_SESSION_BUS_ADDRESS='unix:path='+tmp+'/no-bus')
            for name in ('xray','sing-box','v2ray','xray'):
                result=subprocess.run([str(ROOT/'khroxy'),'engine','select',name,'--binary',str(paths()[name])],capture_output=True,text=True,env=env,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                saved=Store(config/'state.json').load()
                self.assertEqual(saved['profiles'],state['profiles']);self.assertEqual(saved['apps'],state['apps'])
                self.assertEqual(saved['engine'],name)
                preview=subprocess.run([str(ROOT/'khroxy'),'engine','preview'],capture_output=True,text=True,env=env,timeout=5)
                self.assertEqual(preview.returncode,0,preview.stderr)

    def test_all_supported_protocols_use_real_engines_and_verified_tls(self):
        binaries=paths()
        with tempfile.TemporaryDirectory() as tmp, ThreadingHTTPServer(('127.0.0.1',0),Origin) as origin:
            root=Path(tmp);threading.Thread(target=origin.serve_forever,daemon=True).start()
            self.addCleanup(origin.shutdown)
            cert,key=root/'fixture.crt',root/'fixture.key'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost',
                '-addext','subjectAltName=DNS:localhost,IP:127.0.0.1','-addext','basicConstraints=critical,CA:TRUE',
                '-keyout',str(key),'-out',str(cert)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=10)
            ports={kind:free_port(udp=kind=='hysteria2') for kind in ('vless','vmess','trojan','shadowsocks','http','https','socks','hysteria2')}
            tls={'enabled':True,'server_name':'localhost','certificate_path':str(cert),'key_path':str(key)}
            inbound=[]
            for kind,port in ports.items():
                item={'type':'http' if kind=='https' else kind,'tag':'server-'+kind,'listen':'127.0.0.1','listen_port':port}
                if kind in ('vless','vmess'): item['users']=[{'uuid':UUID}]
                elif kind in ('trojan','hysteria2'): item['users']=[{'password':PASSWORD}]
                elif kind=='shadowsocks': item.update(method='aes-128-gcm',password=PASSWORD)
                else: item['users']=[{'username':'fixture','password':PASSWORD}]
                if kind not in ('http','socks','shadowsocks'): item['tls']=tls
                inbound.append(item)
            server={'log':{'level':'error'},'inbounds':inbound,'outbounds':[{'type':'direct','tag':'local-fixture'}],
                    'route':{'rules':[{'ip_cidr':['127.0.0.0/8','::1/128'],'action':'route','outbound':'local-fixture'},{'action':'reject'}]}}
            env=dict(os.environ,SSL_CERT_FILE=str(cert),SSL_CERT_DIR=str(root))
            engines.validate(binaries['sing-box'],'sing-box',server,root)
            server_path=root/'server.json';server_path.write_text(json.dumps(server))
            with process(binaries['sing-box'],'sing-box',server_path,env,ports['http']):
                for name,binary in binaries.items():
                    for kind in sorted(engines.CAPABILITIES[name] | {'https'}):
                        with self.subTest(engine=name,protocol=kind):
                            port=ports[kind]
                            if kind in ('vless','vmess'): link=f'{kind}://{UUID}@127.0.0.1:{port}?security=tls&sni=localhost'
                            elif kind=='trojan': link=f'trojan://{PASSWORD}@127.0.0.1:{port}?sni=localhost'
                            elif kind=='hysteria2': link=f'hy2://{PASSWORD}@127.0.0.1:{port}?sni=localhost'
                            elif kind=='shadowsocks': link=f'ss://aes-128-gcm:{PASSWORD}@127.0.0.1:{port}'
                            else: link=f'{"socks5" if kind=="socks" else kind}://fixture:{PASSWORD}@127.0.0.1:{port}'
                            node=p.parse_share(link,allow_plaintext=True)
                            local_port=free_port()
                            state={'profiles':{'fixture':node},'apps':{'fixture':{'port':local_port,'profile':'fixture','command':['fixture']}},'engine':name}
                            config=p.build_runtime(state)
                            engines.validate(binary,name,config,root)
                            path=root/f'client-{name}-{kind}.json';path.write_text(json.dumps(config))
                            with process(binary,name,path,env,local_port):
                                result=subprocess.run(['curl','--silent','--show-error','--noproxy','','--max-time','5','--proxy',f'http://127.0.0.1:{local_port}',f'http://127.0.0.1:{origin.server_address[1]}/fixture'],capture_output=True,text=True,timeout=7)
                                self.assertEqual(result.returncode,0,result.stderr)
                                self.assertEqual(result.stdout,'khroxy-local-engine-fixture')


if __name__=='__main__': unittest.main()
