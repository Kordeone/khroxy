import contextlib
import importlib.machinery
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('khroxy_shell_test', str(Path(__file__).parent / 'khroxy'))
spec = importlib.util.spec_from_loader(loader.name, loader)
p = importlib.util.module_from_spec(spec)
loader.exec_module(p)

class FakeWindow:
    def __init__(self, keys):
        self.keys = iter(keys)
        self.output = []
    def getmaxyx(self): return (28, 100)
    def keypad(self, value): pass
    def timeout(self, value): pass
    def erase(self): pass
    def attron(self, style): pass
    def attroff(self, style): pass
    def addstr(self, *args): pass
    def addnstr(self, y, x, text, limit, *args): self.output.append(str(text))
    def refresh(self): pass
    def getch(self): return next(self.keys)

class TuIShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        (self.home / '.config' / 'khroxy').mkdir(parents=True)
        self.state = {'version': 1, 'profiles': {}, 'apps': {}, 'subscriptions': {}}

    def test_subscription_panel_does_not_index_profiles(self):
        self.state['subscriptions']['joyboy'] = {'mode':'direct','minutes':60,'last_ok':None,'stale':[], 'profiles':[]}
        wnd = FakeWindow([ord('b'), ord('q')])
        with patch.object(p,'load', return_value=self.state), patch.object(p,'running',return_value=False), \
             patch.object(p.curses,'curs_set', return_value=None), patch.object(p.curses,'noecho',return_value=None):
            p._tui(wnd)
        self.assertTrue(any('joyboy' in text and '60m' in text for text in wnd.output))

    def test_shell_functions_only_selected_safe_executables(self):
        self.state['apps'] = {
            'kilo': {'port':20820,'profile':'main','command':['kilo']},
            'other': {'port':20821,'profile':'main','command':['curl']},
            'curl': {'port':20822,'profile':'main','command':['curl']},
        }
        result = p.shell_functions(self.state)
        self.assertEqual(len(result), 1)
        self.assertIn('kilo() {', result[0])
        self.assertIn('run kilo "$@"', result[0])

    def test_shell_enable_disable_preserves_original(self):
        rc = self.home / '.bashrc'
        rc.write_text('# user config\nalias ll="ls -la"\n')
        with patch.object(p,'bashrc_path',return_value=rc):
            with contextlib.redirect_stdout(io.StringIO()):
                p.cmd_shell_enable()
                p.cmd_shell_enable()
            self.assertEqual(rc.read_text().count('# khroxy-shell-hook'),1)
            with contextlib.redirect_stdout(io.StringIO()):
                p.cmd_shell_disable()
            self.assertEqual(rc.read_text(),'# user config\nalias ll="ls -la"\n')

    def test_symlink_bashrc_rejected(self):
        rc = self.home / '.bashrc'
        target = self.home / 'other'
        target.write_text('keep')
        rc.symlink_to(target)
        with patch.object(p,'bashrc_path',return_value=rc), self.assertRaisesRegex(ValueError,'symlink'):
            p.cmd_shell_enable()
        self.assertEqual(target.read_text(),'keep')

    def test_simplified_subscription_tui_routes_are_distinct(self):
        wnd = object()
        choices = ['favorite','https://example.org/feed']
        with patch.object(p, '_input', side_effect=choices), patch.object(p, 'lock',return_value=50), \
             patch.object(p.os,'close',return_value=None), patch.object(p,'load',return_value=self.state), \
             patch.object(p, 'add_feed',return_value=None) as add:
            result = p._tui_add_subscription(wnd,'profile','primary')
        add.assert_called_once_with(self.state, 'favorite', 'https://example.org/feed', 'profile','primary')
        self.assertEqual(result,'Done')

if __name__=='__main__': unittest.main()
