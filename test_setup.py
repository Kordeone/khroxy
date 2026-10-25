"""Offline tests for Podman setup: no host Podman, firewall, or system service changes."""
import contextlib
from io import StringIO
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess
import setup

def elf64():
    a=bytearray(64);a[:6]=b'\x7fELF\x02\x01';struct.pack_into('<H',a,18,62);return bytes(a)

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.projects=self.root/'mnt/storage/Projects Alpha';self.projects.mkdir(parents=True)
        self.xray=self.root/'existing-xray';self.xray.write_bytes(elf64());self.xray.chmod(0o700)
        for key,val in [('ROOT',self.root),('SETTINGS',self.root/'workspace-settings.json'),('STAGED_XRAY',self.root/'docker/xray')]:
            val.parent.mkdir(parents=True,exist_ok=True) if isinstance(val,Path) else None
            pp=patch.object(setup,key,val);pp.start();self.addCleanup(pp.stop)
        for key,val in [('agent_uid',1000),('xray_source',self.xray)]:
            pp=patch.object(setup,key,return_value=val);pp.start();self.addCleanup(pp.stop)
        pp=patch.object(setup.platform,'machine',return_value='x86_64');pp.start();self.addCleanup(pp.stop)

    def call(self,*args):
        with contextlib.redirect_stdout(StringIO()),contextlib.redirect_stderr(StringIO()):
            return setup.main(['--projects',str(self.projects),'--xray',str(self.xray),*args])

    def test_generate_without_any_podman(self):
        with patch.object(setup,'preflight_podman',side_effect=AssertionError('Podman invoked')):
            self.assertEqual(self.call('--configure-only'),0)
        cfg=json.loads(setup.SETTINGS.read_text())
        self.assertEqual(cfg['engine'],'podman')
        self.assertEqual(cfg['projects'],str(self.projects))
        self.assertEqual((self.root/'docker/xray').read_bytes(),elf64())
        self.assertEqual(stat.S_IMODE(setup.SETTINGS.stat().st_mode),0o600)
        self.assertFalse((self.root/'compose.local.yaml').exists())

    def test_dry_run_never_writes(self):
        self.assertEqual(self.call('--dry-run'),0)
        self.assertFalse(setup.SETTINGS.exists())
        self.assertFalse(setup.STAGED_XRAY.exists())

    def test_podman_build_command_and_no_compose(self):
        cmds=[]
        with patch.object(setup,'preflight_podman',return_value='5.0'),\
             patch.object(setup,'preflight_build_proxy'),\
             patch.object(setup,'preflight_podman_build_proxy'),\
             patch.object(setup,'run',side_effect=lambda c,**kw:cmds.append(c)):
            self.assertEqual(self.call('--build'),0)
        self.assertEqual(len(cmds),1)
        self.assertEqual(cmds[0][:2],['podman','build'])
        self.assertIn('AGENT_UID=1000',cmds[0])
        self.assertNotIn('compose',str(cmds).lower())

    def test_build_uses_proxy_only_in_isolated_pasta_network(self):
        cmd=setup.build_command(1000,20820)
        self.assertIn('--network=pasta:-T,20820',cmd)
        self.assertIn('--http-proxy=true',cmd)
        self.assertNotIn('--network=host',cmd)
        env=setup.build_proxy_env(20820)
        for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
            self.assertEqual(env[name],'http://127.0.0.1:20820')
        self.assertEqual(env['NO_PROXY'],'localhost,127.0.0.1,::1')
        self.assertEqual(env['no_proxy'],'localhost,127.0.0.1,::1')
        self.assertNotIn('20820',str(setup.start_command(self.projects,'net:[123]')))

    def test_build_preflight_failure_never_runs_podman_build(self):
        cmds=[]
        with patch.object(setup,'preflight_podman',return_value='5.4.2'),\
             patch.object(setup,'preflight_build_proxy',side_effect=setup.SetupError('no proxy')) ,\
             patch.object(setup,'run',side_effect=lambda c,**kw:cmds.append(c)):
            self.assertEqual(self.call('--build'),1)
        self.assertEqual(cmds,[])

    def test_podman_build_preflight_failure_prevents_build(self):
        cmds=[]
        with patch.object(setup,'preflight_podman',return_value='5.4.2'),\
             patch.object(setup,'preflight_build_proxy'),\
             patch.object(setup,'preflight_podman_build_proxy',side_effect=setup.SetupError('bridge inaccessible')) ,\
             patch.object(setup,'run',side_effect=lambda c,**kw:cmds.append(c)):
            self.assertEqual(self.call('--build'),1)
        self.assertEqual(cmds,[])

    def test_invalid_build_port(self):
        for p in ('0','65536','-1'):
            with self.subTest(port=p):
                self.assertEqual(self.call('--dry-run','--build-proxy-port',p),1)
        with self.assertRaises(setup.SetupError):
            setup.build_command(1000,0)

    def test_host_proxy_registry_preflight_uses_explicit_proxy(self):
        p=CompletedProcess([],0,stdout='200')
        with patch.object(setup.shutil,'which',return_value='/usr/bin/curl'),\
             patch.object(setup.subprocess,'run',return_value=p) as runner:
            setup.preflight_build_proxy(20820)
        args=runner.call_args.args[0]
        self.assertIn('--noproxy',args)
        self.assertEqual(args[args.index('--proxy')+1],'http://127.0.0.1:20820')
        self.assertIn('https://registry.npmjs.org/@openai%2fcodex',args)
        with patch.object(setup.shutil,'which',return_value='/usr/bin/curl'),\
             patch.object(setup.subprocess,'run',return_value=CompletedProcess([],28,stdout='000')):
            with self.assertRaisesRegex(setup.SetupError,'No direct-network fallback'):
                setup.preflight_build_proxy(20820)

    def test_podman_proxy_reachability_uses_pasta(self):
        with patch.object(setup.subprocess,'run',return_value=CompletedProcess([],0)) as runner:
            setup.preflight_podman_build_proxy(20820)
        args=runner.call_args.args[0]
        self.assertIn('--network=pasta:-T,20820',args)
        self.assertNotIn('--network=host',args)
        with patch.object(setup.subprocess,'run',return_value=CompletedProcess([],1,stderr='connection refused')):
            with self.assertRaisesRegex(setup.SetupError,'npm build has not started'):
                setup.preflight_podman_build_proxy(20820)

    def test_start_requires_approval_and_fail_closed_validation(self):
        cmds=[]
        with patch.object(setup,'preflight_podman',return_value='5.0'),\
             patch.object(setup,'preflight_build_proxy'),\
             patch.object(setup,'preflight_podman_build_proxy'),\
             patch.object(setup,'run',side_effect=lambda c,**kw:cmds.append(c)),patch.object(setup,'confirm',return_value=False):
            self.assertEqual(self.call('--start'),1)
        self.assertEqual(len(cmds),1)
        with patch.object(setup,'preflight_podman',return_value='5.0'),\
             patch.object(setup,'preflight_build_proxy'),\
             patch.object(setup,'preflight_podman_build_proxy'),\
             patch.object(setup,'run',side_effect=lambda c,**kw:cmds.append(c)),patch.object(setup,'confirm',return_value=True),patch.object(setup,'container_exists',return_value=False),patch.object(setup,'test_container'):
            self.assertEqual(self.call('--start'),0)
        self.assertTrue(any(c[:2]==['podman','run'] for c in cmds))

    def test_start_refuses_preexisting_container(self):
        cmds=[]
        with patch.object(setup,'preflight_podman',return_value='5.0'),\
             patch.object(setup,'preflight_build_proxy'),\
             patch.object(setup,'preflight_podman_build_proxy'),\
             patch.object(setup,'run',side_effect=lambda c,**kw:cmds.append(c)),patch.object(setup,'confirm',return_value=True),patch.object(setup,'container_exists',return_value=True):
            self.assertEqual(self.call('--start'),1)
        self.assertFalse(any(c[:2]==['podman','run'] for c in cmds))

    def test_start_command_guards_network_and_mounts(self):
        c=setup.start_command(self.projects,'net:[123456]')
        self.assertIn('--userns=keep-id',c)
        self.assertEqual(c[c.index('--user')+1],'0:0')
        self.assertIn('--network=pasta',c)
        self.assertIn('127.0.0.1:20819:20819/tcp',c)
        self.assertIn(f'type=bind,source={self.projects},destination=/workspace,rw',c)
        self.assertIn('KHROXY_HOST_NETNS=net:[123456]',c)
        self.assertIn('KHROXY_TRUSTED_LAN_TCP=',c)
        self.assertIn('--cap-drop=all',c)
        self.assertNotIn('--privileged',c)
        self.assertNotIn('--network=host',c)
        self.assertNotIn('docker',str(c))
        with self.assertRaises(setup.SetupError):setup.start_command(self.projects,'')

    def test_paths_sensitive_or_mount_syntax_rejected(self):
        for p in ('/', '/mnt','/mnt/storage','/etc/ssh','/root/.ssh','/proc/self'):
            with self.subTest(path=p),self.assertRaises(setup.SetupError):setup.projects_path(p)
        link=self.root/'link';link.symlink_to(self.projects, target_is_directory=True)
        with self.assertRaises(setup.SetupError):setup.projects_path(str(link))
        comma=self.root/'mnt/storage/Proj,Alpha';comma.mkdir()
        with self.assertRaises(setup.SetupError):setup.projects_path(str(comma))

    def test_invalid_binary_doesnt_stage(self):
        self.xray.write_bytes(b'NOPE')
        self.assertEqual(self.call('--configure-only'),1)
        self.assertFalse(setup.STAGED_XRAY.exists())

    def test_preflight_rejects_rootful(self):
        with patch.object(setup.shutil,'which',return_value='/usr/bin/podman'),patch.object(setup.subprocess,'run',return_value=CompletedProcess([],0,stdout=json.dumps({'host':{'security':{'rootless':False}}}))):
            with self.assertRaisesRegex(setup.SetupError,'Rootless'):
                setup.preflight_podman()

    def test_image_contains_expected_tools(self):
        p=Path(__file__).parent/'docker/Containerfile'
        text=p.read_text()
        self.assertIn('node:24-trixie-slim',text)
        self.assertIn('npm install -g --no-audit --no-fund "@openai/codex@${CODEX_VERSION}"',text)
        self.assertIn('ultracave',text)
        self.assertIn('CODEX_HOME=/home/agent/.codex',text)

    def test_workspace_shell_never_executes_root(self):
        t=(Path(__file__).parent/'workspace.sh').read_text()
        self.assertIn('--user agent',t)
        self.assertIn('--user khroxy',t)
        self.assertIn('--user root "$name" iptables -S OUTPUT',t)
        self.assertNotIn('--user root --workdir /workspace',t)
        self.assertNotIn('docker compose',t)

if __name__=='__main__':unittest.main()

class LiveGateOfflineTests(unittest.TestCase):
    def test_checks_both_families_as_root_then_probe_as_agent(self):
        calls=[]
        def fake_subprocess(c,**kw):
            calls.append(c)
            return CompletedProcess(c,0,stdout='-P OUTPUT DROP\n')
        with patch.object(setup.subprocess,'run',side_effect=fake_subprocess),patch.object(setup,'run',side_effect=lambda c,**kw:calls.append(c)):
            setup.test_container()
        self.assertIn(['podman','exec','--user','root','khroxy-ai','iptables','-S','OUTPUT'],calls)
        self.assertIn(['podman','exec','--user','root','khroxy-ai','ip6tables','-S','OUTPUT'],calls)
        self.assertIn(['podman','exec','--user','agent','khroxy-ai','python3','/usr/local/bin/verify-isolation.py'],calls)
