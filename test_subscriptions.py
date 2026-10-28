import base64
import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('khroxy_sub_test', str(Path(__file__).parent / 'khroxy'))
spec = importlib.util.spec_from_loader(loader.name, loader)
p = importlib.util.module_from_spec(spec)
loader.exec_module(p)
UUID1 = '01234567-89ab-4cde-8fff-0123456789ab'
UUID2 = '11234567-89ab-4cde-8fff-0123456789ab'
link1 = f'vless://{UUID1}@example.org:443?security=tls'
link2 = f'vless://{UUID2}@example.net:443?security=tls'


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.root = self.home / '.config/khroxy'
        self.root.mkdir(parents=True)
        self.state_path = self.root / 'state.json'
        self.runtime_path = self.root / 'runtime.json'
        self.patches = [
            patch.object(p, 'ROOT', self.root),
            patch.object(p, 'STATE', self.state_path),
            patch.object(p, 'RUNTIME', self.runtime_path),
            patch.object(p, 'EVENTS', self.root / 'events.log'),
            patch.object(p, 'running', return_value=False),
            patch.object(p, 'xray_test', return_value=None),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.state = {'version':1,'profiles':{},'apps':{},'subscriptions':{}}

    def getstate(self):
        return json.loads(self.state_path.read_text())

    def save(self, state):
        p.write_file(self.state_path, p.serial(state))

    def quiet(self, fn, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*args, **kwargs)

    def test_plain_list_and_base64(self):
        feed = (link1+'\n'+link2+'\n').encode()
        self.assertEqual(len(p.parse_subscription(feed)),2)
        self.assertEqual(p.parse_subscription(base64.b64encode(feed)),p.parse_subscription(feed))
        self.assertEqual(p.parse_subscription(base64.urlsafe_b64encode(feed).rstrip(b'=')),p.parse_subscription(feed))

    def test_reject_unsupported_and_invalid_instead_of_partial_import(self):
        with self.assertRaisesRegex(ValueError, 'link #2'):
            p.parse_subscription((link1+'\nunsupported://fixture@example.org:443').encode())
        with self.assertRaisesRegex(ValueError, 'Empty'):
            p.parse_subscription(b'  ')
        with self.assertRaises(ValueError):
            p.parse_subscription(b'x'* (p.MAX_SUB_BYTES+1))

    def test_urls_reject_unencrypted_and_local(self):
        for url in ('http://example.org/sub/secret', 'https://127.0.0.1/feed',
                    'https://localhost/feed', 'https://10.0.0.10/feed',
                    'https://user:pw@example.org/feed', 'https://host.home.arpa/feed'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                p.validate_subscription_url(url)
        self.assertEqual(p.validate_subscription_url('https://example.org/s/secret'), 'https://example.org/s/secret')

    def test_add_does_not_fetch_and_rejects_bad_route(self):
        with self.assertRaisesRegex(ValueError, 'not configured'):
            self.quiet(p.add_feed, self.state, 'sub', 'https://example.org/sub', 'profile', 'missing')
        self.quiet(p.add_feed, self.state, 'sub', 'https://example.org/secret')
        self.assertEqual(self.getstate()['subscriptions']['sub']['profiles'], [])
        self.assertEqual(self.getstate()['subscriptions']['sub']['mode'], 'direct')
        self.assertNotIn('example.org/secret', str(p.make_sub_profiles('sub', [p.parse_share(link1)])))

    def test_update_preserves_unrelated_and_assigned_deleted_profile(self):
        self.quiet(p.add_feed, self.state, 'sub', 'https://example.org/sub')
        with patch.object(p, 'get_feed_with_mode', return_value=link1.encode()):
            self.quiet(p.update_feed, self.getstate(), 'sub')
        state = self.getstate()
        self.assertEqual(len(state['profiles']),1)
        assigned = next(iter(state['profiles']))
        self.assertTrue(assigned.startswith('sub-'))
        state['profiles']['manual'] = p.parse_share(link2)
        state['apps']['kilo'] = {'profile': assigned, 'port': 20820, 'command': ['kilo']}
        self.save(state)
        with patch.object(p, 'get_feed_with_mode', return_value=link2.encode()), \
             patch.object(p, 'commit', side_effect=AssertionError('Unused feed change must not restart service')):
            self.quiet(p.update_feed, self.getstate(), 'sub')
        updated = self.getstate()
        self.assertIn('manual', updated['profiles'])
        self.assertIn(assigned, updated['profiles'])
        self.assertEqual(updated['apps']['kilo']['profile'], assigned)
        self.assertIn(assigned, updated['subscriptions']['sub']['stale'])
        self.assertEqual(len(updated['subscriptions']['sub']['profiles']),2)
        self.quiet(p.remove_feed, updated, 'sub')
        removed = self.getstate()
        self.assertEqual(removed['subscriptions'], {})
        self.assertIn(assigned, removed['profiles'])
        self.assertIn('manual', removed['profiles'])
        self.assertEqual(len(removed['profiles']),2)

    def test_partial_update_skips_plaintext_and_keeps_prior_profiles(self):
        self.quiet(p.add_feed, self.state, 'joy', 'https://example.org/sub')
        with patch.object(p, 'get_feed_with_mode', return_value=(link1 + '\n' + link2).encode()):
            self.quiet(p.update_feed, self.getstate(), 'joy')
        original = self.getstate()
        prior = set(original['subscriptions']['joy']['profiles'])
        third = f'vless://{str(__import__("uuid").UUID(int=3))}@third.example.org:443?security=tls'
        plaintext = f'vless://{UUID1}@unsafe.example.org:80?security=none'
        with patch.object(p, 'get_feed_with_mode', return_value=(third + '\n' + plaintext).encode()), \
             patch.object(p, 'commit', side_effect=AssertionError('Unused feed change should not restart service')):
            self.quiet(p.update_feed, self.getstate(), 'joy')
        after = self.getstate()
        self.assertEqual(after['subscriptions']['joy']['last_skipped'], 1)
        self.assertTrue(prior.issubset(set(after['profiles'])))
        self.assertTrue(prior.issubset(set(after['subscriptions']['joy']['stale'])))
        self.assertEqual(len(after['subscriptions']['joy']['profiles']), 3)
        self.assertTrue(all(item['streamSettings']['security'] == 'tls' for item in after['profiles'].values()))
        self.assertIn('parse_skip joy skipped', (self.root / 'events.log').read_text())

    def test_plaintext_only_update_preserves_state_bytes(self):
        self.quiet(p.add_feed, self.state, 'joy', 'https://example.org/sub')
        original = self.state_path.read_bytes()
        plaintext = f'vless://{UUID1}@unsafe.example.org:80?security=none'
        with patch.object(p, 'get_feed_with_mode', return_value=plaintext.encode()):
            with self.assertRaisesRegex(ValueError, 'no supported profiles'):
                self.quiet(p.update_feed, self.getstate(), 'joy')
        self.assertEqual(self.state_path.read_bytes(), original)

    def test_update_failure_keeps_state_bytes(self):
        self.quiet(p.add_feed, self.state, 'sub', 'https://example.org/sub')
        before = self.state_path.read_bytes()
        with patch.object(p, 'get_feed_with_mode', side_effect=ValueError('Download failed')):
            with self.assertRaises(ValueError):
                self.quiet(p.update_feed, self.getstate(), 'sub')
        self.assertEqual(before, self.state_path.read_bytes())

    def test_profile_routes_and_any_fallback(self):
        state = copy.deepcopy(self.state)
        state['profiles']={'bad': p.parse_share(link1), 'good':p.parse_share(link2)}
        entry={'url':'https://example.org/sub','mode':'any'}
        @contextlib.contextmanager
        def temp(profile):
            if profile['settings']['vnext'][0]['address']=='example.org':
                raise ValueError('bad profile')
            yield 'http://127.0.0.1:12345'
        with patch.object(p,'temporary_profile_proxy', side_effect=temp), \
             patch.object(p, 'fetch_subscription',return_value=b'hello') as fetch:
            self.assertEqual(p.get_feed_with_mode(state,entry), b'hello')
            self.assertEqual(fetch.call_args[1]['proxy'], 'http://127.0.0.1:12345')
        entry['mode']='direct'
        with patch.object(p, 'fetch_subscription',return_value=b'hello') as fetch:
            p.get_feed_with_mode(state,entry)
            self.assertEqual(fetch.call_args[0],('https://example.org/sub',))

    def test_autoupdate_timer_generation(self):
        svc, timer=p._auto_units_content()
        self.assertIn(b'sub-update --due',svc)
        self.assertIn(b'OnCalendar=*:0/15',timer)
        self.assertNotIn(b'http',timer)

if __name__=='__main__':
    unittest.main(verbosity=2)
