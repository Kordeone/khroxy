"""Reliability audit regression tests; no external connections or systemd calls."""
import contextlib
import importlib.machinery
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

p = Path(__file__).parent
loader = importlib.machinery.SourceFileLoader('khroxy_audit', str(p / 'khroxy'))
spec = importlib.util.spec_from_loader(loader.name, loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)


@contextlib.contextmanager
def fake_local_proxy(_outbound):
    yield 'http://127.0.0.1:29993'


class AnyProfileRouting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for field, path in [('ROOT', root), ('STATE', root / 'state.json'),
                            ('RUNTIME', root / 'runtime.json'), ('EVENTS', root / 'events.log')]:
            obj = patch.object(mod, field, path)
            obj.start()
            self.addCleanup(obj.stop)
        self.profile = {'protocol': 'vless', 'settings': {'vnext': []}}

    def test_any_mode_tolerates_missing_symbolic_selection(self):
        state = {'version': 1, 'profiles': {'working': self.profile},
                 'apps': {'codex': {'port': 20820, 'profile': '@fastest', 'command': ['codex']},
                          'kilo': {'port': 20821, 'profile': '@active', 'command': ['kilo']}},
                 'subscriptions': {}}
        entry = {'mode': 'any', 'url': 'https://example.net/private-feed'}
        with patch.object(mod, 'temporary_profile_proxy', side_effect=fake_local_proxy), \
             patch.object(mod, 'fetch_subscription', return_value=b'feed') as fetch:
            data = mod.get_feed_with_mode(state, entry, 'joy')
        self.assertEqual(data, b'feed')
        fetch.assert_called_once_with(entry['url'], proxy='http://127.0.0.1:29993')

    def test_any_mode_tries_next_after_first_profile_fails(self):
        state = {'version': 1, 'profiles': {'a': self.profile, 'b': self.profile},
                 'apps': {}, 'subscriptions': {}}
        entry = {'mode': 'any', 'url': 'https://example.net/private-feed'}
        with patch.object(mod, 'temporary_profile_proxy', side_effect=fake_local_proxy), \
             patch.object(mod, 'fetch_subscription', side_effect=[ValueError('timeout'), b'feed']) as fetch:
            self.assertEqual(mod.get_feed_with_mode(state, entry, 'joy'), b'feed')
        self.assertEqual(fetch.call_count, 2)

    def test_local_proxy_is_only_applied_to_explicit_fetch(self):
        entry = {'mode': 'direct', 'url': 'https://example.net/private-feed'}
        with patch.object(mod, 'fetch_subscription', return_value=b'feed') as fetch:
            self.assertEqual(mod.get_feed_with_mode({'profiles': {}, 'apps': {}}, entry, 'joy'), b'feed')
        fetch.assert_called_once_with(entry['url'])




class TUIActionBindings(unittest.TestCase):
    def test_g_activates_fastest_instead_of_scrolling_to_end(self):
        from test_v07_features import FakeWindow
        import threading
        window = FakeWindow([ord('g'), ord('q')], h=28, w=100)
        state = {'version': 1, 'profiles': {}, 'apps': {}, 'subscriptions': {}}
        stopper = threading.Event()
        with patch.object(mod, 'load', return_value=state), \
             patch.object(mod, 'activate_fastest', return_value=None) as activate, \
             patch.object(mod, 'start_status_monitor', return_value=({'value': 'OFFLINE'}, stopper)), \
             patch.object(mod.curses, 'curs_set', return_value=None), \
             patch.object(mod.curses, 'noecho', return_value=None):
            mod._tui(window)
        activate.assert_called_once()
        self.assertTrue(stopper.is_set())

if __name__ == '__main__':
    unittest.main()
