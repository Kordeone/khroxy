"""Standalone identity contracts; no production settings or services touched."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parent
COMMANDS = {'init','import','profiles','select','ping','fastest','subs','logs','sub-add','sub-update','sub-remove','sub-route','sub-auto','remove-profile','app-add','remove-app','apps','switch','run','test','service-env','shell-enable','shell-disable','shell-init','install','status','container-status','menu','tui','uninstall'}


class IdentityTests(unittest.TestCase):
    def test_original_tui_functions_unchanged(self):
        historical = json.loads((ROOT / 'docs/tui-baseline.json').read_text())
        expected_names = {'_box', '_help_rows', '_help_screen', '_tui'}
        self.assertEqual(set(historical), expected_names)
        legacy = (ROOT / 'tests_support/legacy_khroxy.py').read_bytes()
        # Anchor the privacy-sanitized legacy fixture, then compare TUI ASTs on the same interpreter.
        # Python 3.13 omits empty fields in ast.dump by default; 3.12 includes them.
        self.assertEqual(hashlib.sha256(legacy).hexdigest(), 'e7a45129ad0eadd3af6edde5fa75ebf450e0bf81f597efb0e03ef1f99a9d4a36')
        def functions(source):
            tree = ast.parse(source)
            return {node.name: ast.dump(node, include_attributes=False)
                    for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in expected_names}
        self.assertEqual(functions((ROOT / 'khroxy').read_text()), functions(legacy))

    def test_cli_help_preserves_all_commands(self):
        result = subprocess.run([str(ROOT / 'khroxy'), '--help'], text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in COMMANDS:
            self.assertIn(name, result.stdout)

    @unittest.skipUnless(os.environ.get('KHROXY_TEST_XRAY') or Path.home().joinpath('.local/bin/xray').is_file(), 'Trusted local Xray absent')
    def test_real_native_clean_install_and_cli_initialization(self):
        binary = Path(os.environ.get('KHROXY_TEST_XRAY', str(Path.home() / '.local/bin/xray')))
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp, PYTHONDONTWRITEBYTECODE='1')
            result = subprocess.run([str(ROOT / 'install.sh'), '--mode', 'native', '--xray', str(binary)],
                                    env=env, input='', text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('No services started yet', result.stdout)
            cli = Path(tmp) / '.local/bin/khroxy'
            result = subprocess.run([str(cli), 'init'], env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(tmp) / '.config/khroxy/state.json').is_file())


if __name__ == '__main__': unittest.main()
