"""Regressions for service-bus stalls, local proxy routing, and retirement safety."""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).with_name('khroxy')
loader = importlib.machinery.SourceFileLoader('khroxy_v05_test', str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
p = importlib.util.module_from_spec(spec)
loader.exec_module(p)

class FakeWindow:
    def __init__(self, keys):
        self.keys = iter(keys)
        self.lines = []
    def getmaxyx(self): return 28, 110
    def keypad(self, value): pass
    def timeout(self, value): pass
    def erase(self): pass
    def attron(self, value): pass
    def attroff(self, value): pass
    def addstr(self, *args): pass
    def addnstr(self, y, x, text, count, *args):
        self.lines.append((y, x, str(text)))
    def refresh(self): pass
    def getch(self): return next(self.keys)

class ConnectProxy(socketserver.StreamRequestHandler):
    def handle(self):
        self.server.first_line = self.rfile.readline().decode('ascii', errors='replace').strip()
        self.wfile.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')

class V05Tests(unittest.TestCase):
    def test_systemctl_has_bounded_subprocess_timeout(self):
        with patch.object(p.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['systemctl'], 2)) as run:
            with self.assertRaisesRegex(ValueError, 'timed out'):
                p.running()
        self.assertEqual(run.call_args.kwargs['timeout'], 2)

    def test_tui_paints_and_quits_without_sync_systemctl(self):
        fake = FakeWindow([ord('b'), 9, ord('q')])
        state = {'version': 1, 'profiles': {}, 'apps': {},
                 'subscriptions': {'joyboy': {'mode':'direct', 'minutes':60, 'last_ok':None,
                                                'profiles':[], 'stale':[]}}}
        stop = threading.Event()
        with patch.object(p, 'load', return_value=state), \
             patch.object(p, 'start_status_monitor', return_value=({'value':'UNKNOWN'}, stop)), \
             patch.object(p, 'systemctl', side_effect=AssertionError('UI must never call systemd to draw')), \
             patch.object(p.curses, 'curs_set', return_value=None), \
             patch.object(p.curses, 'noecho', return_value=None):
            p._tui(fake)
        self.assertTrue(stop.is_set())
        self.assertTrue(any('joyboy' in value for _, _, value in fake.lines))
        self.assertTrue(any('UNKNOWN' in value for _, _, value in fake.lines))
        self.assertTrue(any('keybindings' in value for _, _, value in fake.lines))
        self.assertFalse(any(value.strip() in ('NAV', 'DO') for _, _, value in fake.lines))

    def test_profile_proxy_used_even_if_no_apps_no_service_and_no_proxy_star(self):
        class LocalServer(socketserver.TCPServer):
            allow_reuse_address = True
        with LocalServer(('127.0.0.1', 0), ConnectProxy) as server:
            thread = threading.Thread(target=server.handle_request, daemon=True)
            thread.start()
            state = {'profiles': {'main': {'protocol': 'vless'}}, 'apps': {}, 'subscriptions': {}}
            entry = {'mode': 'profile', 'via': 'main', 'url':'https://example.com/privatefeed'}
            @contextlib.contextmanager
            def stub_proxy(outbound):
                self.assertEqual(outbound['protocol'], 'vless')
                yield 'http://127.0.0.1:%d' % server.server_address[1]
            with tempfile.TemporaryDirectory() as tmp, \
                 patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*'}), \
                 patch.object(p, 'ROOT', Path(tmp)), \
                 patch.object(p, 'EVENTS', Path(tmp)/'events.log'), \
                 patch.object(p, 'temporary_profile_proxy', side_effect=stub_proxy):
                with self.assertRaisesRegex(ValueError, 'previous profiles retained'):
                    p.get_feed_with_mode(state, entry, 'private')
                events = (Path(tmp)/'events.log').read_text()
                self.assertNotIn('https://', events)
                self.assertNotIn('example.com', events)
                self.assertIn('route_ready main', events)
            thread.join(timeout=3)
            self.assertEqual(server.first_line, 'CONNECT example.com:443 HTTP/1.1')

    def test_retirement_accepts_approved_xray_status_symlink_in_dry_run(self):
        script = Path(__file__).with_name('retire-codex-xray.sh')
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp)
            (home/'.local/bin').mkdir(parents=True)
            (home/'.local/bin/xray-switch').write_text('#!/bin/sh\n')
            (home/'.local/bin/xray-status').symlink_to('xray-switch')
            fakebin=home/'fakebin'; fakebin.mkdir()
            ctl=fakebin/'systemctl'
            ctl.write_text('#!/bin/sh\nexit 0\n'); ctl.chmod(0o755)
            env=dict(os.environ, HOME=tmp, PATH=str(fakebin)+':'+os.environ['PATH'])
            proc=subprocess.run(['bash',str(script),'--dry-run'],text=True,capture_output=True,env=env,timeout=5)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertIn('DRY RUN',proc.stdout)
            self.assertTrue((home/'.local/bin/xray-status').is_symlink())

if __name__ == '__main__': unittest.main()
