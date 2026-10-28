"""Privacy boundaries for launch environments and release artifacts."""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import build_release
import setup

ROOT = Path(__file__).parent
loader = importlib.machinery.SourceFileLoader('khroxy_privacy', str(ROOT / 'khroxy'))
spec = importlib.util.spec_from_loader(loader.name, loader)
app = importlib.util.module_from_spec(spec)
loader.exec_module(app)


class PrivacyTests(unittest.TestCase):
    def test_launcher_replaces_inherited_bypasses_with_loopback_only(self):
        state = {'apps': {'fixture': {'port': 20820, 'command': ['fixture']}}}
        with patch.object(app, 'running', return_value=True), \
             patch.object(app, 'listener_ready', return_value=True), \
             patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*'}), \
             patch.object(app.os, 'execvpe') as execute:
            app.cmd_run(state, 'fixture', [])
        environment = execute.call_args.args[2]
        for name in ('NO_PROXY', 'no_proxy'):
            self.assertEqual(environment[name], 'localhost,127.0.0.1,::1')
        self.assertEqual(environment['HTTPS_PROXY'], 'http://127.0.0.1:20820')

    def test_interactive_projects_default_comes_from_current_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with patch.object(setup, 'SETTINGS', home / 'absent.json'), \
                 patch.object(setup, 'agent_uid', return_value=1000), \
                 patch.object(setup.Path, 'home', return_value=home), \
                 patch.object(setup.sys.stdin, 'isatty', return_value=True), \
                 patch('builtins.input', return_value='') as prompt, \
                 patch.object(setup, 'projects_path', side_effect=setup.SetupError('test stopped')), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(setup.main(['--dry-run', '--caveman', 'none']), 1)
            self.assertIn(str(home / 'Projects'), prompt.call_args.args[0])

    def test_tracked_internal_documents_excluded_from_package_and_git_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, (raw, mode) in build_release.inputs(allow_untracked=True).items():
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
            private = ('docs/TASKS.md', 'docs/handoff.md', 'docs/prompt.json',
                       'AGENTS.md', 'CLAUDE.md', '.agents/instructions.md',
                       '.codex/config.toml', 'RELEASE_AUDIT.md')
            for name in private:
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text('PRIVATE-FIXTURE\n')
            subprocess.run(['git', 'init', '--quiet', str(root)], check=True, capture_output=True)
            subprocess.run(['git', 'add', '--force', '.'], cwd=root, check=True, capture_output=True)
            selected = build_release.inputs(root)
            self.assertFalse(set(private) & set(selected))
            for name in ('AGENTS.md', 'CLAUDE.md', 'docs/TASKS.md', '.agents', '.codex', 'RELEASE_AUDIT.md'):
                result = subprocess.run(['git', 'check-attr', '--cached', 'export-ignore', '--', name],
                                        cwd=root, check=True, capture_output=True, text=True)
                self.assertTrue(result.stdout.strip().endswith(': set'), result.stdout)
