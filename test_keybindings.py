"""Regression tests for the minimalist, on-demand keybindings screen."""
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
import threading

loader = importlib.machinery.SourceFileLoader('khroxy_keybindings_test', str(Path(__file__).with_name('khroxy')))
spec = importlib.util.spec_from_loader(loader.name, loader)
p = importlib.util.module_from_spec(spec)
loader.exec_module(p)


class FakeWindow:
    def __init__(self, keys, h=24, w=80):
        self.keys = iter(keys)
        self.h, self.w = h, w
        self.snapshots = []
        self.lines = []
        self.timeouts = []
    def getmaxyx(self): return self.h, self.w
    def keypad(self, _): pass
    def timeout(self, value): self.timeouts.append(value)
    def erase(self): self.lines = []
    def addnstr(self, y, x, content, count, *args): self.lines.append((y, x, str(content)))
    def attron(self, _): pass
    def attroff(self, _): pass
    def addstr(self, *args): pass
    def refresh(self): self.snapshots.append(list(self.lines))
    def getch(self): return next(self.keys)


class HelpTests(unittest.TestCase):
    def test_help_screen_has_all_five_sections_and_can_scroll(self):
        win = FakeWindow([p.curses.KEY_END, p.curses.KEY_HOME, p.curses.KEY_DOWN, p.curses.KEY_NPAGE, 27], h=20, w=65)
        p._help_screen(win)
        output = ' '.join(text for snap in win.snapshots for _, _, text in snap)
        for group in ('NAVIGATION', 'PROFILES', 'SUBSCRIPTIONS', 'APPLICATIONS', 'SERVICE & SETTINGS'):
            self.assertIn(group, output)
        self.assertIn('KEYBINDINGS', output)
        self.assertEqual(win.timeouts, [-1, 1000])

    def test_question_mark_toggles_screen_without_quitting_app(self):
        win = FakeWindow([ord('?'), ord('?'), ord('q')], h=28, w=100)
        state = {'version': 1, 'profiles': {}, 'apps': {}, 'subscriptions': {}}
        stop = threading.Event()
        with patch.object(p, 'load', return_value=state), \
             patch.object(p, 'start_status_monitor', return_value=({'value':'OFFLINE'}, stop)), \
             patch.object(p.curses, 'curs_set', return_value=None), \
             patch.object(p.curses, 'noecho', return_value=None):
            p._tui(win)
        self.assertTrue(stop.is_set())
        main = ' '.join(text for _, _, text in win.snapshots[-1])
        self.assertIn('khroxy  /  private Xray manager', main)
        self.assertIn('keybindings', main)
        self.assertNotIn('DO ', main)
        self.assertNotIn('NAV ', main)
        self.assertTrue(any('KEYBINDINGS' in text for snap in win.snapshots for _, _, text in snap))

    def test_help_screen_q_returns_to_main_not_quit(self):
        win = FakeWindow([ord('?'), ord('q'), ord('q')], h=28, w=100)
        state = {'version':1, 'profiles':{}, 'apps':{}, 'subscriptions':{}}
        stop = threading.Event()
        with patch.object(p, 'load', return_value=state), \
             patch.object(p, 'start_status_monitor', return_value=({'value':'OFFLINE'}, stop)), \
             patch.object(p.curses, 'curs_set', return_value=None), \
             patch.object(p.curses, 'noecho', return_value=None):
            p._tui(win)
        self.assertTrue(stop.is_set())
        self.assertGreaterEqual(len(win.snapshots), 3)

    def test_small_terminal_help_safe(self):
        win = FakeWindow([27], h=6, w=22)
        p._help_screen(win)
        self.assertTrue(any('Resize' in text for snap in win.snapshots for _, _, text in snap))

if __name__ == '__main__': unittest.main()
