#!/usr/bin/env python3
"""Unified installer. Dry runs never write files or invoke a container/service."""
import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import setup
from khroxy_lib.storage import Store
from khroxy_lib import engines

ROOT = Path(__file__).resolve().parent


def systemd_arg(path):
    return '"' + str(path).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def safe_path(path, home):
    for component in (path, *path.parents):
        if component.is_symlink():
            raise setup.SetupError(f'Refusing symlink install destination: {component}')
        if component == home:
            break


def native_install(args):
    setup.agent_uid()
    home = Path.home()
    cli = home / '.local/bin/khroxy'
    state = home / '.config/khroxy/state.json'
    saved = Store(state).load() if Store(state).exists else json.loads(state.read_text()) if state.exists() else {'version': 1, 'profiles': {}, 'apps': {}, 'subscriptions': {}}
    engine = args.proxy_engine or saved.get('engine','xray')
    if engine not in engines.NAMES: raise setup.SetupError('Invalid saved proxy engine')
    xray = home / '.local/bin' / engine
    runtime = state.with_name('runtime.json')
    unit = home / '.config/systemd/user/khroxy.service'
    health_unit = unit.with_name('khroxy-health.service')
    for path in (cli, cli.with_name('khroxy.previous'), xray, state, runtime, unit, health_unit):
        safe_path(path, home)
    for path in (unit, health_unit):
        if path.exists() and not path.read_text().startswith('# Managed by khroxy\n'):
            raise setup.SetupError(f'Existing foreign unit retained: {path}')
    if engine != saved.get('engine','xray') and unit.exists():
        active = subprocess.run(['systemctl','--user','is-active','--quiet','khroxy.service'],capture_output=True,timeout=5)
        if active.returncode == 0:
            raise setup.SetupError('Stop the managed proxy before changing the installed engine; sessions are not interrupted')
    if cli.exists() and 'khroxy: local-only' not in cli.read_text(errors='replace')[:500]:
        raise setup.SetupError('Existing ~/.local/bin/khroxy is not recognized; refusing overwrite')
    if args.xray and engine != 'xray':
        raise setup.SetupError('Use --engine-binary with a non-Xray proxy engine')
    binary = Path(args.engine_binary).expanduser().resolve() if args.engine_binary else setup.xray_source(args.xray) if engine == 'xray' else engines.discover(engine,saved)
    if engine != 'xray': engines.identify(binary, engine)
    setup.check_elf_xray(binary)
    if xray.exists() and binary != xray.resolve() and binary.read_bytes() != xray.read_bytes():
        raise setup.SetupError('Existing Xray retained; omit --xray or select the installed binary')
    saved['engine'] = engine
    saved.setdefault('engine_binaries',{})[engine] = str(xray)
    loader = importlib.machinery.SourceFileLoader('khroxy_installer', str(ROOT / 'khroxy'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    policy = mod.health_settings(saved)
    if args.health_interval is not None:
        policy['interval'] = args.health_interval
    saved['health_policy'] = policy
    saved['session_safe_fastest'] = True
    mod.health_settings(saved)
    if saved.get('version') != 1:
        raise setup.SetupError('Unsupported state version; no files changed')
    proxy_unit = ('# Managed by khroxy\n[Unit]\nDescription=khroxy private per-app Xray proxy\n'
                  f'ConditionPathExists={systemd_arg(runtime)}\nAfter=network-online.target\n\n'
                  '[Service]\nType=simple\n'
                  'ExecStart=' + ' '.join(systemd_arg(arg) for arg in engines.command(xray,engine,runtime)) + '\n'
                  'Restart=on-failure\nRestartSec=3\nNoNewPrivileges=true\nUMask=0077\n\n'
                  '[Install]\nWantedBy=default.target\n')
    health_contents = ('# Managed by khroxy\n[Unit]\nDescription=khroxy periodic profile health checks\n\n'
                       '[Service]\nType=simple\n'
                       f'ExecStart={systemd_arg(cli)} health-watch\n'
                       'Restart=on-failure\nRestartSec=10\nNoNewPrivileges=true\nUMask=0077\n\n'
                       '[Install]\nWantedBy=default.target\n')
    print(f'Native plan: install ~/.local/bin/khroxy, trusted {engine} if absent, user proxy and health units.')
    print(f'Health interval: {policy["interval"]} seconds. Existing profiles/settings retained.')
    print('Fastest changes use a private routing API without restarting sessions; legacy gateways defer until maintenance.')
    if args.dry_run:
        print('DRY RUN: no files or services changed')
        return 0
    modules = list((ROOT / 'khroxy_lib').glob('*.py'))
    for source in modules:
        target = home / '.local/lib/khroxy/khroxy_lib' / source.name
        safe_path(target, home)
        if target.exists() and not target.read_text().startswith('# KHROXY managed module'):
            raise setup.SetupError('Foreign module retained; refusing overwrite')
    # Installation does not restart a running gateway, including during upgrades.
    fd = None
    try:
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        state.parent.chmod(0o700)
        mod.ROOT = state.parent
        mod.STATE = state
        fd = mod.lock()
        if state.exists():
            current = Store(state).load() if Store(state).exists else json.loads(state.read_text())
            if current.get('version') != 1:
                raise setup.SetupError('Unsupported state version; no files changed')
            current['health_policy'] = policy
            current['session_safe_fastest'] = True
            current['engine'] = engine
            current.setdefault('engine_binaries',{})[engine] = str(xray)
            saved = current
        for path in (cli, xray, unit):
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if cli.exists() and cli.read_bytes() != (ROOT / 'khroxy').read_bytes():
            setup.atomic_text(cli.with_name('khroxy.previous'), cli.read_text(), 0o700)
        for source in modules:
            target = home / '.local/lib/khroxy/khroxy_lib' / source.name
            target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            setup.atomic_text(target, source.read_bytes(), 0o600)
        setup.atomic_text(cli, (ROOT / 'khroxy').read_text(), 0o755)
        if not xray.exists():
            setup.atomic_text(xray, binary.read_bytes(), 0o700)
        if Store(state).exists:
            Store(state).save(saved)
        else:
            setup.atomic_text(state, json.dumps(saved, indent=2) + '\n')
            Store(state).migrate()
        setup.atomic_text(unit, proxy_unit, 0o644)
        setup.atomic_text(health_unit, health_contents, 0o644)
    finally:
        if fd is not None:
            os.close(fd)
    print('Installed. Add ~/.local/bin to PATH if needed. No services started yet.')
    enable = args.enable_services
    if not enable and sys.stdin.isatty():
        enable = setup.confirm('Enable user proxy and periodic health services now? Type YES: ')
    elif enable:
        enable = setup.confirm('Enable user proxy and periodic health services now? Type YES: ')
    if enable:
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True, timeout=20)
        # enable --now does not restart an already running service.
        subprocess.run(['systemctl', '--user', 'enable', '--now', 'khroxy.service', 'khroxy-health.service'], check=True, timeout=30)
    else:
        print('Enable later: systemctl --user daemon-reload; systemctl --user enable --now khroxy.service khroxy-health.service')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('native', 'container'))
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--xray')
    parser.add_argument('--proxy-engine', choices=engines.NAMES)
    parser.add_argument('--engine-binary')
    parser.add_argument('--health-interval', type=int)
    parser.add_argument('--enable-services', action='store_true', help='Ask explicitly before enabling user services')
    args, extra = parser.parse_known_args(argv)
    if args.mode is None:
        if not sys.stdin.isatty():
            parser.error('Use --mode native or --mode container in noninteractive mode')
        choice = input('Install A) Native Linux or B) AI Container [A]: ').strip().lower()
        if choice not in ('', 'a', 'b', 'native', 'container'):
            parser.error('Choose A or B')
        args.mode = 'container' if choice in ('b', 'container') else 'native'
    try:
        if args.mode == 'native':
            if extra:
                parser.error('Unknown native options: ' + ' '.join(extra))
            return native_install(args)
        if args.health_interval is not None or args.enable_services or args.proxy_engine or args.engine_binary:
            parser.error('Health interval / user services options apply to native mode only')
        if not any(arg == '--engine' or arg.startswith('--engine=') for arg in extra) and sys.stdin.isatty():
            default = 'podman'
            if setup.SETTINGS.exists():
                default = json.loads(setup.SETTINGS.read_text()).get('engine', default)
            engine = input(f'Engine: podman (rootless) / docker [{default}]: ').strip() or default
            extra += ['--engine', engine]
        if args.dry_run:
            extra.append('--dry-run')
        if args.xray:
            extra += ['--xray', args.xray]
        return setup.main(extra)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Installation stopped:', error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
