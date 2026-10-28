"""Optional localhost-only integration test with a preinstalled trusted Xray.

Set KHROXY_TEST_XRAY to its absolute path. No downloads, Internet, containers,
system services, or firewall operations. Both upstreams are local test fixtures.
"""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


class Proxy(socketserver.StreamRequestHandler):
    def handle(self):
        if not self.rfile.readline().startswith(b'CONNECT '):
            return
        while self.rfile.readline() not in (b'\r\n', b'\n', b''):
            pass
        self.wfile.write(b'HTTP/1.1 200 Connection established\r\n\r\n')
        self.wfile.flush()
        while self.rfile.readline():
            self.wfile.write(self.server.label + b'\n')
            self.wfile.flush()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@unittest.skipUnless(os.environ.get('KHROXY_TEST_XRAY'), 'Set KHROXY_TEST_XRAY for optional local Xray integration')
class XrayRoutingTests(unittest.TestCase):
    def test_api_switch_preserves_open_connection_and_fixed_route(self):
        binary = Path(os.environ['KHROXY_TEST_XRAY'])
        self.assertTrue(binary.is_absolute() and binary.is_file())
        loader = importlib.machinery.SourceFileLoader('khroxy_xray_integration', str(Path(__file__).with_name('khroxy')))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        p = importlib.util.module_from_spec(spec)
        loader.exec_module(p)
        with tempfile.TemporaryDirectory() as tmp, Server(('127.0.0.1', 0), Proxy) as one, Server(('127.0.0.1', 0), Proxy) as two:
            for server, label in ((one, b'main'), (two, b'backup')):
                server.label = label
                threading.Thread(target=server.serve_forever, daemon=True).start()
                self.addCleanup(server.shutdown)
            def free_port():
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1', 0))
                    return sock.getsockname()[1]
            dynamic, fixed = free_port(), free_port()
            while fixed == dynamic:
                fixed = free_port()
            def outbound(server):
                return {'protocol': 'http', 'settings': {'servers': [{'address': '127.0.0.1', 'port': server.server_address[1]}]}}
            p.ROOT = Path(tmp)
            p.RUNTIME = p.ROOT / 'runtime.json'
            p.XRAY = binary
            state = {'profiles': {'main': outbound(one), 'backup': outbound(two)}, 'apps': {
                'dynamic': {'port': dynamic, 'profile': '@fastest', 'command': ['fixture']},
                'fixed': {'port': fixed, 'profile': 'main', 'command': ['fixture']}},
                'fastest_profile': 'main', 'session_safe_fastest': True}
            p.RUNTIME.write_bytes(p.serial(p.build_runtime(state)))
            validation = subprocess.run([str(binary), 'run', '-test', '-config', str(p.RUNTIME)], capture_output=True, timeout=10)
            self.assertEqual(validation.returncode, 0, validation.stderr.decode())
            with (p.ROOT / 'xray.log').open('wb') as log:
                process = subprocess.Popen([str(binary), 'run', '-config', str(p.RUNTIME)], stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 5
                    while not (p.ROOT / 'routing.sock').exists():
                        if process.poll() is not None or time.monotonic() > deadline:
                            self.fail('Local Xray fixture failed to start: ' + (p.ROOT / 'xray.log').read_text())
                        time.sleep(0.02)
                    def tunnel(port):
                        sock = socket.create_connection(('127.0.0.1', port), timeout=3)
                        self.addCleanup(sock.close)
                        stream = sock.makefile('rwb', buffering=0)
                        self.addCleanup(stream.close)
                        stream.write(b'CONNECT fixture.invalid:443 HTTP/1.1\r\nHost: fixture.invalid:443\r\n\r\n')
                        self.assertIn(b'200', stream.readline())
                        while stream.readline() not in (b'\r\n', b'\n', b''):
                            pass
                        return stream
                    def query(stream):
                        stream.write(b'probe\n')
                        return stream.readline().strip()
                    existing = tunnel(dynamic)
                    self.assertEqual(query(existing), b'main')
                    with patch.object(p, 'CONTAINER_MODE', False):
                        self.assertTrue(p.switch_fastest_live(state, dict(state, fastest_profile='backup')))
                    self.assertEqual(query(tunnel(dynamic)), b'backup')
                    self.assertEqual(query(existing), b'main')
                    self.assertEqual(query(tunnel(fixed)), b'main')
                    self.assertIsNone(process.poll())
                finally:
                    process.terminate()
                    process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
