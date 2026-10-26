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
IMAGE = 'localhost/khroxy-ai:0.9.1'
CONTAINER = 'khroxy-ai'
PROXY_PORT = 20819
DEFAULT_BUILD_PROXY_PORT = 20820  # existing native khroxy application proxy

class SetupError(ValueError):
    pass

def projects_path(raw):
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SetupError('Projects path must be absolute, e.g. /mnt/storage/Projects')
    # Reject forbidden literal paths BEFORE filesystem probes: an unprivileged
    # installer cannot even stat /root/.ssh on a locked-down CI/host.
    for restricted in ('/proc', '/sys', '/dev', '/etc', '/run', '/root', '/boot', '/usr', '/var', '/opt'):
        if path == Path(restricted) or Path(restricted) in path.parents:
            raise SetupError('Refusing to mount a protected system directory')
    try:
        if path.is_symlink() or not path.is_dir():
            raise SetupError('Projects path must be an existing directory, not a symlink')
        resolved = path.resolve(strict=True)
    except PermissionError as exc:
        raise SetupError('Projects path is not accessible by this user') from exc
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

def valid_port(raw):
    try:
        p = int(raw)
    except (TypeError, ValueError) as e:
        raise SetupError('Build proxy port must be an integer from 1 to 65535') from e
    if not 1 <= p <= 65535:
        raise SetupError('Build proxy port must be between 1 and 65535')
    return p


def build_proxy_url(port):
    return f'http://127.0.0.1:{valid_port(port)}'


def build_proxy_env(port):
    """Only affect Podman's build process; NEVER apply these to `podman run`."""
    env = os.environ.copy()
    url = build_proxy_url(port)
    for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        env[name] = url
    for name in ('NO_PROXY','no_proxy'):
        env[name] = 'localhost,127.0.0.1,::1'
    # Don't silently inherit registry-specific overrides from a retired proxy.
    env['NODE_USE_ENV_PROXY'] = '1'
    return env


def preflight_build_proxy(port):
    """Check npm registry explicitly through host khroxy; no direct fallback."""
    if not shutil.which('curl'):
        raise SetupError('curl is required for build-proxy preflight; install it on Debian')
    cmd = ['curl','--noproxy','','--proxy',build_proxy_url(port),
           '--connect-timeout','6','--max-time','18','--silent','--show-error',
           '--output','/dev/null','--write-out','%{http_code}',
           'https://registry.npmjs.org/@openai%2fcodex']
    try:
        r = subprocess.run(cmd,capture_output=True,text=True,timeout=22)
    except (OSError,subprocess.TimeoutExpired) as e:
        raise SetupError(f'Build proxy 127.0.0.1:{port} is not responding: {type(e).__name__}') from e
    if r.returncode or r.stdout.strip() != '200':
        raise SetupError(f'Build proxy 127.0.0.1:{port} cannot fetch Codex from npm '
                         f'(HTTP {r.stdout.strip() or "none"}); run khroxy test codex and retry. '
                         'No direct-network fallback will be attempted.')


def preflight_podman_build_proxy(port):
    """Verify Podman build-style pasta forwarding before expensive apt/npm steps."""
    js = ("const net=require('node:net');"
          "const s=net.connect({host:'127.0.0.1',port:Number(process.argv[1])});"
          "s.setTimeout(9000);"
          "s.on('connect',()=>{console.log('build proxy TCP reachable');s.end()});"
          "s.on('error',e=>{console.error(e.code||e.message);process.exitCode=1});"
          "s.on('timeout',()=>{console.error('timeout');process.exitCode=1;s.destroy()});")
    cmd = ['podman','run','--rm',f'--network=pasta:-T,{port}',
           '--read-only','--security-opt=no-new-privileges',
           'docker.io/library/node:24-trixie-slim','node','-e',js,str(port)]
    try:
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=45)
    except (OSError,subprocess.TimeoutExpired) as e:
        raise SetupError('Podman build-proxy network preflight failed: '+type(e).__name__) from e
    if result.returncode:
        raise SetupError('Podman pasta build networking cannot reach the local proxy '
                         f'on 127.0.0.1:{port}. The npm build has not started. '
                         'Check that pasta supports -T and the selected proxy port. '
                         'Podman: '+result.stderr.strip()[-300:])


def build_command(uid, proxy_port=DEFAULT_BUILD_PROXY_PORT):
    """pasta -T exposes ONLY the selected host TCP proxy to build RUN steps.

    Never use --network=host: that grants package installers access to all
    host-local services, and is unnecessary for a single localhost proxy.
    """
    p = valid_port(proxy_port)
    return ['podman','build',f'--network=pasta:-T,{p}', '--http-proxy=true',
            '--file','docker/Containerfile',
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

def run(cmd, *, check=True, env=None):
    return subprocess.run(cmd,cwd=ROOT,check=check,text=True,env=env)

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
    ap.add_argument('--build-proxy-port',type=int,default=DEFAULT_BUILD_PROXY_PORT,metavar='PORT',
                    help='Host khroxy HTTP proxy for Podman build (default: 20820)')
    ap.add_argument('--configure-only',action='store_true',help='Stage configuration but do not build or start')
    ap.add_argument('--build',action='store_true',help='Build image with rootless Podman')
    ap.add_argument('--start',action='store_true',help='Build and ask approval to start container')
    args=ap.parse_args(argv)
    if args.configure_only and (args.build or args.start):
        ap.error('--configure-only conflicts with --build/--start')
    try:
        uid=agent_uid()
        build_port=valid_port(args.build_proxy_port)
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
        print(f'Build downloads: via existing host khroxy at 127.0.0.1:{build_port} (no direct fallback)')
        print('Xray binary:', binary or 'MISSING; required for build')
        if args.dry_run:
            print('DRY RUN: no files, packages, firewall, or containers changed')
            return 0
        if STAGED_XRAY.is_symlink():
            raise SetupError('Refusing symlink for staged Xray destination')
        atomic_text(SETTINGS,json.dumps({'projects':str(projects),'uid':uid,'engine':'podman', 'build_proxy_port':build_port},indent=2)+'\n')
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
        preflight_build_proxy(build_port)
        preflight_podman_build_proxy(build_port)
        print('npm registry and Podman build proxy reachable; building with isolated pasta TCP forwarding')
        run(build_command(uid, build_port), env=build_proxy_env(build_port))
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
