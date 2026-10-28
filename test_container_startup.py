"""Offline rootfs/mount and bootstrap regressions; never start a real container."""
import contextlib
import errno
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import setup

ROOT = Path(__file__).parent
loader = importlib.machinery.SourceFileLoader('khroxy_runtime_paths', str(ROOT / 'docker/runtime-paths.py'))
spec = importlib.util.spec_from_loader(loader.name, loader)
runtime = importlib.util.module_from_spec(spec)
loader.exec_module(runtime)


def mountinfo(root_mode='ro', omit=None, readonly=None):
    paths = [('/', root_mode)] + [(p, 'ro' if p == readonly else 'rw') for p in runtime.WRITABLE_MOUNTS if p != omit]
    return '\n'.join(f'{i} 1 0:1 / {path} {mode},nosuid - tmpfs tmpfs rw' for i, (path, mode) in enumerate(paths, 2))


class RuntimePathsTests(unittest.TestCase):
    def test_readonly_root_and_dedicated_writable_mounts_accepted(self):
        runtime.check_layout(mountinfo())

    def test_writable_root_refused(self):
        with self.assertRaisesRegex(ValueError, 'root must be read-only'):
            runtime.check_layout(mountinfo(root_mode='rw'))

    def test_missing_or_readonly_runtime_mount_refused(self):
        for path in runtime.WRITABLE_MOUNTS:
            for kwargs in ({'omit': path}, {'readonly': path}):
                with self.subTest(path=path, kwargs=kwargs), self.assertRaisesRegex(ValueError, 'missing writable mount'):
                    runtime.check_layout(mountinfo(**kwargs))

    def test_mountinfo_unescapes_paths(self):
        self.assertIn('/test folder', runtime.mounts('1 0 0:1 / /test\\040folder rw - tmpfs tmpfs rw'))

    def test_writable_leaf_under_nonwritable_parents_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / 'image-home'
            leaf = parent / '.config/khroxy'
            leaf.mkdir(parents=True)
            saved = leaf / 'state.json'
            saved.write_bytes(b'fixture-profile-content')
            parent.chmod(0o555)
            leaf.parent.chmod(0o555)
            try:
                runtime.check_writable(leaf)
                self.assertEqual(saved.read_bytes(), b'fixture-profile-content')
                self.assertEqual(list(leaf.iterdir()), [saved])
                self.assertEqual(parent.stat().st_mode & 0o777, 0o555)
            finally:
                parent.chmod(0o755)
                leaf.parent.chmod(0o755)

    def test_readonly_filesystem_error_is_not_treated_as_writable(self):
        # Simulated EROFS: no mount or host filesystem changes are performed.
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(runtime.tempfile, 'mkstemp', side_effect=OSError(errno.EROFS, 'Read-only file system')):
            with self.assertRaises(OSError) as error:
                runtime.check_writable(Path(tmp))
            self.assertEqual(error.exception.errno, errno.EROFS)

    def test_symlink_runtime_directory_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / 'link'
            link.symlink_to(Path(tmp), target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlinked'):
                runtime.check_writable(link)

    def test_image_parents_precreated_without_relaxing_credential_volume(self):
        text = (ROOT / 'docker/Containerfile').read_text()
        self.assertIn('chmod 0755 /home /home/khroxy', text)
        self.assertIn('install -d -m 0755 -o khroxy -g khroxy /home/khroxy/.config', text)
        self.assertIn('install -d -m 0700 -o khroxy -g khroxy /home/khroxy/.config/khroxy', text)
        entry = (ROOT / 'docker/entrypoint.sh').read_text()
        self.assertNotIn('mkdir -p /home', entry)
        self.assertIn('gosu khroxy chmod 0700', entry)
        self.assertIn('gosu khroxy python3 /usr/local/bin/runtime-paths.py khroxy', entry)
        self.assertIn('gosu agent python3 /usr/local/bin/runtime-paths.py agent', entry)
        self.assertNotIn('chown -R', entry)

    def test_existing_volume_names_mappings_and_capabilities_preserved(self):
        with patch.object(setup, 'ENGINE', 'podman'):
            cmd = setup.start_command(Path('/fixture/projects'), 'net:[123]')
        self.assertIn('--read-only', cmd)
        self.assertIn('--userns=keep-id', cmd)
        self.assertEqual(cmd[cmd.index('--user') + 1], '0:0')
        self.assertEqual({v for v in cmd if v.startswith('--cap-add=')},
                         {'--cap-add=NET_ADMIN', '--cap-add=CHOWN', '--cap-add=SETUID', '--cap-add=SETGID'})
        self.assertIn('type=volume,source=khroxy_config,destination=/home/khroxy/.config/khroxy', cmd)
        self.assertIn('type=volume,source=khroxy_agent_home,destination=/home/agent', cmd)
        self.assertNotIn(':U', str(cmd))

    def test_agent_project_access_failure_is_reported_without_writing_projects(self):
        user = SimpleNamespace(pw_uid=os.geteuid(), pw_gid=os.getegid())
        with patch.object(runtime.pwd, 'getpwnam', return_value=user), \
             patch.object(runtime.Path, 'read_text', return_value=mountinfo()), \
             patch.object(runtime, 'check_writable') as check, \
             patch.object(runtime.os, 'access', return_value=False), \
             patch('sys.argv', ['runtime-paths.py', 'agent']), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runtime.main(), 1)
        self.assertEqual([call.args[0] for call in check.call_args_list], [Path('/home/agent'), Path('/tmp')])
        self.assertIn('project mount inaccessible to agent UID/GID', output.getvalue())


class StartupClassificationTests(unittest.TestCase):
    def test_verify_existing_never_stages_settings_or_checks_for_install(self):
        with patch.object(setup, 'test_container') as verify, \
             patch.object(setup, 'atomic_text', side_effect=AssertionError('settings write')), \
             patch.object(setup, 'agent_uid', side_effect=AssertionError('install check')):
            self.assertEqual(setup.main(['--engine', 'podman', '--verify-existing']), 0)
        verify.assert_called_once()

    def test_verify_existing_cannot_override_dry_run(self):
        with patch.object(setup, 'test_container', side_effect=AssertionError('engine called')), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            setup.main(['--verify-existing', '--dry-run'])
        self.assertEqual(error.exception.code, 2)

    def test_exited_startup_not_reported_as_firewall_verification_failure(self):
        with patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'exited 1\n')), \
             patch.object(setup, 'isolation_diagnostics') as diagnostics:
            with self.assertRaisesRegex(setup.SetupError, 'startup failed.*before isolation verification'):
                setup.test_container()
        diagnostics.assert_called_once()

    def test_running_without_startup_marker_times_out_before_firewall_queries(self):
        calls = []
        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess([], 0, 'running 0\n') if cmd[1] == 'inspect' else subprocess.CompletedProcess([], 1, '')
        with patch.object(setup.subprocess, 'run', side_effect=run), patch.object(setup, 'isolation_diagnostics'), \
             patch.object(setup.time, 'monotonic', side_effect=[0, 30]):
            with self.assertRaisesRegex(setup.SetupError, 'startup readiness timed out; isolation verification not attempted'):
                setup.test_container()
        self.assertFalse(any('iptables' in cmd or 'ip6tables' in cmd for cmd in calls))

    def test_firewall_failure_after_startup_remains_fatal(self):
        with patch.object(setup, 'wait_for_startup'), patch.object(setup, 'isolation_diagnostics'), \
             patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '')), \
             patch.object(setup.time, 'monotonic', side_effect=[0, 30]):
            with self.assertRaisesRegex(setup.SetupError, 'firewall verification failed after startup readiness'):
                setup.test_container()

    def test_exit_after_ready_not_misclassified_as_missing_firewall_policy(self):
        def run(cmd, **kw):
            return (subprocess.CompletedProcess([], 0, 'exited 1\n') if cmd[1] == 'inspect'
                    else subprocess.CompletedProcess([], 1, ''))
        with patch.object(setup, 'wait_for_startup'), patch.object(setup, 'isolation_diagnostics'), \
             patch.object(setup.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(setup.SetupError, 'process exited during isolation verification'):
                setup.test_container()

    def test_startup_diagnostics_include_only_prefixed_bootstrap_logs(self):
        logs = 'private-data\nkhroxy-startup: FAILED stage=runtime-paths exit=1\n'
        with patch.object(setup.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, logs, '')), \
             contextlib.redirect_stderr(io.StringIO()) as output:
            setup.isolation_diagnostics()
        self.assertIn('stage=runtime-paths', output.getvalue())
        self.assertNotIn('private-data', output.getvalue())

    def test_entrypoint_stage_failure_after_guard_success(self):
        # Simulated commands only: the copied shell never runs a real guard,
        # chown, gosu, install, or persistent process.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, body in {
                'network-guard.sh': 'echo "khroxy network guard installed inside container namespace (public AI egress DROP)"',
                'chown': 'exit 0',
                'gosu': 'case "$*" in *runtime-paths.py*) exit 1;; *) exit 0;; esac',
                'install': 'exit 0',
                'sleep': 'exit 0',
            }.items():
                path = root / name
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o700)
            script = root / 'entrypoint.sh'
            script.write_text((ROOT / 'docker/entrypoint.sh').read_text().replace('/usr/local/bin/', str(root) + '/'))
            env = dict(os.environ, PATH=str(root) + ':' + os.environ.get('PATH', ''))
            result = subprocess.run(['sh', str(script)], env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn('network guard installed', result.stdout)
        self.assertIn('khroxy-startup: FAILED stage=runtime-paths', result.stderr)
        self.assertNotIn('khroxy-startup: READY', result.stderr)


if __name__ == '__main__':
    unittest.main()
