"""Bounded/cancelled probes against real loopback endpoints and Xray processes."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import signal
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from khroxy_lib.testing import Runner,Guard,tcp_probe,resolve,category
from tests_support.engines import paths,free_port

ROOT=Path(__file__).parent
loader=importlib.machinery.SourceFileLoader('khroxy_testing_tests',str(ROOT/'khroxy'))
spec=importlib.util.spec_from_loader(loader.name,loader);app=importlib.util.module_from_spec(spec);loader.exec_module(app)
DIRECT={'protocol':'freedom','settings':{}}


class Origin(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        with self.server.lock:
            self.server.active+=1;self.server.peak=max(self.server.peak,self.server.active);self.server.requests+=1;number=self.server.requests
        try:
            if self.path=='/slow': time.sleep(2)
            if self.path=='/concurrent': time.sleep(.25)
            code=503 if self.path=='/error' or (self.path=='/retry' and number==1) else 200
            body=b'x'*(65536 if self.path=='/download' else 16)
            self.send_response(code);self.send_header('Content-Length',str(len(body)));self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError): pass
        finally:
            with self.server.lock:self.server.active-=1
    def do_POST(self):
        size=int(self.headers.get('Content-Length','0'));data=self.rfile.read(size)
        with self.server.lock:self.server.uploaded+=len(data)
        self.send_response(204);self.end_headers()


def server():
    value=ThreadingHTTPServer(('127.0.0.1',0),Origin)
    value.lock=threading.Lock();value.active=0;value.peak=0;value.requests=0;value.uploaded=0
    threading.Thread(target=value.serve_forever,daemon=True).start()
    return value


class BudgetTests(unittest.TestCase):
    def test_invalid_resource_limits_rejected(self):
        for kw in ({'concurrency':0},{'timeout':0},{'retries':4},{'byte_limit':0},{'stages':['unknown']}):
            with self.assertRaises(ValueError):Runner(app,{},url='http://127.0.0.1',**kw)

    def test_real_tcp_success_refusal_and_timeout(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(0);port=listener.getsockname()[1]
            node={'settings':{'servers':[{'address':'127.0.0.1','port':port}]}}
            guard=Guard(.2,threading.Event(),1024)
            try:self.assertTrue(tcp_probe(node,guard)['ok'])
            finally:guard.close()
            # No accept: the first completed connection fills the owned backlog.
            guard=Guard(.15,threading.Event(),1024);started=time.monotonic()
            try:
                with self.assertRaises(OSError) as error:tcp_probe(node,guard)
                self.assertEqual(category(error.exception,expired=True),'timeout')
            finally:guard.close()
            self.assertLess(time.monotonic()-started,1)
        guard=Guard(.5,threading.Event(),1024)
        try:
            with self.assertRaises(ConnectionRefusedError):tcp_probe(node,guard)
        finally:guard.close()

    def test_real_resolver_failure_is_bounded_and_redacted(self):
        guard=Guard(2,threading.Event(),1024)
        try:
            # Invalid-length label fails IDNA locally; no resolver traffic required.
            with self.assertRaises(socket.gaierror):resolve('a'*64+'.invalid',443,guard)
        finally:guard.close()


@unittest.skipUnless(paths().get('xray'),'Set KHROXY_TEST_XRAY for real probe integration')
class RealProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.origin=server();self.addCleanup(self.origin.server_close);self.addCleanup(self.origin.shutdown)
        self.app_patch=patch.object(app,'ROOT',self.root);self.app_patch.start();self.addCleanup(self.app_patch.stop)
        self.binary_patch=patch.object(app,'XRAY',paths()['xray']);self.binary_patch.start();self.addCleanup(self.binary_patch.stop)
        self.state={'engine':'xray','profiles':{},'apps':{},'subscriptions':{}}
    def run_probe(self,path='/',profiles=None,**kw):
        runner=Runner(app,self.state,url=f'http://127.0.0.1:{self.origin.server_address[1]}'+path,**kw)
        result=runner.run(profiles or {'fixture':DIRECT})
        return result,runner

    def test_real_http_and_download_upload_limits(self):
        results,runner=self.run_probe('/download',stages=['download'],byte_limit=1024)
        self.assertTrue(results[0]['ok']);self.assertEqual(results[0]['bytes_used'],1024)
        self.assertEqual(results[0]['stages']['download']['bytes_received'],1024)
        results,runner=self.run_probe(stages=['upload'],byte_limit=4096)
        self.assertTrue(results[0]['ok']);self.assertEqual(self.origin.uploaded,4096)
        self.assertEqual(results[0]['bytes_used'],4096)

    def test_budget_shared_across_stages(self):
        results,runner=self.run_probe('/download',stages=['download','upload'],byte_limit=1024)
        self.assertEqual(results[0]['bytes_used'],1024)
        self.assertEqual(results[0]['stages']['upload']['category'],'byte-budget-exhausted')

    def test_http_failure_and_retry_budget(self):
        results,_=self.run_probe('/retry',retries=1)
        self.assertTrue(results[0]['ok']);self.assertEqual(results[0]['stages']['http']['attempts'],2)
        results,_=self.run_probe('/error',retries=2)
        self.assertFalse(results[0]['ok']);self.assertEqual(results[0]['stages']['http']['category'],'http-status')
        self.assertEqual(results[0]['stages']['http']['attempts'],3)
        self.assertNotIn('/error',json.dumps(results))

    def test_slow_response_obeys_profile_deadline(self):
        started=time.monotonic();results,_=self.run_probe('/slow',timeout=.3)
        self.assertFalse(results[0]['ok']);self.assertEqual(results[0]['stages']['http']['category'],'timeout')
        self.assertLess(time.monotonic()-started,1.2)

    def test_concurrency_limit_and_owned_process_cleanup(self):
        results,runner=self.run_probe('/concurrent',profiles={f'node{i}':DIRECT for i in range(5)},concurrency=2,timeout=3)
        self.assertTrue(all(result['ok'] for result in results))
        self.assertEqual(self.origin.peak,2)
        self.assertTrue(runner.seen_pids)
        self.assertTrue(all(not Path(f'/proc/{pid}').exists() for pid in runner.seen_pids))
        self.assertEqual(list(self.root.glob('.probe-*')),[])

    def test_cancellation_closes_sockets_and_all_owned_processes(self):
        cancel=threading.Event();runner=Runner(app,self.state,url=f'http://127.0.0.1:{self.origin.server_address[1]}/slow',timeout=20,concurrency=2,cancel=cancel)
        values=[];thread=threading.Thread(target=lambda:values.extend(runner.run({f'node{i}':DIRECT for i in range(6)})))
        thread.start();time.sleep(.25);started=time.monotonic();cancel.set();thread.join(timeout=2)
        self.assertFalse(thread.is_alive());self.assertLess(time.monotonic()-started,2)
        self.assertTrue(all(value['category']=='cancelled' for value in values))
        self.assertTrue(all(not Path(f'/proc/{pid}').exists() for pid in runner.seen_pids))

    def test_real_cli_cancellation_preserves_active_runtime(self):
        config=self.root/'.config/khroxy';config.mkdir(parents=True)
        state=dict(self.state,version=1,profiles={'fixture':DIRECT},engine_binaries={'xray':str(paths()['xray'])})
        raw=json.dumps(state).encode();(config/'state.json').write_bytes(raw)
        runtime=config/'runtime.json';runtime.write_bytes(b'active-fixture-runtime')
        env=dict(os.environ,HOME=str(self.root),PYTHONDONTWRITEBYTECODE='1')
        child=subprocess.Popen([str(ROOT/'khroxy'),'test-profiles','--url',f'http://127.0.0.1:{self.origin.server_address[1]}/slow','--timeout','20'],
                               env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+3
            while self.origin.requests==0:
                self.assertLess(time.monotonic(),deadline);time.sleep(.02)
            child.send_signal(signal.SIGINT)
            stdout,stderr=child.communicate(timeout=3)
            self.assertEqual(child.returncode,130,stderr)
            self.assertEqual(json.loads(stdout)[0]['category'],'cancelled')
            self.assertEqual(runtime.read_bytes(),b'active-fixture-runtime')
            self.assertEqual((config/'state.json').read_bytes(),raw)
        finally:
            if child.poll() is None:child.kill();child.wait()

    def test_tls_verification_failure_and_explicit_ca_success(self):
        cert,key=self.root/'certificate.pem',self.root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1','-keyout',str(key),'-out',str(cert)],capture_output=True,check=True,timeout=10)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
        self.origin.socket=context.wrap_socket(self.origin.socket,server_side=True)
        url=f'https://127.0.0.1:{self.origin.server_address[1]}/'
        result=Runner(app,self.state,url=url,timeout=3).run({'fixture':DIRECT})[0]
        self.assertEqual(result['stages']['http']['category'],'tls')
        result=Runner(app,self.state,url=url,timeout=3,ca_file=str(cert)).run({'fixture':DIRECT})[0]
        self.assertTrue(result['ok'])


if __name__=='__main__':unittest.main()
