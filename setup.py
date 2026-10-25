#!/usr/bin/env python3
"""Guided rootless Podman installer for the khroxy AI workspace.

Never installs Podman, changes host firewall/SSH/systemd, or launches
containers without explicit confirmation. Uses Podman directly: no Compose.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / 'workspace-settings.json'
STAGED_XRAY = ROOT / 'docker' / 'xray'
IMAGE = 'localhost/khroxy-ai:0.9'
CONTAINER = 'khroxy-ai'
PROXY_PORT = 20819

class SetupError(ValueError):
    pass

def projects_path(raw):
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SetupError('Projects path must be absolute, e.g. /mnt/storage/Projects')
    if path.is_symlink() or not path.is_dir():
        raise SetupError('Projects path must be an existing directory, not a symlink')
    resolved = path.resolve(strict=True)
    if len(resolved.parts) < 4:
        raise SetupError('Refusing broad host mount; choose the projects folder itself')
    if ',' in str(resolved) or '\n' in str(resolved):
        raise SetupError('Projects path cannot contain commas or newlines (Podman mount syntax)')
    for restricted in ('/proc', '/sys', '/dev', '/etc', '/run', '/root', '/boot', '/usr', '/var', '/opt'):
        if resolved == Path(restricted) or Path(restricted) in resolved.parents:
            raise SetupError('Refusing to mount a protected system directory')
    if not os.access(resolved, os.R_OK | os.W_OK | os.X_OK):
        raise SetupError('Projects directory must be accessible read/write to this user')
    return resolved

def agent_uid():
    uid = os.getuid()
    if uid < 1000 or uid == 12000:
        raise SetupError('Run as a non-root project owner (UID >= 1000, != 12000)')
    return uid

def xray_source(user_path=None):
    paths = ([Path(user_path).expanduser()] if user_path else
             [Path.home()/'.local/bin/xray', Path('/usr/local/bin/xray'), Path('/usr/bin/xray')])
    for path in paths:
        if path.is_file() and os.access(path, os.X_OK):
            return path.resolve(strict=True)
    return None

def check_elf_xray(path):
    if path is None:
        raise SetupError('Xray binary missing; use --xray /path/to/trusted/xray')
    with path.open('rb') as f:
        h = f.read(20)
    if len(h) < 20 or h[:4] != b'\x7fELF' or h[4] != 2:
        raise SetupError('Xray must be a 64-bit Linux ELF executable')
    endian = '<' if h[5] == 1 else '>' if h[5] == 2 else None
    if endian is None:
        raise SetupError('Invalid Xray ELF encoding')
    machine = struct.unpack(endian+'H', h[18:20])[0]
    expected = {'x86_64':62,'aarch64':183}.get(platform.machine())
    if expected is None or machine != expected:
        raise SetupError('Xray architecture does not match this supported host')

def build_command(uid):
    return ['podman','build','--file','docker/Containerfile',
            '--build-arg',f'AGENT_UID={uid}',
            '--build-arg','CODEX_VERSION=0.164.0',
            '--build-arg','CAVEMAN_REF=v4.0.1',
            '--tag',IMAGE,'.']

def start_command(projects, host_ns):
    if not re.fullmatch(r'net:\[\d+\]', host_ns):
        raise SetupError('Host network namespace could not be identified safely')
    return ['podman','run','--detach','--name',CONTAINER,
            '--userns=keep-id','--user','0:0','--network=pasta',
            '--cap-drop=all','--cap-add=NET_ADMIN','--cap-add=SETUID',
            '--cap-add=SETGID','--cap-add=CHOWN',
            '--security-opt=no-new-privileges',
            '--read-only',
            '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=64m',
            '--tmpfs','/run:rw,nosuid,nodev,size=4m',
            '--publish',f'127.0.0.1:{PROXY_PORT}:{PROXY_PORT}/tcp',
            '--mount',f'type=bind,source={projects},destination=/workspace,rw',
            '--mount','type=volume,source=khroxy_agent_home,destination=/home/agent',
            '--mount','type=volume,source=khroxy_config,destination=/home/khroxy/.config/khroxy',
            '--env','KHROXY_CONTAINER=1',
            '--env',f'KHROXY_HOST_NETNS={host_ns}',
            '--env','KHROXY_TRUSTED_LAN_TCP=',
            IMAGE]

def preflight_podman():
    if not shutil.which('podman'):
        raise SetupError('Podman is not installed. On Debian: sudo apt install podman passt uidmap')
    try:
        p = subprocess.run(['podman','info','--format','json'],capture_output=True,text=True,timeout=15)
        if p.returncode:
            raise SetupError('Podman not usable as current user: '+(p.stderr.strip()[:300]))
        data = json.loads(p.stdout)
        if data.get('host',{}).get('security',{}).get('rootless') is not True:
            raise SetupError('Rootless Podman required; refusing rootful Podman')
        return str(data.get('version',{}).get('Version','installed'))
    except (OSError,subprocess.TimeoutExpired,json.JSONDecodeError) as e:
        raise SetupError('Podman preflight failed: '+type(e).__name__) from e

def run(cmd, *, check=True):
    return subprocess.run(cmd,cwd=ROOT,check=check,text=True)

def confirm(prompt):
    try:
        return input(prompt).strip() == 'YES'
    except EOFError:
        return False

def atomic_text(path, content, mode=0o600):
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.new',dir=path.parent)
    try:
        os.fchmod(fd,mode)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            f.write(content)
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)

def stage_xray(binary):
    if STAGED_XRAY.is_symlink():
        raise SetupError('Refusing symlink for staged Xray destination')
    fd,name=tempfile.mkstemp(prefix='xray.',dir=STAGED_XRAY.parent)
    try:
        with binary.open('rb') as src,os.fdopen(fd,'wb') as dst:
            shutil.copyfileobj(src,dst)
        os.chmod(name,0o755)
        os.replace(name,STAGED_XRAY)
    finally:
        Path(name).unlink(missing_ok=True)

def container_exists():
    r = subprocess.run(['podman','container','exists',CONTAINER],capture_output=True,text=True,timeout=10)
    if r.returncode not in (0,1):
        raise SetupError('Could not check existing Podman container safely')
    return r.returncode == 0

def test_container():
    """Wait for bootstrap firewall, then probe a non-root direct connection.

    Necessary smoke checks, not a proof against all egress / DNS bypasses.
    """
    deadline=time.monotonic()+12
    while True:
        results=[]
        for tool in ('iptables','ip6tables'):
            try:
                p=subprocess.run(['podman','exec','--user','root',CONTAINER,tool,'-S','OUTPUT'],
                                 capture_output=True,text=True,timeout=3)
            except (subprocess.TimeoutExpired,OSError):
                p=None
            results.append(bool(p and p.returncode==0 and '-P OUTPUT DROP' in p.stdout))
        if all(results):
            break
        if time.monotonic()>deadline:
            raise SetupError('Container did not install IPv4 AND IPv6 OUTPUT DROP policy')
        time.sleep(.4)
    run(['podman','exec','--user','agent',CONTAINER,
         'python3','/usr/local/bin/verify-isolation.py'])

def main(argv=None):
    ap=argparse.ArgumentParser(description='Safe guided khroxy ROOTLESS Podman installer')
    ap.add_argument('--projects',help='Existing host projects directory; prompted if omitted')
    ap.add_argument('--xray',help='Trusted local Xray executable')
    ap.add_argument('--dry-run',action='store_true',help='Check paths and display plan only')
    ap.add_argument('--configure-only',action='store_true',help='Stage configuration but do not build or start')
    ap.add_argument('--build',action='store_true',help='Build image with rootless Podman')
    ap.add_argument('--start',action='store_true',help='Build and ask approval to start container')
    args=ap.parse_args(argv)
    if args.configure_only and (args.build or args.start):
        ap.error('--configure-only conflicts with --build/--start')
    try:
        uid=agent_uid()
        previous={}
        if SETTINGS.is_file():
            previous=json.loads(SETTINGS.read_text())
        path=args.projects
        if path is None:
            if not sys.stdin.isatty():
                raise SetupError('Use --projects PATH in noninteractive mode')
            default=previous.get('projects','/mnt/storage/Projects')
            path=input(f'Host projects directory [{default}]: ').strip() or default
        projects=projects_path(path)
        binary=xray_source(args.xray)
        if binary:
            check_elf_xray(binary)
        host_ns=os.readlink('/proc/self/ns/net')
        proposed=start_command(projects,host_ns)
        print(f'Runtime: rootless Podman (no daemon, no Compose)')
        print(f'Projects: {projects} -> /workspace (read/write)')
        print(f'AI user: UID {uid}; Codex auth in a separate persistent named volume')
        print(f'Host proxy: 127.0.0.1:{PROXY_PORT}; no public/LAN listener')
        print('Trusted LAN: blocked initially; build: Node 24, npm/npx, Codex, Ultracave')
        print('Xray binary:', binary or 'MISSING; required for build')
        if args.dry_run:
            print('DRY RUN: no files, packages, firewall, or containers changed')
            return 0
        if STAGED_XRAY.is_symlink():
            raise SetupError('Refusing symlink for staged Xray destination')
        atomic_text(SETTINGS,json.dumps({'projects':str(projects),'uid':uid,'engine':'podman'},indent=2)+'\n')
        if binary:
            stage_xray(binary)
            print('Local Xray staged (excluded from Git and build secrets)')
        wants_build=args.build or args.start
        if not wants_build and not args.configure_only and sys.stdin.isatty():
            wants_build=confirm('Build Podman image now? Type YES: ')
        if not wants_build:
            print('Configured only. Build with: python3 setup.py --build --projects '+str(projects))
            return 0
        if not binary:
            raise SetupError('Missing Xray binary; cannot build')
        version=preflight_podman()
        print('Rootless Podman:',version)
        run(build_command(uid))
        wants_start=args.start
        if not wants_start and sys.stdin.isatty():
            wants_start=confirm('Start workspace container? Type YES: ')
        if wants_start:
            if not confirm('Start with private Podman network and internal DROP rules? Type YES: '):
                raise SetupError('Start declined; image built only')
            if container_exists():
                raise SetupError('Container khroxy-ai already exists. Use ./workspace.sh status or inspect it; no replacement performed')
            run(proposed)
            try:
                test_container()
            except (SetupError,subprocess.CalledProcessError,subprocess.TimeoutExpired,OSError) as e:
                print('ISOLATION CHECK FAILED: stopping container; volumes preserved',file=sys.stderr)
                run(['podman','stop','--time','3',CONTAINER],check=False)
                raise SetupError('Container did not pass isolation smoke tests: '+str(e)) from e
            print('Smoke tests passed; full DNS, UDP, IPv6 and host checks still required.')
            print('Next: ./workspace.sh tui, then ./workspace.sh login')
        else:
            print('Image built, no container started.')
        return 0
    except (SetupError,OSError,ValueError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as e:
        print('Setup stopped:',str(e),file=sys.stderr)
        return 1

if __name__=='__main__':
    sys.exit(main())
