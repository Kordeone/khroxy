"""Machine-readable CLI, private exports and actual authenticated HTTP API."""
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import fcntl
import http.client
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from khroxy_lib import automation, api, importing, engines, supervisor
from khroxy_lib.storage import Store
from test_networking import Origin, Upstream
from tests_support.engines import paths,free_port

ROOT=Path(__file__).parent
loader=importlib.machinery.SourceFileLoader('khroxy_automation_tests',str(ROOT/'khroxy'))
spec=importlib.util.spec_from_loader(loader.name,loader);app=importlib.util.module_from_spec(spec);loader.exec_module(app)


class AutomationUnitTests(unittest.TestCase):
    def test_completions_include_every_original_command_and_parse_bash(self):
        original=set(re.findall(r"(?:sub\.add_parser|p = sub\.add_parser)\('([^']+)'",(ROOT/'khroxy').read_text()))
        self.assertFalse(original-set(automation.COMMANDS))
        for shell in ('bash','zsh','fish'):
            self.assertIn('khroxy',automation.completion(shell))
        result=subprocess.run(['bash','-n'],input=automation.completion('bash'),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('No automatic direct fallback',automation.MANUAL)
    def test_token_file_requires_private_owned_regular_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            token=Path(tmp)/'token';token.write_text('x'*43);token.chmod(0o600)
            self.assertEqual(api.token_file(token),'x'*43)
            token.chmod(0o644)
            with self.assertRaises(ValueError):api.token_file(token)
            token.chmod(0o600);link=Path(tmp)/'link';link.symlink_to(token)
            with self.assertRaises(OSError):api.token_file(link)
            token.write_text('x'*10)
            with self.assertRaises(ValueError):api.token_file(token)
        with self.assertRaises(ValueError):api.Server(('0.0.0.0',21090),None,'fixture')
    def test_private_subscription_formats_roundtrip_and_refuse_loss(self):
        nodes=[app.parse_share('ss://aes-128-gcm:fictional-password@127.0.0.1:12345')]
        state={'version':1,'profiles':{'fixture':nodes[0]},'apps':{},'subscriptions':{}}
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'load',return_value=state):
            interface=SimpleNamespace(**vars(app))
            for kind in ('json','base64','sip008'):
                path=Path(tmp)/kind;args=SimpleNamespace(format=kind,profile=None,output=str(path),include_secrets=True)
                result=automation.export(interface,args);self.assertTrue(result['contains_credentials'])
                self.assertEqual(path.stat().st_mode&0o777,0o600)
                parsed=app.parse_subscription(path.read_bytes(),allow_plaintext=True)
                self.assertEqual(importing.fingerprint(parsed[0]),importing.fingerprint(nodes[0]))
                with self.assertRaises(FileExistsError):automation.export(interface,args)
            args=SimpleNamespace(format='json',profile=None,output=None,include_secrets=False)
            self.assertNotIn('fictional-password',json.dumps(automation.export(interface,args)))
            args.include_secrets=True
            with self.assertRaises(ValueError):automation.export(interface,args)
            args.output=str(Path(tmp)/'not-written');args.format='sip008'
            state['profiles']['fixture']=app.parse_share('https://fictional:password@127.0.0.1:443')
            with self.assertRaises(ValueError):automation.export(interface,args)
            self.assertFalse(Path(args.output).exists())


@unittest.skipUnless(paths().get('xray'),'Set KHROXY_TEST_XRAY for real CLI/API integration')
class AutomationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name)
        self.root=self.home/'.config/khroxy';self.root.mkdir(parents=True)
        self.env=dict(os.environ,HOME=str(self.home),XDG_RUNTIME_DIR=str(self.home),DBUS_SESSION_BUS_ADDRESS='unix:path='+str(self.home/'no-bus'))
        self.state={'version':1,'profiles':{'one':app.parse_share('http://fictional:private-test-password@127.0.0.1:12345',allow_plaintext=True),
                                           'two':app.parse_share('socks5://127.0.0.1:12346',allow_plaintext=True)},
            'apps':{'fixture':{'profile':'@active','port':free_port(),'command':['fixture']}},'subscriptions':{},
            'active_profile':'one','fastest_profile':'one','engine':'xray','engine_binaries':{'xray':str(paths()['xray'])},
            'health':{'one':{'ok':True,'ms':20,'time':time.time()},'two':{'ok':True,'ms':10,'time':time.time()}}}
        (self.root/'state.json').write_text(json.dumps(self.state));self.store=Store(self.root/'state.json');self.store.migrate()
        self.addCleanup(self.cleanup)
    def cli(self,*args,check=True):
        result=subprocess.run([str(ROOT/'khroxy'),*args],env=self.env,capture_output=True,text=True,timeout=20)
        if check:self.assertEqual(result.returncode,0,result.stderr)
        return result
    def cleanup(self):
        self.cli('daemon','stop',check=False)
    def start_api(self):
        token=self.home/'api.token';self.cli('api','token','--output',str(token));self.token=token.read_text().strip();self.port=free_port()
        self.child=subprocess.Popen([str(ROOT/'khroxy'),'api','serve','--token-file',str(token),'--port',str(self.port)],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.assertIn('ready',self.child.stdout.readline());self.addCleanup(self.stop_api)
    def stop_api(self):
        if self.child.poll() is None:self.child.terminate()
        out,err=self.child.communicate(timeout=5)
        self.assertNotIn('private-test-password',out+err);self.assertNotIn(self.token,out+err)
    def request(self,path='/v1/status',method='GET',body=None,token=True,extra=None):
        headers={'Authorization':'Bearer '+self.token} if token else {}
        if body is not None:headers['Content-Type']='application/json'
        if extra:headers.update(extra)
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        try:
            connection.request(method,path,json.dumps(body) if body is not None else None,headers)
            response=connection.getresponse();raw=response.read();return response.status,json.loads(raw)
        finally:connection.close()
    def test_cli_json_pagination_filtering_selection_and_private_export(self):
        row=json.loads(self.cli('automation','profiles','--limit','1','--offset','1','--enabled','true').stdout)
        self.assertEqual(row['total'],2);self.assertEqual(row['profiles'][0]['name'],'two');self.assertNotIn('password',json.dumps(row))
        row=json.loads(self.cli('automation','profiles','--protocol','http','--search','one').stdout);self.assertEqual(row['total'],1)
        row=json.loads(self.cli('automation','select','two').stdout);self.assertTrue(row['applied']);self.assertEqual(self.store.load()['active_profile'],'two')
        row=json.loads(self.cli('automation','rotate').stdout);self.assertTrue(row['applied']);self.assertFalse(row['direct_fallback'])
        status=json.loads(self.cli('automation','status').stdout);self.assertEqual(status['schema'],1)
        path=self.home/'export';self.cli('export-data','--format','base64','--include-secrets','--output',str(path))
        self.assertEqual(len(app.parse_subscription(path.read_bytes(),allow_plaintext=True)),2)
        original=(self.root/'state.json').read_bytes()
        self.assertEqual(self.cli('completion','bash').returncode,0);self.assertIn('.TH KHROXY',self.cli('manual').stdout)
        self.assertEqual((self.root/'state.json').read_bytes(),original)
    def test_auth_required_for_reads_mutations_and_browser_host_rejected(self):
        self.start_api();before=self.store.load()
        self.assertEqual(self.request(token=False)[0],401)
        self.assertEqual(self.request('/v1/selection','POST',{'name':'two'},token=False)[0],401)
        self.assertEqual(self.request(extra={'Authorization':'Bearer wrong'})[0],401)
        self.assertEqual(self.request(extra={'Origin':'http://foreign.invalid'})[0],403)
        self.assertEqual(self.request(extra={'Host':'foreign.invalid'})[0],400)
        self.assertEqual(self.store.load(),before)
        self.assertEqual(self.request()[0],200)
        code,row=self.request('/v1/profiles?limit=1&offset=1&enabled=true');self.assertEqual(code,200);self.assertEqual(row['total'],2)
        self.assertNotIn('private-test-password',json.dumps(row))
        self.assertEqual(self.request('/v1/selection','POST',{'name':'two'})[0],200)
        self.assertEqual(self.store.load()['active_profile'],'two')
        self.assertEqual(self.request('/v1/rotation','POST',{})[0],200)
    def test_invalid_requests_limits_and_busy_configuration_are_bounded(self):
        self.start_api()
        for path in ('/v1/profiles?limit=0','/v1/profiles?offset=-1','/v1/profiles?limit=1&limit=2','/v1/status?unexpected=1'):
            self.assertEqual(self.request(path)[0],400)
        self.assertEqual(self.request('/v1/no-such-endpoint')[0],404)
        self.assertEqual(self.request('/v1/selection','POST',{'name':'two','secret':'private-test-password'})[0],400)
        self.assertEqual(self.request('/v1/selection','POST',{'name':'missing'})[0],400)
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        connection.request('POST','/v1/selection','x'*4097,{'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        response=connection.getresponse();self.assertEqual(response.status,413);response.read();connection.close()
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        connection.request('GET','/v1/status',headers={'X-Oversized':'x'*20000})
        response=connection.getresponse();self.assertEqual(response.status,431);response.read();connection.close()
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
        connection.request('POST','/v1/selection','['*1100+']'*1100,{'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        response=connection.getresponse();self.assertEqual(response.status,400);response.read();connection.close()
        with (self.root/'.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);start=time.monotonic()
            self.assertEqual(self.request('/v1/selection','POST',{'name':'two'})[0],400)
            self.assertLess(time.monotonic()-start,2)
    def test_handler_capacity_and_socket_timeouts_recover(self):
        self.start_api();held=[]
        try:
            for unused in range(8):
                sock=socket.create_connection(('127.0.0.1',self.port),timeout=2)
                sock.sendall(b'GET /v1/status HTTP/1.1\r\n');held.append(sock)
            deadline=time.monotonic()+2
            while len(list(Path(f'/proc/{self.child.pid}/task').iterdir()))<9 and time.monotonic()<deadline:time.sleep(.02)
            self.assertEqual(len(list(Path(f'/proc/{self.child.pid}/task').iterdir())),9)
            with socket.create_connection(('127.0.0.1',self.port),timeout=2) as extra:
                try:
                    extra.sendall(b'GET / HTTP/1.0\r\n\r\n');self.assertEqual(extra.recv(64),b'')
                except ConnectionResetError:pass
            # Stalled header readers expire without service restarts.
            deadline=time.monotonic()+4
            while len(list(Path(f'/proc/{self.child.pid}/task').iterdir()))>1 and time.monotonic()<deadline:time.sleep(.03)
            self.assertEqual(self.request()[0],200)
        finally:
            for sock in held:sock.close()

    def test_loopback_listener_actual_socket_and_no_unsolicited_service(self):
        self.start_api();inodes=set()
        for path in Path(f'/proc/{self.child.pid}/fd').iterdir():
            try:
                name=path.readlink().as_posix()
                if name.startswith('socket:['):inodes.add(name[8:-1])
            except OSError:pass
        listeners=[line.split()[1] for line in Path('/proc/net/tcp').read_text().splitlines()[1:] if line.split()[3]=='0A' and line.split()[9] in inodes]
        self.assertEqual(listeners,['0100007F:%04X'%self.port])
        self.assertFalse((self.home/'.config/systemd').exists())
    def test_api_session_safe_switch_uses_real_engine_without_restarting(self):
        class Second(Upstream):marker=b'second-upstream'
        servers=[]
        for handler in (Upstream,Second):
            server=ThreadingHTTPServer(('127.0.0.1',0),handler);threading.Thread(target=server.serve_forever,daemon=True).start()
            servers.append(server);self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        state=self.store.load()
        state.update(session_safe_fastest=True,selection_mode='fastest')
        for name,server in zip(('one','two'),servers):state['profiles'][name]=app.parse_share(f'http://127.0.0.1:{server.server_address[1]}',allow_plaintext=True)
        state['apps']['fixture']['profile']='@fastest'
        state['apps']['fixed']={'profile':'one','port':free_port(),'command':['fixture']}
        self.store.save(state);self.cli('daemon','start');self.cli('daemon','proxy-start');self.start_api()
        before=supervisor.read(self.root,supervisor.SESSION)['pid']
        def request(port):
            result=subprocess.run(['curl','--silent','--show-error','--noproxy','','--max-time','2','--proxy',f'http://127.0.0.1:{port}','http://127.0.0.1:12345/'],capture_output=True,timeout=4)
            self.assertEqual(result.returncode,0,result.stderr);return result.stdout
        self.assertEqual(request(state['apps']['fixture']['port']),b'assigned-upstream')
        code,result=self.request('/v1/selection','POST',{'name':'two','target':'fastest'})
        self.assertEqual(code,200,result);self.assertTrue(result['applied'])
        self.assertEqual(request(state['apps']['fixture']['port']),b'second-upstream')
        self.assertEqual(request(state['apps']['fixed']['port']),b'assigned-upstream')
        self.assertEqual(supervisor.read(self.root,supervisor.SESSION)['pid'],before)
        # Changing a live @active route would restart sessions; reject it explicitly.
        state=self.store.load();state['apps']['fixture']['profile']='@active';self.store.save(state)
        snapshot=self.store.load()
        self.assertEqual(self.request('/v1/selection','POST',{'name':'two','target':'active'})[0],400)
        self.assertEqual(self.store.load(),snapshot)

if __name__=='__main__':unittest.main()
