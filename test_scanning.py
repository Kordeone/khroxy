"""Literal candidate scan limits, real sockets, cancellation and persistence."""
import copy
from http.server import ThreadingHTTPServer
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from khroxy_lib.scanning import Scanner, targets, resume, ranked
from khroxy_lib.storage import Store
from test_networking import Origin
from tests_support.engines import paths, process, free_port

ROOT=Path(__file__).parent

class ScanTests(unittest.TestCase):
    def test_only_explicit_bounded_literal_candidates(self):
        for value in ([],['127.0.0.1'],[{'ip':'127.0.0.0/8','port':80}],
                      [{'ip':'example.invalid','port':80}],[{'ip':'0.0.0.0','port':80}],
                      [{'ip':'224.0.0.1','port':80}],[{'ip':'::1%lo','port':80}],
                      [{'ip':'127.0.0.1','port':0}],[{'ip':'127.0.0.1','port':80,'range':10}],
                      [{'ip':'127.0.0.1','port':80}]*1001):
            with self.assertRaises(ValueError):targets(value)
        self.assertEqual(targets([{'ip':'::1','port':80}]*2),[{'ip':'::1','port':80}])
        for kwargs in ({'rate':float('inf')},{'rate':21},{'concurrency':33},{'timeout':11}):
            with self.assertRaises(ValueError):Scanner([{'ip':'127.0.0.1','port':80}],**kwargs)

    def test_real_socket_results_and_rate_limit(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1',0));server.listen(20)
            items=[{'ip':'127.0.0.1','port':server.getsockname()[1]}, {'ip':'127.0.0.1','port':free_port()}]
            start=time.monotonic();values=list(Scanner(items,concurrency=2,rate=5,timeout=.2).run())
            self.assertGreaterEqual(time.monotonic()-start,.18)
            self.assertTrue(values[0]['ok']);self.assertGreaterEqual(values[0]['ms'],0)
            self.assertEqual(values[1]['category'],'connection-refused')

    def test_real_timeout_cancellation_and_concurrency_bound(self):
        # A full loopback listen backlog keeps further TCP connects pending.
        sockets=[]
        try:
            for unused in range(4):
                server=socket.socket();server.bind(('127.0.0.1',0));server.listen(0);sockets.append(server)
                established=socket.create_connection(server.getsockname(),timeout=.2);sockets.append(established)
            items=[{'ip':'127.0.0.1','port':sockets[i].getsockname()[1]} for i in range(0,8,2)]
            start=time.monotonic();results=list(Scanner(items,concurrency=2,rate=20,timeout=.3).run())
            elapsed=time.monotonic()-start
            self.assertTrue(all(r['category']=='timeout' for r in results))
            self.assertGreaterEqual(elapsed,.55);self.assertLess(elapsed,1.2)
            cancel=threading.Event();values=[];scanner=Scanner(items,concurrency=2,rate=.1,timeout=10,cancel=cancel)
            worker=threading.Thread(target=lambda:values.extend(scanner.run()));worker.start();time.sleep(.1)
            start=time.monotonic();cancel.set();worker.join(timeout=1)
            self.assertFalse(worker.is_alive());self.assertLess(time.monotonic()-start,1);self.assertEqual(values,[])
        finally:
            for sock in sockets:sock.close()

    def test_resume_checkpoint_ranking_and_backup_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json';path.write_text(json.dumps({'version':1,'profiles':{},'apps':{},'subscriptions':{}}));store=Store(path);store.migrate()
            candidates=[{'ip':'127.0.0.1','port':12345},{'ip':'::1','port':12345}]
            self.assertEqual(resume(store,'fixture',candidates),candidates)
            store.append('metadata',None,{'kind':'edge-scan-result','scan':'fixture',**candidates[0],'ok':True,'ms':1,'timestamp':time.time()})
            self.assertEqual(resume(store,'fixture',candidates,True),candidates[1:])
            with self.assertRaises(ValueError):resume(store,'fixture',candidates)
            with self.assertRaises(ValueError):resume(store,'fixture',candidates[:1],True)
            self.assertEqual(ranked(store,'fixture')[0]['ms'],1)
            backup=Path(tmp)/'backup';store.export(backup);store.restore(backup)
            self.assertEqual(len(ranked(store,'fixture')),1)

    def test_only_configured_target_receives_connection(self):
        with socket.socket() as selected,socket.socket() as excluded:
            selected.bind(('127.0.0.1',0));selected.listen(2)
            excluded.bind(('127.0.0.1',0));excluded.listen(2);excluded.settimeout(.1)
            list(Scanner([{'ip':'127.0.0.1','port':selected.getsockname()[1]}],rate=20).run())
            accepted,_=selected.accept();accepted.close()
            with self.assertRaises(TimeoutError):excluded.accept()


@unittest.skipUnless(paths().get('xray'),'Set KHROXY_TEST_XRAY')
class ScanIntegrationTests(unittest.TestCase):
    def test_cli_scan_preserves_active_real_runtime_and_resumes(self):
        loader=importlib.machinery.SourceFileLoader('khroxy_scan_tests',str(ROOT/'khroxy'))
        spec=importlib.util.spec_from_loader(loader.name,loader);app=importlib.util.module_from_spec(spec);loader.exec_module(app)
        with tempfile.TemporaryDirectory() as tmp,ThreadingHTTPServer(('127.0.0.1',0),Origin) as origin:
            threading.Thread(target=origin.serve_forever,daemon=True).start();self.addCleanup(origin.shutdown)
            home=Path(tmp);root=home/'.config/khroxy';root.mkdir(parents=True);port=free_port()
            state={'version':1,'profiles':{'fixture':{'protocol':'freedom','settings':{}}},'apps':{'fixture':{'profile':'fixture','port':port,'command':['fixture']}},'subscriptions':{}}
            (root/'state.json').write_text(json.dumps(state));Store(root/'state.json').migrate()
            runtime=root/'runtime.json';runtime.write_text(json.dumps(app.build_runtime(state)));before=runtime.read_bytes()
            candidates=home/'candidates.json';candidates.write_text(json.dumps([{'ip':'127.0.0.1','port':origin.server_address[1]}]))
            env=dict(os.environ,HOME=tmp,XDG_RUNTIME_DIR=tmp,DBUS_SESSION_BUS_ADDRESS='unix:path='+tmp+'/no-bus')
            with process(paths()['xray'],'xray',runtime,port=port) as child:
                for extra in ([],['--resume']):
                    result=subprocess.run([str(ROOT/'khroxy'),'scan','run','fixture','--file',str(candidates),*extra],env=env,capture_output=True,text=True,timeout=5)
                    self.assertEqual(result.returncode,0,result.stderr)
                self.assertIsNone(child.poll());self.assertEqual(runtime.read_bytes(),before)
                result=subprocess.run(['curl','--silent','--show-error','--noproxy','','--max-time','2','--proxy',f'http://127.0.0.1:{port}',f'http://127.0.0.1:{origin.server_address[1]}/'],capture_output=True,timeout=4)
                self.assertEqual(result.stdout,b'direct-origin')
            self.assertEqual(len(ranked(Store(root/'state.json'),'fixture')),1)
            candidates.write_text(json.dumps([{'ip':'127.0.0.1','port':free_port()},{'ip':'127.0.0.1','port':free_port()}]))
            command=[str(ROOT/'khroxy'),'scan','run','cancelled','--file',str(candidates),'--rate','.1']
            child=subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                first=child.stdout.readline();self.assertTrue(first, 'First checkpoint missing')
                duplicate=subprocess.run([*command,'--resume'],env=env,capture_output=True,text=True,timeout=5)
                self.assertNotEqual(duplicate.returncode,0);self.assertIn('already running',duplicate.stderr)
                child.send_signal(signal.SIGINT);out,err=child.communicate(timeout=3)
                self.assertEqual(child.returncode,130,err);self.assertIn('resumable',out)
            finally:
                if child.poll() is None:child.kill();child.communicate(timeout=2)
            completed=subprocess.run([*command,'--resume','--rate','20'],env=env,capture_output=True,text=True,timeout=5)
            self.assertEqual(completed.returncode,0,completed.stderr)
            self.assertEqual(len(ranked(Store(root/'state.json'),'cancelled')),2)


if __name__=='__main__':unittest.main()
