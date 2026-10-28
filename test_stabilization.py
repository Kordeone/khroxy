"""Offline stabilization regressions. No containers, services, or firewall changes."""
import contextlib
import copy
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import installer
import setup


def load(name, file):
    loader = importlib.machinery.SourceFileLoader(name, str(Path(__file__).parent / file))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


p = load('khroxy_stabilization', 'khroxy')
caveman = load('khroxy_caveman_test', 'docker/caveman.py')
OUT = {'protocol': 'vless', 'settings': {'vnext': [{'address': '198.51.100.2', 'port': 443, 'users': []}]}}


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = dict(version=1, profiles={'main': OUT, 'backup': OUT}, subscriptions={},
            apps={'dynamic': {'port': 20820, 'profile': '@fastest', 'command': ['test']},
                  'fixed': {'port': 20821, 'profile': 'main', 'command': ['test']}},
            fastest_profile='main', session_safe_fastest=True)
        for name, val in [('ROOT', self.root), ('STATE', self.root / 'state.json'),
                          ('RUNTIME', self.root / 'runtime.json'), ('EVENTS', self.root / 'events.log'),
                          ('CONTAINER_MODE', False)]:
            pt = patch.object(p, name, val)
            pt.start()
            self.addCleanup(pt.stop)
        self.save()

    def save(self):
        p.STATE.write_text(json.dumps(self.state))

    def result(self, ms):
        return {'ok': ms is not None, 'ms': ms, 'time': int(time.time())}

    def cycle(self, main=100, backup=30, live=False):
        with patch.object(p, 'probe_profile', side_effect=[self.result(main), self.result(backup)]), \
             patch.object(p, 'running', return_value=live), patch.object(p, 'xray_test'), \
             contextlib.redirect_stdout(io.StringIO()):
            p.background_health()
        return p.load()

    def test_stopped_gateway_selects_best_and_preserves_fixed_assignment(self):
        new = self.cycle()
        self.assertEqual(new['fastest_profile'], 'backup')
        self.assertEqual(new['apps'], self.state['apps'])
        self.assertIn('Fastest: backup', new['health_status'])
        config = json.loads(p.RUNTIME.read_text())
        self.assertEqual(config['routing']['rules'][1]['outboundTag'], 'profile-main#')

    def test_hysteresis_retains_current_for_small_gain(self):
        self.assertEqual(self.cycle(100, 95)['fastest_profile'], 'main')

    def test_failed_current_selects_healthy_backup(self):
        self.assertEqual(self.cycle(None, 200)['fastest_profile'], 'backup')

    def test_zero_healthy_reports_failure_and_retains_routes(self):
        with self.assertRaisesRegex(ValueError, 'No healthy profiles'):
            self.cycle(None, None)
        self.assertEqual(p.load()['fastest_profile'], 'main')
        self.assertTrue(all(not item['ok'] for item in p.load()['health'].values()))

    def test_missing_profiles_fail_without_service_calls(self):
        self.state.update(profiles={}, apps={})
        self.save()
        with patch.object(p, 'systemctl', side_effect=AssertionError('service call')):
            with self.assertRaisesRegex(ValueError, 'No healthy profiles'):
                self.cycle()

    def test_deleted_selection_is_not_a_candidate(self):
        self.state['fastest_profile'] = 'gone'
        self.save()
        self.assertEqual(self.cycle()['fastest_profile'], 'backup')

    def test_missing_fixed_profile_is_reported_without_changing_assignment(self):
        del self.state['profiles']['main']
        self.save()
        with self.assertRaises(ValueError), patch.object(p, 'systemctl', side_effect=AssertionError('service call')):
            self.cycle()
        self.assertEqual(p.load()['apps']['fixed']['profile'], 'main')

    def test_live_legacy_gateway_defers_without_restart(self):
        with patch.object(p, 'systemctl', side_effect=AssertionError('service call')):
            new = self.cycle(live=True)
        self.assertEqual(new['fastest_profile'], 'main')
        self.assertEqual(new['pending_fastest'], 'backup')

    def test_stopped_legacy_runtime_migrates_even_without_profile_change(self):
        legacy = dict(self.state, session_safe_fastest=False)
        p.RUNTIME.write_text(json.dumps(p.build_runtime(legacy)))
        new = self.cycle(50, 100)
        self.assertEqual(new['fastest_profile'], 'main')
        self.assertIn('api', json.loads(p.RUNTIME.read_text()))

    def test_live_switch_uses_only_routing_override(self):
        p.RUNTIME.write_text(json.dumps(p.build_runtime(self.state)))
        with patch.object(p.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run, \
             patch.object(p, 'systemctl', side_effect=AssertionError('restart')):
            new = self.cycle(live=True)
        self.assertEqual(new['fastest_profile'], 'backup')
        self.assertNotIn('pending_fastest', new)
        self.assertEqual(run.call_args.args[0][1:3], ['api', 'bo'])
        self.assertEqual(run.call_args.args[0][-1], 'profile-backup#')
        self.assertIn('--server=unix://' + str(self.root / 'routing.sock'), run.call_args.args[0])

    def test_api_failure_restores_runtime_and_defers(self):
        original = json.dumps(p.build_runtime(self.state))
        p.RUNTIME.write_text(original)
        for result in (subprocess.CompletedProcess([], 1), subprocess.TimeoutExpired('xray', 6)):
            with self.subTest(result=type(result).__name__):
                kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch.object(p.subprocess, 'run', **kwargs):
                    new = self.cycle(live=True)
                self.assertEqual(new['fastest_profile'], 'main')
                self.assertEqual(p.RUNTIME.read_text(), original)

    def test_changed_outbound_cannot_be_applied_to_live_gateway(self):
        p.RUNTIME.write_text(json.dumps(p.build_runtime(self.state)))
        self.state['profiles']['backup'] = dict(OUT, settings={})
        self.save()
        with patch.object(p.subprocess, 'run', side_effect=AssertionError('API used for edited profile')):
            self.assertEqual(self.cycle(live=True)['pending_fastest'], 'backup')

    def test_outdated_results_discarded_for_concurrent_profile_edit(self):
        def probe(outbound):
            self.state['profiles'] = {'main': dict(OUT, settings={})}
            self.save()
            return self.result(50)
        with patch.object(p, 'probe_profile', side_effect=probe), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'No healthy profiles'):
                p.background_health()
        self.assertEqual(p.load().get('health', {}), {})

    def test_prefix_profile_names_do_not_match_balancer_selector(self):
        self.state['profiles']['main-extra'] = OUT
        config = p.build_runtime(self.state)
        self.assertEqual(config['routing']['balancers'][0]['selector'], ['profile-main#'])
        self.assertNotIn('freedom', {out['protocol'] for out in config['outbounds']})
        self.assertEqual(config['api']['services'], ['RoutingService'])
        self.assertTrue(config['api']['listen'].startswith(str(self.root)))

    def test_invalid_policy_rejected(self):
        for policy in ({'interval': 0}, {'min_improvement_ratio': 1.1}, {'min_improvement_ms': -1}):
            with self.assertRaises(ValueError):
                p.health_settings({'health_policy': policy})

    def test_stale_health_cannot_select_profile(self):
        self.state['health'] = {'main': dict(self.result(10), time=int(time.time()) - 10000)}
        self.assertIsNone(p.choose_stable_best(self.state))


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.binary = self.home / 'trusted-xray'
        header = bytearray(64)
        header[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', header, 18, 62)
        self.binary.write_bytes(header)
        self.binary.chmod(0o700)
        for pt in (patch.object(installer.Path, 'home', return_value=self.home),
                   patch.object(setup, 'agent_uid', return_value=1000),
                   patch.object(setup.platform, 'machine', return_value='x86_64'),
                   patch.object(installer.sys.stdin, 'isatty', return_value=False)):
            pt.start()
            self.addCleanup(pt.stop)

    def call(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return installer.main(['--mode', 'native', '--xray', str(self.binary), *args])

    def test_native_dry_run_writes_nothing_calls_no_services(self):
        with patch.object(installer.subprocess, 'run', side_effect=AssertionError('service call')):
            self.assertEqual(self.call('--dry-run'), 0)
        self.assertEqual(list(self.home.iterdir()), [self.binary])

    def test_native_install_is_idempotent_preserves_settings_and_no_service_calls(self):
        state = self.home / '.config/khroxy/state.json'
        state.parent.mkdir(parents=True)
        saved = {'version': 1, 'profiles': {'main': OUT}, 'apps': {}, 'subscriptions': {}, 'owner_setting': 17}
        state.write_text(json.dumps(saved))
        with patch.object(installer.subprocess, 'run', side_effect=AssertionError('service call')):
            self.assertEqual(self.call('--health-interval', '60'), 0)
            self.assertEqual(self.call('--health-interval', '60'), 0)
        new = json.loads(state.read_text())
        self.assertEqual(new['profiles'], saved['profiles'])
        self.assertEqual(new['owner_setting'], 17)
        self.assertEqual(new['health_policy']['interval'], 60)
        self.assertTrue(new['session_safe_fastest'])
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.home / '.config/systemd/user/khroxy-health.service').exists())

    def test_foreign_unit_preserved(self):
        unit = self.home / '.config/systemd/user/khroxy.service'
        unit.parent.mkdir(parents=True)
        unit.write_text('foreign')
        self.assertEqual(self.call(), 1)
        self.assertEqual(unit.read_text(), 'foreign')
        self.assertFalse((self.home / '.local/bin/khroxy').exists())

    def test_native_upgrade_symlink_rejected(self):
        local = self.home / '.local'
        local.symlink_to(self.home / 'elsewhere')
        self.assertEqual(self.call(), 1)

    def test_docker_arguments_keep_runtime_private(self):
        with patch.object(setup, 'ENGINE', 'docker'):
            start = setup.start_command(Path('/srv/work/projects'), 'net:[123]')
            self.assertIn('--network=bridge', start)
            self.assertNotIn('--userns=keep-id', start)
            self.assertIn('127.0.0.1:20819:20819/tcp', start)
            self.assertIn('--cap-add=NET_ADMIN', start)
            self.assertNotIn('--privileged', start)
            self.assertNotIn('--network=host', start)
            with self.assertRaises(setup.SetupError):
                setup.build_command(1000)
            build = setup.build_command(1000, docker_proxy='http://host.docker.internal:20820')
            self.assertIn('HTTP_PROXY=http://host.docker.internal:20820', build)

    def test_diagnostics_distinguish_query_failure_from_missing_policy(self):
        with patch.object(setup, 'wait_for_startup'), \
             patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'private-value')), \
             patch.object(setup.time, 'monotonic', side_effect=[0, 20]), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaisesRegex(setup.SetupError, 'installation and exec permissions'):
                setup.test_container()
        self.assertIn('exec/query failed', stderr.getvalue())
        self.assertNotIn('private-value', stderr.getvalue())

    def test_policy_verification_rejects_substring(self):
        def fake(cmd, **kw):
            return subprocess.CompletedProcess([], 0, 'prefix -P OUTPUT DROP suffix\n', '')
        with patch.object(setup, 'wait_for_startup'), patch.object(setup.subprocess, 'run', side_effect=fake), \
             patch.object(setup.time, 'monotonic', side_effect=[0, 20]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(setup.SetupError):
                setup.test_container()

    def test_drop_policies_without_ready_marker_do_not_pass(self):
        def fake(cmd, **kw):
            return subprocess.CompletedProcess([], 1 if 'test' in cmd else 0, '-P OUTPUT DROP\n', '')
        with patch.object(setup, 'wait_for_startup'), patch.object(setup.subprocess, 'run', side_effect=fake), \
             patch.object(setup.time, 'monotonic', side_effect=[0, 20]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(setup.SetupError):
                setup.test_container()

    def test_docker_preflight_refuses_remote_daemon_without_calls(self):
        with patch.dict(os.environ, {'DOCKER_HOST': 'tcp://remote.invalid:2375'}), \
             patch.object(setup.shutil, 'which', return_value='/usr/bin/docker'), \
             patch.object(setup.subprocess, 'run', side_effect=AssertionError('remote call')):
            with self.assertRaisesRegex(setup.SetupError, 'Remote Docker'):
                setup.preflight_docker()

    def test_recipe_and_context_allowlists_stay_synchronized(self):
        root = Path(__file__).parent
        self.assertEqual((root / 'docker/Containerfile').read_bytes(), (root / 'docker/Dockerfile').read_bytes())
        # Ignore all descendants again after allowing traversal of docker/.
        for name in ('.dockerignore', '.containerignore'):
            content = (root / name).read_text()
            self.assertIn('!docker\ndocker/**\n', content)
            self.assertIn('!docker/caveman.py', content)

    def test_authentication_project_directory_refused(self):
        for part in ('.ssh', '.aws', '.codex', '.config', '.gnupg'):
            path = self.home / part
            path.mkdir()
            with self.assertRaisesRegex(setup.SetupError, 'authentication'):
                setup.projects_path(str(path))


class CavemanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'home'
        self.home.mkdir()
        self.source = Path(self.tmp.name) / 'source'
        for name in ('caveman', 'megacave', 'ultracave', 'extra'):
            path = self.source / 'skills' / name / 'SKILL.md'
            path.parent.mkdir(parents=True)
            path.write_text('skill content ' + name)

    def manage(self, suite):
        with contextlib.redirect_stdout(io.StringIO()):
            caveman.manage(self.home, self.source, suite)

    def test_essentials_full_none_preserve_auth_no_hooks(self):
        auth = self.home / '.codex/auth.json'
        auth.parent.mkdir()
        auth.write_text('test-auth')
        self.manage('essentials')
        self.assertEqual(len(list((self.home / '.agents/skills').iterdir())), 3)
        self.manage('full')
        self.assertEqual(len(list((self.home / '.agents/skills').iterdir())), 4)
        self.manage('none')
        self.assertEqual(list((self.home / '.agents/skills').iterdir()), [])
        self.assertEqual(auth.read_text(), 'test-auth')
        self.assertFalse((auth.parent / 'hooks.json').exists())

    def test_modified_skill_not_overwritten_or_removed(self):
        self.manage('essentials')
        skill = self.home / '.agents/skills/caveman/SKILL.md'
        skill.write_text('owner changes')
        for suite in ('essentials', 'none'):
            with self.assertRaisesRegex(ValueError, 'conflict'):
                self.manage(suite)
        self.assertEqual(skill.read_text(), 'owner changes')

    def test_unrelated_empty_skill_directory_retained(self):
        foreign = self.home / '.agents/skills/owner-skill'
        foreign.mkdir(parents=True)
        self.manage('essentials')
        self.manage('none')
        self.assertTrue(foreign.is_dir())

    def test_foreign_skill_and_symlink_preserved(self):
        skill = self.home / '.agents/skills/caveman/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('foreign')
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.manage('essentials')
        skill.unlink()
        skill.symlink_to(self.source / 'skills/caveman/SKILL.md')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.manage('essentials')


if __name__ == '__main__':
    unittest.main()
