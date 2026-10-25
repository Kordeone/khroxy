import contextlib
import importlib.machinery
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('khroxy_cleanup_test', str(Path(__file__).parent / 'khroxy'))
spec = importlib.util.spec_from_loader(loader.name, loader)
p = importlib.util.module_from_spec(spec)
loader.exec_module(p)


class StubResult:
    returncode = 0
    stderr = ''


class UninstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / '.config/khroxy'
        self.root.mkdir(parents=True)
        self.unit = self.home / '.config/systemd/user/khroxy.service'
        self.unit.parent.mkdir(parents=True)
        self.unit.write_text('# Managed by khroxy\n[Service]\nExecStart=/tmp/xray\n')
        self.autosvc = self.home / '.config/systemd/user/khroxy-sub-update.service'
        self.autotimer = self.home / '.config/systemd/user/khroxy-sub-update.timer'
        self.bin = self.home / '.local/bin/khroxy'
        self.bin.parent.mkdir(parents=True)
        self.bin.write_text((Path(__file__).parent / 'khroxy').read_text())
        (self.root / 'state.json').write_text('dummy-secret')
        (self.root / 'runtime.json').write_text('another-secret')
        (self.root / '.lock').write_text('')
        self.calls = []
        self.patches = [
            patch.object(p.Path, 'home', return_value=self.home),
            patch.object(p, 'ROOT', self.root),
            patch.object(p, 'UNIT', self.unit),
            patch.object(p, 'AUTOSERVICE_UNIT', self.autosvc),
            patch.object(p, 'AUTOTIMER_UNIT', self.autotimer),
            patch.object(p, 'STATE', self.root / 'state.json'),
            patch.object(p, 'RUNTIME', self.root / 'runtime.json'),
            patch.object(p, 'running', return_value=False),
            patch.object(p, 'systemctl', side_effect=lambda *args: self.calls.append(args) or StubResult()),
        ]
        for pt in self.patches:
            pt.start()
            self.addCleanup(pt.stop)

    def quiet(self, fn, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*args, **kwargs)

    def test_dry_run_does_not_change_anything(self):
        self.quiet(p.cmd_uninstall, purge=True, dry_run=True)
        self.assertTrue(self.unit.exists())
        self.assertTrue(self.bin.exists())
        self.assertTrue((self.root / 'state.json').exists())
        self.assertEqual(self.calls, [])

    def test_normal_uninstall_retains_secrets(self):
        self.quiet(p.cmd_uninstall)
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.bin.exists())
        self.assertEqual((self.root / 'state.json').read_text(), 'dummy-secret')
        self.assertEqual(self.calls[0], ('disable', '--now', 'khroxy.service'))
        self.assertEqual(self.calls[1], ('daemon-reload',))

    def test_purge_requires_exact_confirmation(self):
        with self.assertRaisesRegex(ValueError, 'confirmation'):
            self.quiet(p.cmd_uninstall, purge=True, confirmation='yes')
        self.assertTrue(self.unit.exists())
        self.assertTrue(self.bin.exists())

    def test_purge_deletes_only_owned_files(self):
        other = self.root / 'owner-notes.txt'
        other.write_text('KEEP')
        foreign = self.home / '.config/codex-xray/config.json'
        foreign.parent.mkdir(parents=True)
        foreign.write_text('KEEP-CODEX')
        self.quiet(p.cmd_uninstall, purge=True, confirmation='PURGE KHROXY')
        self.assertFalse((self.root / 'state.json').exists())
        self.assertFalse((self.root / 'runtime.json').exists())
        self.assertFalse((self.root / '.lock').exists())
        self.assertTrue(other.exists())
        self.assertEqual(foreign.read_text(), 'KEEP-CODEX')

    def test_purge_removes_owned_auto_update_units(self):
        self.autosvc.write_text('# Managed by khroxy\n[Service]\n')
        self.autotimer.write_text('# Managed by khroxy\n[Timer]\n')
        self.quiet(p.cmd_uninstall, purge=True, confirmation='PURGE KHROXY')
        self.assertFalse(self.autosvc.exists())
        self.assertFalse(self.autotimer.exists())
        self.assertIn(('disable', '--now', 'khroxy-sub-update.timer'), self.calls)
        self.assertIn(('stop', 'khroxy-sub-update.service'), self.calls)

    def test_refuse_foreign_autoupdate_unit(self):
        self.autotimer.write_text('[Timer]\nOnCalendar=hourly\n')
        with self.assertRaisesRegex(ValueError, 'Unknown subscription timer unit'):
            self.quiet(p.cmd_uninstall, purge=True, dry_run=False, confirmation='PURGE KHROXY')
        self.assertEqual(self.calls, [])
        self.assertTrue(self.bin.exists())

    def test_reject_foreign_unit(self):
        self.unit.write_text('# manually installed unrelated daemon\n')
        with self.assertRaisesRegex(ValueError, 'owner'):
            self.quiet(p.cmd_uninstall)
        self.assertTrue(self.unit.exists())
        self.assertEqual(self.calls, [])

    def test_reject_symlink_cli(self):
        self.bin.unlink()
        self.bin.symlink_to(self.home / 'target')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.quiet(p.cmd_uninstall)
        self.assertTrue(self.unit.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
