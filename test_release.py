"""Release recovery, exact artifacts and real installed engine security checks."""
import contextlib
import copy
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import build_release
import installer
import setup
import verify_release
from khroxy_lib import engines, upgrading, supervisor
from khroxy_lib.storage import Store
from tests_support.engines import paths, process, free_port
from test_networking import Origin, Upstream, app

ROOT=Path(__file__).parent


class UpgradeUnitTests(unittest.TestCase):
    def test_snapshot_restores_exact_modules_units_and_retains_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);cli=home/'.local/bin/khroxy';cli.parent.mkdir(parents=True)
            cli.write_text('# khroxy: local-only\nold implementation\n')
            module=home/'.local/lib/khroxy/khroxy_lib';module.mkdir(parents=True)
            (module/'__init__.py').write_text('# KHROXY managed module\nold module\n')
            root=home/'.config/khroxy';root.mkdir(parents=True);(root/'owner-data').write_text('KEEP')
            identifier=upgrading.snapshot(home)
            self.assertEqual(upgrading.snapshot(home),identifier)
            cli.write_text('# khroxy: local-only\nnew implementation\n');(module/'__init__.py').write_text('# KHROXY managed module\nnew module\n')
            upgrading.restore(home,identifier,setup.atomic_text)
            self.assertIn('old implementation',cli.read_text());self.assertIn('old module',(module/'__init__.py').read_text())
            self.assertEqual((root/'owner-data').read_text(),'KEEP')
            snapshot=home/'.local/lib/khroxy/rollback-code'/identifier
            self.assertEqual(snapshot.stat().st_mode&0o777,0o700)
            (snapshot/'cli').write_text('corrupt')
            with self.assertRaisesRegex(ValueError,'checksum'):upgrading.read_snapshot(home,identifier)

    def test_snapshot_symlink_and_escape_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);cli=home/'.local/bin/khroxy';cli.parent.mkdir(parents=True);cli.symlink_to('/bin/true')
            with self.assertRaises(ValueError):upgrading.snapshot(home)
            with self.assertRaises(ValueError):upgrading.read_snapshot(home,'../foreign')

    def test_install_failure_restores_code_modules_and_original_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);binary=home/'xray';binary.write_bytes(b'fixture');binary.chmod(0o700)
            cli=home/'.local/bin/khroxy';cli.parent.mkdir(parents=True);cli.write_text('# khroxy: local-only\nprevious CLI\n');before=cli.read_bytes()
            state=home/'.config/khroxy/state.json';state.parent.mkdir(parents=True)
            legacy={'version':1,'profiles':{},'apps':{},'subscriptions':{},'setting':'preserve'};state.write_text(json.dumps(legacy));Store(state).migrate()
            original=setup.atomic_text;failed=[False]
            def failure(path,*args,**kwargs):
                if path.name=='khroxy-health.service' and not failed[0]:failed[0]=True;raise OSError('injected write failure')
                return original(path,*args,**kwargs)
            with patch.object(installer.Path,'home',return_value=home),patch.object(setup,'agent_uid',return_value=1000),patch.object(setup,'check_elf_xray'),\
                 patch.object(installer.sys.stdin,'isatty',return_value=False),patch.object(setup,'atomic_text',side_effect=failure),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(installer.main(['--mode','native','--xray',str(binary)]),1)
            self.assertEqual(cli.read_bytes(),before);self.assertEqual(Store(state).load(),legacy)
            self.assertFalse((home/'.config/systemd/user/khroxy.service').exists())
            self.assertFalse(list((home/'.local/lib/khroxy/khroxy_lib').glob('*.py')))


class ArtifactTests(unittest.TestCase):
    def test_deterministic_allowlisted_artifacts_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);one=root/'one';two=root/'two'
            a=build_release.build(one,allow_dirty=True);b=build_release.build(two,allow_dirty=True)
            self.assertEqual(a,b)
            for name in a['artifacts']:self.assertEqual((one/name).read_bytes(),(two/name).read_bytes())
            result=verify_release.verify(one,development=True);self.assertTrue(result['source_zipapp_identity'])
            source=next(one.glob('*.tar.gz'))
            import tarfile
            with tarfile.open(source) as archive:
                names=archive.getnames()
                self.assertFalse(any('/windscribe/' in name or name.endswith(('workspace-settings.json','workspace.json','docker/xray')) for name in names))
            app_path=next(one.glob('*.pyz'));app_path.write_bytes(app_path.read_bytes()+b'tampered')
            with self.assertRaisesRegex(ValueError,'checksum'):verify_release.verify(one,development=True)

    def test_ignored_private_documents_never_enter_clean_package_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,(raw,mode) in build_release.inputs(allow_untracked=True).items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            subprocess.run(['git','init','--quiet',str(root)],check=True,capture_output=True)
            subprocess.run(['git','add','.'],cwd=root,check=True,capture_output=True)
            private=root/'docs/auth.json';private.write_text('{"token":"fictional-private-document"}')
            selected=build_release.inputs(root)
            self.assertNotIn('docs/auth.json',selected)
            self.assertNotIn('fictional-private-document',str(selected.keys()))
            self.assertIn('docs/tui-baseline.json',selected)

    def test_zipapp_expansion_budget_rejects_compressed_bomb_before_read(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);build_release.build(root/'artifacts',allow_dirty=True)
            archive=next((root/'artifacts').glob('*.pyz'))
            with zipfile.ZipFile(archive,'a',compression=zipfile.ZIP_DEFLATED) as package:
                package.writestr('oversized-fixture',b'x'*(17*1024*1024))
            sums=root/'artifacts/SHA256SUMS';lines=[]
            for line in sums.read_text().splitlines():
                digest,name=line.split('  ')
                if name==archive.name:digest=hashlib.sha256(archive.read_bytes()).hexdigest()
                lines.append(digest+'  '+name)
            sums.write_text('\n'.join(lines)+'\n')
            with self.assertRaisesRegex(ValueError,'Expanded zipapp too large'):verify_release.verify(root/'artifacts',development=True)


@unittest.skipUnless(len(paths())==3,'Set all KHROXY_TEST_* engine paths')
class ReleaseIntegrationTests(unittest.TestCase):
    def test_real_engine_logs_never_include_destination_or_authentication_secrets(self):
        with tempfile.TemporaryDirectory() as tmp,ThreadingHTTPServer(('127.0.0.1',0),Upstream) as upstream:
            root=Path(tmp);threading.Thread(target=upstream.serve_forever,daemon=True).start();self.addCleanup(upstream.shutdown)
            for name,binary in paths().items():
                with self.subTest(engine=name):
                    port=free_port();node=app.parse_share(f'http://fictional:private-auth-fixture@127.0.0.1:{upstream.server_address[1]}',allow_plaintext=True)
                    value={'profiles':{'fixture':node},'apps':{'fixture':{'profile':'fixture','port':port,'command':['fixture']}},'engine':name}
                    config=app.build_runtime(value);engines.validate(binary,name,config,root)
                    path=root/(name+'.json');path.write_text(json.dumps(config))
                    with process(binary,name,path,port=port):
                        response=subprocess.run(['curl','--silent','--show-error','--noproxy','','--max-time','2','--proxy',f'http://127.0.0.1:{port}','http://127.0.0.1:12345/private-path-fixture?token=private-url-fixture'],capture_output=True,timeout=4)
                        self.assertEqual(response.stdout,b'assigned-upstream')
                    raw=Path(str(path)+'.test-log').read_text()
                    for secret in ('private-auth-fixture','private-path-fixture','private-url-fixture'):self.assertNotIn(secret,raw)

    def test_upgrade_preserves_exact_legacy_source_and_supports_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);root=home/'.config/khroxy';root.mkdir(parents=True)
            node=app.parse_share('socks5://127.0.0.1:12345',allow_plaintext=True)
            legacy={'version':1,'profiles':{'fixture':node},'apps':{'fixture':{'profile':'fixture','port':free_port(),'command':['fixture']}},'subscriptions':{}}
            raw=json.dumps(legacy).encode();(root/'state.json').write_bytes(raw)
            cli=home/'.local/bin/khroxy';cli.parent.mkdir(parents=True);cli.write_bytes((ROOT/'tests_support/legacy_khroxy.py').read_bytes());cli.chmod(0o700)
            env=dict(os.environ,HOME=tmp,XDG_RUNTIME_DIR=tmp,DBUS_SESSION_BUS_ADDRESS='unix:path='+tmp+'/no-bus')
            result=subprocess.run([str(ROOT/'install.sh'),'--mode','native','--xray',str(paths()['xray'])],env=env,input='',capture_output=True,text=True,timeout=25)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((root/'state.json').read_bytes(),raw);self.assertEqual((root/'state.json.pre-sqlite').read_bytes(),raw)
            self.assertEqual(json.loads((root/'runtime.json').read_bytes())['log']['access'],'none')
            self.assertEqual(Store(root/'state.json').load()['apps'],legacy['apps'])
            identifier=next((home/'.local/lib/khroxy/rollback-code').iterdir()).name
            result=subprocess.run([str(ROOT/'install.sh'),'--rollback-code',identifier],env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(cli.read_bytes(),(ROOT/'tests_support/legacy_khroxy.py').read_bytes())
            for verb in ('profiles','apps'):
                result=subprocess.run([str(cli),verb],env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr);self.assertIn('fixture',result.stdout)
            self.assertTrue((root/'state.sqlite').exists())

    def test_clean_artifact_install_and_managed_module_uninstall_preserve_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);artifacts=root/'artifacts';build_release.build(artifacts,allow_dirty=True)
            result=verify_release.verify(artifacts,paths()['xray'],development=True);self.assertTrue(result['isolated_installation'])
            home=root/'home';home.mkdir();env=dict(os.environ,HOME=str(home),XDG_RUNTIME_DIR=str(home),DBUS_SESSION_BUS_ADDRESS='unix:path='+str(home/'no-bus'))
            result=subprocess.run([str(ROOT/'install.sh'),'--mode','native','--xray',str(paths()['xray'])],env=env,input='',capture_output=True,text=True,timeout=25)
            self.assertEqual(result.returncode,0,result.stderr)
            config=home/'.config/khroxy';(config/'owner-project-reference').write_text('KEEP')
            original=(config/'state.sqlite').read_bytes()
            shim=home/'test-bin';shim.mkdir();systemctl=shim/'systemctl'
            systemctl.write_text('#!/bin/sh\n# Disposable test user bus; no real system service action.\ncase "$*" in *is-active*) exit 3;; *) exit 0;; esac\n');systemctl.chmod(0o700)
            env['PATH']=str(shim)+':'+env['PATH'];cli=home/'.local/bin/khroxy'
            result=subprocess.run([str(cli),'uninstall'],env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse(cli.exists());self.assertFalse((home/'.local/lib/khroxy/khroxy_lib').exists())
            self.assertEqual((config/'state.sqlite').read_bytes(),original);self.assertEqual((config/'owner-project-reference').read_text(),'KEEP')
            self.assertTrue((home/'.local/bin/xray').exists())

    def test_live_upgrade_refuses_owned_runtime_and_retains_every_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);env=dict(os.environ,HOME=tmp,XDG_RUNTIME_DIR=tmp,DBUS_SESSION_BUS_ADDRESS='unix:path='+tmp+'/no-bus')
            result=subprocess.run([str(ROOT/'install.sh'),'--mode','native','--xray',str(paths()['xray'])],env=env,input='',capture_output=True,text=True,timeout=25)
            self.assertEqual(result.returncode,0,result.stderr)
            root=home/'.config/khroxy';store=Store(root/'state.json');value=store.load()
            value['profiles']={'fixture':{'protocol':'freedom','settings':{}}};value['apps']={'fixture':{'profile':'fixture','port':free_port(),'command':['fixture']}};store.save(value)
            cli=home/'.local/bin/khroxy'
            def invoke(*args):return subprocess.run([str(cli),*args],env=env,capture_output=True,text=True,timeout=15)
            try:
                self.assertEqual(invoke('daemon','start').returncode,0);self.assertEqual(invoke('daemon','proxy-start').returncode,0)
                before=cli.read_bytes();pid=supervisor.read(root,supervisor.SESSION)['pid']
                result=subprocess.run([str(ROOT/'install.sh'),'--mode','native','--xray',str(home/'.local/bin/xray')],env=env,input='',capture_output=True,text=True,timeout=25)
                self.assertNotEqual(result.returncode,0);self.assertIn('sessions preserved',result.stderr)
                self.assertEqual(cli.read_bytes(),before);self.assertEqual(supervisor.read(root,supervisor.SESSION)['pid'],pid)
                self.assertTrue(supervisor.owned(supervisor.read(root,supervisor.SESSION)))
            finally:invoke('daemon','stop')

if __name__=='__main__':unittest.main()
