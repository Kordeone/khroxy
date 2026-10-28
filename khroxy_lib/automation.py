# KHROXY managed module
"""Stable JSON automation and deliberately private interoperable exports."""
import argparse
import base64
import copy
import json
import time
from . import importing, rotation
from .storage import Store, exclusive_file

COMMANDS = ('init','import','import-data','profiles','apps','app-add','switch','remove-profile','remove-app',
            'select','fastest','ping','health','health-config','health-watch','install','uninstall','run','test','service-env','shell-enable','shell-disable','shell-init','status','container-status',
            'sub-add','sub-update','sub-remove','sub-route','sub-auto','subs','logs','tui','menu',
            'storage','profile','engine','test-profiles','results','metadata','rotation','daemon','listener','route','dns',
            'scan','automation','export-data','api','completion','manual')


def profiles(app, limit=100, offset=0, protocol=None, enabled=None, search=''):
    if type(limit) is not int or not 1<=limit<=1000 or type(offset) is not int or offset<0 or len(search)>100:
        raise ValueError('Invalid pagination/filter')
    state=app.load();items=[]
    for name,node in sorted(state['profiles'].items()):
        available=state.get('profile_options',{}).get(name,{}).get('enabled',True)
        if (protocol and node['protocol']!=protocol) or (enabled is not None and available!=enabled) or search not in name:continue
        items.append({'name':name,'protocol':node['protocol'],'enabled':available,
                      'active':name==state.get('active_profile'),'fastest':name==state.get('fastest_profile')})
    return {'schema':1,'total':len(items),'limit':limit,'offset':offset,'profiles':items[offset:offset+limit]}


def status(app):
    state=app.load()
    return {'schema':1,'version':app.VERSION,'engine':state.get('engine','xray'),'running':app.running(),
            'profiles':len(state['profiles']),'applications':len(state['apps']),
            'active':state.get('active_profile'),'fastest':state.get('fastest_profile'),
            'health_checked_at':state.get('health_checked_at'),'pending_fastest':state.get('pending_fastest'),
            'automation_suspended':bool(state.get('rotation_error')),'direct_fallback':False}


def selection(app,name=None,target='active',rotate=False):
    if target not in ('active','fastest'):raise ValueError('Unknown selection target')
    fd=app.lock(timeout=1)
    try:
        old=app.load();state=copy.deepcopy(old);reason='manual'
        if rotate:
            name,reason=rotation.choose(app,state,force=True);target='fastest'
        if name is None or not rotation.eligible(state,name):raise ValueError('No eligible profile; existing routes retained, no direct fallback')
        if target=='fastest':
            recent=dict(state,health={name:state.get('health',{}).get(name,{})})
            if app.choose_best(recent) is None:raise ValueError('Fastest selection requires a fresh successful health measurement')
        if state.get('rotation_error') and target=='fastest':raise ValueError('Automatic selection suspended; review rotation status')
        state[target+'_profile']=name;state['selection_mode']=target
        changed=app.build_runtime(state)!=app.build_runtime(old)
        if changed and app.running():
            if target!='fastest':raise ValueError('Stop the managed runtime before changing active selection; sessions preserved')
            if not app.switch_fastest_live(old,state):
                old['pending_fastest']=name
                if state.get('rotation_error'):old['rotation_error']=state['rotation_error']
                app.write_file(app.STATE,app.serial(old))
                return {'schema':1,'applied':False,'pending':name,'reason':'session-safe switching unavailable','direct_fallback':False}
            app.write_file(app.STATE,app.serial(state))
        elif changed:app.commit(old,state)
        else:app.write_file(app.STATE,app.serial(state))
        if target=='fastest':
            rotation.note_switch(app,old,state,reason);app.write_file(app.STATE,app.serial(state))
        return {'schema':1,'applied':True,'target':target,'profile':name,'reason':reason,'direct_fallback':False}
    finally:app.os.close(fd)


def export(app,args):
    if args.format=='backup':
        if not args.include_secrets or not args.output:raise ValueError('Backups require --include-secrets and a private --output')
        Store(app.STATE).export(args.output);return {'schema':1,'private_export_written':True,'contains_credentials':True}
    state=app.load();names=args.profile or sorted(state['profiles'])
    if not 1<=len(names)<=100 or len(set(names))!=len(names) or any(name not in state['profiles'] for name in names):
        raise ValueError('Export requires 1..100 distinct existing profiles; filter larger collections explicitly')
    nodes=[copy.deepcopy(state['profiles'][name]) for name in names]
    if not args.include_secrets:
        if args.output:raise ValueError('Use automation profiles for redacted output; private exports require --include-secrets')
        return {'schema':1,'contains_credentials':False,'profiles':[{'name':name,'protocol':state['profiles'][name]['protocol']} for name in names]}
    if not args.output:raise ValueError('Credentials may only be exported to an explicit private --output file')
    if args.format=='sip008':
        servers=[]
        for name,node in zip(names,nodes):
            if node['protocol']!='shadowsocks' or node.get('streamSettings',{}):raise ValueError('SIP008 export requires raw Shadowsocks profiles only')
            server=node['settings']['servers'][0]
            servers.append({'remarks':name,'server':server['address'],'server_port':server['port'],'method':server['method'],'password':server['password']})
        raw=json.dumps({'version':1,'servers':servers},allow_nan=False).encode()
    else:
        raw=json.dumps({'outbounds':nodes},allow_nan=False).encode()
        if args.format=='base64':raw=base64.b64encode(raw)
    # Validate exact roundtrip before committing a private artifact.
    if len(raw)>app.MAX_SUB_BYTES:raise ValueError('Export exceeds supported subscription size limit')
    restored=app.parse_subscription(raw,allow_plaintext=True)
    if [importing.fingerprint(n) for n in restored]!=[importing.fingerprint(n) for n in nodes]:
        raise ValueError('Export cannot retain every protocol/security option; nothing written')
    exclusive_file(args.output,raw+b'\n')
    return {'schema':1,'private_export_written':True,'contains_credentials':True,'count':len(nodes),'format':args.format}


def completion(shell):
    words=' '.join(COMMANDS)
    if shell=='bash':return "complete -W '"+words+"' khroxy\n"
    if shell=='zsh':return "#compdef khroxy\n_arguments '1:command:("+words+")' '*::arg:->args'\n"
    if shell=='fish':return "complete -c khroxy -n '__fish_use_subcommand' -a '"+words+"'\n"
    raise ValueError('Unsupported completion shell')


MANUAL=r''' .TH KHROXY 1 "2026" "KHROXY" "User Commands"
.SH NAME
khroxy \- independent rootless per-application proxy manager
.SH SYNOPSIS
.B khroxy
.RI [ command ]
.SH DESCRIPTION
Without a command, opens the original four-screen keyboard-first TUI.
Profiles, Applications, Subscriptions, Logs; press ? for dedicated key help.
No host routing, firewall, SSH or desktop settings are modified.
.SH INSTALLATION
Run ./install.sh for the interactive native or optional AI-container installer.
Existing configuration is preserved. Engines are optional external dependencies.
.SH COMMANDS
.TP
.B profiles, apps, subs, logs
Existing human-readable inventory and diagnostics.
.TP
.B import NAME, import-data, sub-add, sub-update
Import share links, subscriptions or supported bounded files/stdin.
.TP
.B app-add, switch, select, fastest, run, service-env, shell-init
Configure per-application proxies; preserve fixed, @active and @fastest mappings.
.TP
.B health, health-config, health-watch, rotation
Periodic testing, hysteresis, cooldown, pinning and session-safe selection.
.TP
.B storage, profile
Transactional migration, private backups, restore, lifecycle and history.
.TP
.B engine, test-profiles, results, metadata
Optional engine discovery/selection, bounded tests and persistent diagnostics.
.TP
.B daemon
Private owned-process supervisor and explicit user-service preparation.
.TP
.B listener, route, dns, scan
Loopback services, explicit rules, engine-local DNS and bounded candidate scans.
.TP
.B automation status|profiles|select|rotate
Stable schema-1 JSON. Select/rotate do not trigger network testing.
Fastest requires fresh successful health; active changes require stopped runtime.
.TP
.B export-data
Validated private JSON/base64/SIP008 subscriptions or full backups.
Credentials require --include-secrets and an exclusive private --output file.
.TP
.B api token|serve
Optional authenticated loopback-only management API. No automatic startup.
.TP
.B completion bash|zsh|fish, manual
Print shell completion source or this manual in roff format.
.TP
.B uninstall
Stop only owned services/processes; preserve saved data by default.
Use --dry-run before an explicit --purge.
.SH FILES
~/.config/khroxy/state.sqlite is authoritative after migration.
The original state.json is a frozen rollback source, not a second writer.
Files containing authentication use mode 0600; configuration directory uses 0700.
.SH SECURITY
No automatic direct fallback. Direct routing requires an explicit rule.
Local proxy listeners are accessible to local processes; optional authentication
is available. This is not a host-wide VPN, TUN or application sandbox.
Never publish private backups, token files or engine configuration.
.SH EXIT STATUS
0 success; 1 rejected operation/failure; 2 invalid arguments; 130 cancellation.
Some legacy commands retain their existing output and exit behavior.
.SH SEE ALSO
Use khroxy COMMAND --help and the repository docs directory.
'''.lstrip()


def cli(app, family, argv):
    if family=='manual':
        if argv:raise ValueError('manual takes no arguments')
        print(MANUAL,end='');return
    parser=argparse.ArgumentParser(prog='khroxy '+family)
    if family=='completion':
        parser.add_argument('shell',choices=('bash','zsh','fish'));args=parser.parse_args(argv);print(completion(args.shell),end='');return
    if family=='export-data':
        parser.add_argument('--format',choices=('json','base64','sip008','backup'),default='json');parser.add_argument('--profile',action='append')
        parser.add_argument('--output');parser.add_argument('--include-secrets',action='store_true');args=parser.parse_args(argv)
        print(json.dumps(export(app,args)));return
    sub=parser.add_subparsers(dest='action',required=True);sub.add_parser('status');sub.add_parser('rotate')
    p=sub.add_parser('profiles');p.add_argument('--limit',type=int,default=100);p.add_argument('--offset',type=int,default=0)
    p.add_argument('--protocol');p.add_argument('--enabled',choices=('true','false'));p.add_argument('--search',default='')
    p=sub.add_parser('select');p.add_argument('name');p.add_argument('--target',choices=('active','fastest'),default='active')
    args=parser.parse_args(argv)
    if args.action=='status':result=status(app)
    elif args.action=='profiles':result=profiles(app,args.limit,args.offset,args.protocol,None if args.enabled is None else args.enabled=='true',args.search)
    else:result=selection(app,getattr(args,'name',None),getattr(args,'target','fastest'),args.action=='rotate')
    print(json.dumps(result,allow_nan=False))
