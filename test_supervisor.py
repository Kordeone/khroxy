"""Real isolated daemon/engine lifecycle. Never contact a production user bus."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import copy
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from khroxy_lib import supervisor
from khroxy_lib.storage import Store
from tests_support.engines import paths,free_port,process

ROOT=Path(__file__).parent


class OwnershipTests(unittest.TestCase):
    def test_peer_identity_uses_real_kernel_credentials(self):
        one,two=socket.socketpair()
        try:
            self.assertTrue(supervisor.peer_allowed(one))
            self.assertFalse(supervisor.peer_allowed(one,os.geteuid()+1))
        finally:one.close();two.close()

    def test_pid_reuse_wrong_cookie_and_boot_never_signal_foreign_process(self):
        child=subprocess.Popen(['sleep','20'])
        try:
            original=supervisor.identity(child.pid)
            for values in ({'token':'x'*64},{'token':'x'*64,'start_ticks':original['start_ticks']+1},
                           {'token':'x'*64,'boot_id':'previous-boot'}):
                record=dict(original,**values)
                self.assertFalse(supervisor.owned(record))
                self.assertFalse(supervisor.safe_signal(record,signal.SIGTERM))
                self.assertIsNone(child.poll())
        finally:child.terminate();child.wait(timeout=3)

    def test_unsafe_record_paths_and_permissions_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/'foreign';target.write_text('{}')
            (root/supervisor.SESSION).symlink_to(target)
            with self.assertRaises(ValueError):supervisor.read(root,supervisor.SESSION)
            (root/supervisor.SESSION).unlink();supervisor.write(root,supervisor.SESSION,{'desired':False})
            (root/supervisor.SESSION).chmod(0o644)
            with self.assertRaises(ValueError):supervisor.read(root,supervisor.SESSION)

    def test_stale_recovery_request_cannot_restart_a_stopped_runtime(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            app=SimpleNamespace(ROOT=root,STATE=root/'state.json',log_event=lambda *a,**k:None)
            runtime=supervisor.Runtime(app)
            supervisor.write(root,supervisor.SESSION,{'desired':True,'attempts':[]})
            runtime.stop()
            self.assertEqual(runtime.start(recovery=True)['runtime'],'offline')
            self.assertFalse(runtime.record()['desired'])
            runtime.closing=True
            with self.assertRaisesRegex(ValueError,'shutting down'):runtime.start()


class Origin(BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'supervised-fixture';self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def log_message(self,*args):pass


@unittest.skipUnless(paths().get('xray'),'Set KHROXY_TEST_XRAY for real supervisor integration')
class SupervisorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.root=self.home/'.config/khroxy';self.root.mkdir(parents=True)
        self.env=dict(os.environ,HOME=str(self.home),PYTHONDONTWRITEBYTECODE='1',XDG_RUNTIME_DIR=str(self.home),DBUS_SESSION_BUS_ADDRESS='unix:path='+str(self.home/'no-bus'),TERM='xterm-256color')
        self.port=free_port();self.state={'version':1,'engine':'xray','engine_binaries':{'xray':str(paths()['xray'])},
            'profiles':{'fixture':{'protocol':'freedom','settings':{}}},
            'apps':{'fixture':{'port':self.port,'profile':'fixture','command':['fixture']}},'subscriptions':{}}
        (self.root/'state.json').write_text(json.dumps(self.state));Store(self.root/'state.json').migrate()
        self.origin=ThreadingHTTPServer(('127.0.0.1',0),Origin)
        threading.Thread(target=self.origin.serve_forever,daemon=True).start()
        self.addCleanup(self.origin.server_close);self.addCleanup(self.origin.shutdown)
        self.addCleanup(self.cleanup)
    def cli(self,*args,check=True):
        result=subprocess.run([str(ROOT/'khroxy'),*args],env=self.env,capture_output=True,text=True,timeout=40)
        if check:self.assertEqual(result.returncode,0,result.stderr)
        return result
    def cleanup(self):
        try:self.cli('daemon','stop',check=False)
        except (OSError,subprocess.SubprocessError):
            with __import__('contextlib').suppress(OSError,ValueError):supervisor.stop_owned(supervisor.read(self.root,supervisor.SESSION))
        deadline=time.monotonic()+4
        while (self.root/supervisor.SOCKET).exists() and time.monotonic()<deadline:time.sleep(.03)
        owner=supervisor.read(self.root,supervisor.OWNER)
        if supervisor.owned(owner,False):supervisor.safe_signal(owner,signal.SIGTERM,token_required=False)
    def wait_status(self,predicate,timeout=8):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=json.loads(self.cli('daemon','status').stdout)
            if predicate(value):return value
            time.sleep(.04)
        self.fail('Supervisor state transition timed out')
    def start(self):
        self.cli('daemon','start');self.cli('daemon','proxy-start')
        return self.wait_status(lambda value:value['runtime']=='active')
    def request(self):
        result=subprocess.run(['curl','--silent','--show-error','--noproxy','','--max-time','3','--proxy',f'http://127.0.0.1:{self.port}',f'http://127.0.0.1:{self.origin.server_address[1]}/'],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,'supervised-fixture')

    def test_real_background_lifecycle_ipc_and_idempotence(self):
        status=self.start();self.request()
        self.assertEqual((self.root/supervisor.SOCKET).stat().st_mode&0o777,0o600)
        self.assertEqual(self.root.stat().st_mode&0o777,0o700)
        with ThreadPoolExecutor(max_workers=4) as pool:
            values=list(pool.map(lambda _:supervisor.rpc(self.root,'start',timeout=15),range(8)))
        self.assertTrue(all(value['pid']==status['pid'] for value in values));self.request()
        self.cli('daemon','proxy-stop');self.cli('daemon','proxy-stop')
        self.assertEqual(json.loads(self.cli('daemon','status').stdout)['runtime'],'offline')
        self.cli('daemon','disable');self.assertFalse(supervisor.enabled(self.root))

    def test_real_tui_quit_does_not_stop_intentional_background_proxy(self):
        import pty
        status=self.start();master,slave=pty.openpty()
        child=subprocess.Popen([str(ROOT/'khroxy'),'tui'],env=self.env,stdin=slave,stdout=slave,stderr=slave)
        os.close(slave)
        try:
            time.sleep(.25);os.write(master,b'q');child.wait(timeout=4)
            self.assertEqual(child.returncode,0)
        finally:
            if child.poll() is None:child.kill();child.wait()
            os.close(master)
        self.assertEqual(json.loads(self.cli('daemon','status').stdout)['pid'],status['pid']);self.request()

    def test_sigkill_daemon_adopts_only_original_owned_engine(self):
        status=self.start();owner=supervisor.read(self.root,supervisor.OWNER)
        self.assertTrue(supervisor.safe_signal(owner,signal.SIGKILL,token_required=False))
        deadline=time.monotonic()+3
        while supervisor.owned(owner,False) and time.monotonic()<deadline:time.sleep(.02)
        self.request();self.cli('daemon','start')
        result=self.wait_status(lambda value:value.get('daemon'))
        self.assertEqual(result['pid'],status['pid']);self.request()

    def test_engine_crash_recovers_then_restart_budget_stops_loop(self):
        status=self.start()
        for index in range(3):
            record=supervisor.read(self.root,supervisor.SESSION)
            self.assertTrue(supervisor.safe_signal(record,signal.SIGKILL))
            if index<2:
                previous=status['pid'];status=self.wait_status(lambda value:value['runtime']=='active' and value['pid']!=previous)
                self.request()
            else:
                status=self.wait_status(lambda value:value['blocked'] and value['runtime']=='offline')
                self.assertEqual(status['restart_attempts'],3)
        time.sleep(.35);self.assertEqual(json.loads(self.cli('daemon','status').stdout)['runtime'],'offline')

    def test_invalid_ipc_and_foreign_stale_session_do_not_kill_foreign_process(self):
        self.cli('daemon','start')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(3);client.connect(str(self.root/supervisor.SOCKET));client.sendall(b'{"op":"execute","command":"forbidden"}\n')
            response=json.loads(client.recv(8192));self.assertFalse(response['ok'])
        foreign=subprocess.Popen(['sleep','20'])
        try:
            fake=dict(supervisor.identity(foreign.pid),token='not-an-owned-token',desired=False,attempts=[])
            supervisor.write(self.root,supervisor.SESSION,fake)
            self.cli('daemon','proxy-stop');self.assertIsNone(foreign.poll())
        finally:foreign.terminate();foreign.wait(timeout=3)

    def test_launch_intent_recovers_spawn_before_pid_record_commit(self):
        status=self.start();owner=supervisor.read(self.root,supervisor.OWNER)
        record=supervisor.read(self.root,supervisor.SESSION)
        self.assertTrue(supervisor.safe_signal(owner,signal.SIGKILL,token_required=False))
        deadline=time.monotonic()+3
        while supervisor.owned(owner,False) and time.monotonic()<deadline:time.sleep(.02)
        intent={k:v for k,v in record.items() if k not in ('pid','uid','start_ticks','boot_id','exe_device','exe_inode')}
        intent['launching']=True;supervisor.write(self.root,supervisor.SESSION,intent)
        self.cli('daemon','start')
        result=self.wait_status(lambda value:value['runtime']=='active')
        self.assertEqual(result['pid'],status['pid']);self.request()

    def test_isolated_reboot_record_restores_valid_state(self):
        self.start();owner=supervisor.read(self.root,supervisor.OWNER);record=supervisor.read(self.root,supervisor.SESSION)
        supervisor.safe_signal(owner,signal.SIGKILL,token_required=False)
        deadline=time.monotonic()+3
        while supervisor.owned(owner,False) and time.monotonic()<deadline:time.sleep(.02)
        supervisor.safe_signal(record,signal.SIGKILL)
        record.update(boot_id='previous-boot',attempts=[]);supervisor.write(self.root,supervisor.SESSION,record)
        self.cli('daemon','start');self.wait_status(lambda value:value['runtime']=='active')
        self.request();self.assertNotEqual(supervisor.read(self.root,supervisor.SESSION)['boot_id'],'previous-boot')

    def test_uninstall_stops_owned_daemon_and_preserves_profiles(self):
        self.start();before=Store(self.root/'state.json').load()['profiles']
        self.cli('uninstall')
        self.assertEqual(Store(self.root/'state.json').load()['profiles'],before)
        self.assertFalse((self.root/supervisor.SOCKET).exists());self.assertFalse(supervisor.enabled(self.root))
        self.assertFalse(supervisor.owned(supervisor.read(self.root,supervisor.SESSION)))

    def test_stop_completes_before_runtime_files_can_disappear(self):
        self.start();owner=supervisor.read(self.root,supervisor.OWNER);record=supervisor.read(self.root,supervisor.SESSION)
        result=json.loads(self.cli('daemon','stop').stdout)
        self.assertFalse(result['daemon']);self.assertEqual(result['runtime'],'offline')
        self.assertFalse(supervisor.owned(owner,False));self.assertFalse(supervisor.owned(record))
        self.assertFalse((self.root/supervisor.SOCKET).exists())
        shutil.rmtree(self.root)
        self.assertFalse(supervisor.owned(record))

    def test_keep_runtime_shutdown_is_explicit_and_adoptable(self):
        self.start();owner=supervisor.read(self.root,supervisor.OWNER);record=supervisor.read(self.root,supervisor.SESSION)
        result=json.loads(self.cli('daemon','stop','--keep-runtime').stdout)
        self.assertFalse(result['daemon']);self.assertEqual(result['runtime'],'active')
        self.assertFalse(supervisor.owned(owner,False));self.assertTrue(supervisor.owned(record))
        self.request();self.cli('daemon','start')
        adopted=self.wait_status(lambda value:value['runtime']=='active')
        self.assertEqual(adopted['pid'],record['pid'])
        self.cli('daemon','stop');self.assertFalse(supervisor.owned(record))

    def test_service_unit_preparation_is_explicit_and_never_started(self):
        path=self.home/'.config/systemd/user/khroxy-supervisor.service'
        result=self.cli('daemon','service');self.assertIn('NoNewPrivileges=true',result.stdout);self.assertFalse(path.exists())
        self.cli('daemon','service','--write');self.assertTrue(path.exists());self.assertFalse((self.root/supervisor.SOCKET).exists())

    @unittest.skipUnless(shutil.which('systemd-analyze'),'Static systemd verifier unavailable')
    def test_generated_native_and_supervisor_units_have_valid_conditions(self):
        result=self.cli('daemon','service','--write')
        install=subprocess.run([str(ROOT/'install.sh'),'--mode','native','--xray',str(paths()['xray'])],env=self.env,input='',capture_output=True,text=True,timeout=20)
        self.assertEqual(install.returncode,0,install.stderr)
        directory=self.home/'.config/systemd/user'
        units=[str(path) for path in directory.glob('*.service')]
        result=subprocess.run(['systemd-analyze','--user','verify','--man=no','--generators=no',*units],env=self.env,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn('ignoring',result.stderr.lower());self.assertNotIn('not absolute',result.stderr.lower())
        core=(directory/'khroxy.service').read_text()
        self.assertIn('ConditionPathExists=!'+str(self.root/'.supervisor-mode'),core)
        self.assertNotIn('ConditionPathExists="',core)

    def test_clean_sigterm_stops_only_owned_engine(self):
        self.start();owner=supervisor.read(self.root,supervisor.OWNER)
        self.assertTrue(supervisor.safe_signal(owner,signal.SIGTERM,token_required=False))
        self.wait_status(lambda value:not value['daemon'] and value['runtime']=='offline')




@unittest.skipUnless(paths().get('xray'),'Set KHROXY_TEST_XRAY for legacy PID ownership regression')
class LegacyPidSafetyTests(unittest.TestCase):
    def test_integer_pid_record_never_authorizes_foreign_matching_engine_stop(self):
        loader=importlib.machinery.SourceFileLoader('khroxy_legacy_pid_test',str(ROOT/'khroxy'))
        spec=importlib.util.spec_from_loader(loader.name,loader);app=importlib.util.module_from_spec(spec);loader.exec_module(app)
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=root/'runtime.json';pidfile=root/'xray.pid';port=free_port()
            runtime.write_text(json.dumps({'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'http'}],'outbounds':[{'protocol':'blackhole'}]}))
            with process(paths()['xray'],'xray',runtime,port=port) as child, \
                 patch.object(app,'ROOT',root),patch.object(app,'RUNTIME',runtime),patch.object(app,'CONTAINER_PID',pidfile),patch.object(app,'XRAY',paths()['xray']):
                pidfile.write_text(str(child.pid));pidfile.chmod(0o600)
                self.assertIsNone(app._container_owned_pid())
                self.assertEqual(app._container_ctl('stop',app.SERVICE).returncode,0)
                self.assertIsNone(child.poll())


    def test_structured_owned_registry_starts_and_stops_without_integer_pid_trust(self):
        loader=importlib.machinery.SourceFileLoader('khroxy_owned_pid_test',str(ROOT/'khroxy'))
        spec=importlib.util.spec_from_loader(loader.name,loader);app=importlib.util.module_from_spec(spec);loader.exec_module(app)
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=root/'runtime.json';pidfile=root/'xray.pid';port=free_port()
            state={'version':1,'profiles':{},'apps':{},'subscriptions':{},'engine_binaries':{'xray':str(paths()['xray'])}}
            (root/'state.json').write_text(json.dumps(state))
            runtime.write_text(json.dumps({'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'http'}],'outbounds':[{'protocol':'blackhole'}]}))
            with patch.object(app,'ROOT',root),patch.object(app,'STATE',root/'state.json'),patch.object(app,'RUNTIME',runtime),patch.object(app,'CONTAINER_PID',pidfile),patch.object(app,'XRAY',paths()['xray']):
                try:
                    self.assertEqual(app._container_ctl('enable',app.SERVICE).returncode,0)
                    record=supervisor.read(root,'xray.pid');self.assertTrue(supervisor.owned(record))
                    self.assertEqual(app._container_owned_pid(),record['pid'])
                    self.assertEqual(app._container_ctl('stop',app.SERVICE).returncode,0)
                    self.assertFalse(supervisor.owned(record))
                finally:
                    if pidfile.exists():supervisor.stop_owned(supervisor.read(root,'xray.pid'))


class NativeUnitSafetyTests(unittest.TestCase):
    def test_foreign_unit_never_restarted_by_legacy_operations(self):
        loader=importlib.machinery.SourceFileLoader('khroxy_foreign_unit_test',str(ROOT/'khroxy'))
        spec=importlib.util.spec_from_loader(loader.name,loader);app=importlib.util.module_from_spec(spec);loader.exec_module(app)
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);unit=root/'khroxy.service';unit.write_text('[Service]\nExecStart=/owner/program\n')
            with patch.object(app,'ROOT',root),patch.object(app,'UNIT',unit),patch.object(app.subprocess,'run',side_effect=AssertionError('foreign unit touched')):
                for operation in ('restart','stop','disable','enable'):
                    self.assertNotEqual(app.systemctl(operation,app.SERVICE).returncode,0)


if __name__=='__main__':unittest.main()
