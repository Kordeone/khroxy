# KHROXY managed module
"""Explicit local listeners, ordered routing and engine-local DNS policy."""
import argparse
import copy
import ipaddress
import json
import re

NAME = re.compile(r'^[a-z][a-z0-9_-]{0,30}$')
METHODS = ('aes-128-gcm', 'aes-256-gcm', 'chacha20-ietf-poly1305')


def domain(value):
    if not isinstance(value, str) or len(value) > 253:
        raise ValueError('Invalid DNS name')
    value = value.lower().rstrip('.')
    if not value or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part) for part in value.split('.')):
        raise ValueError('Use a DNS name without schemes, wildcards or routing prefixes')
    return value


def assignments(state):
    return [item['profile'] for item in state.get('listeners', {}).values()] + [
        item['profile'] for item in state.get('routing_rules', []) if item.get('action') == 'proxy']


def validate(state):
    listeners = state.get('listeners', {})
    rules = state.get('routing_rules', [])
    if not isinstance(listeners, dict) or len(listeners) > 100 or not isinstance(rules, list) or len(rules) > 100:
        raise ValueError('Limit: 100 additional listeners and 100 routing rules')
    ports = {item['port'] for item in state.get('apps', {}).values()}
    for management in state.get('engine_management', {}).values():
        if 'port' in management: ports.add(management['port'])
    for name, item in listeners.items():
        if not NAME.fullmatch(name) or not isinstance(item, dict) or set(item) - {'protocol','listen','port','profile','auth','method'}:
            raise ValueError('Invalid listener settings')
        if item.get('protocol') not in ('http','socks','shadowsocks'):
            raise ValueError('Unsupported listener protocol')
        if item.get('listen', '127.0.0.1') not in ('127.0.0.1','::1'):
            raise ValueError('Additional listeners must bind an explicit loopback address')
        port = item.get('port')
        if type(port) is not int or not 1024 <= port <= 65535 or port in ports or port == 20819:
            raise ValueError('Listener port invalid, reserved or already assigned')
        ports.add(port)
        auth = item.get('auth')
        if auth is not None:
            if not isinstance(auth, dict) or set(auth) != {'username','password'} or any(
                    not isinstance(v, str) or not v or len(v.encode()) > 255 or any(ord(c) < 32 for c in v) for v in auth.values()):
                raise ValueError('Authentication requires nonempty username/password, at most 255 bytes each')
        if item['protocol'] == 'shadowsocks':
            if not auth or item.get('method') not in METHODS:
                raise ValueError('Shadowsocks requires authentication and a supported AEAD method')
        elif 'method' in item:
            raise ValueError('Cipher method only applies to Shadowsocks')
    seen = set()
    inbounds = {'app-' + name for name in state.get('apps', {})} | {'listener-' + name for name in listeners}
    for item in rules:
        if not isinstance(item, dict) or set(item) - {'id','action','profile','domains','suffixes','cidrs','ports','inbounds','all'}:
            raise ValueError('Invalid routing rule')
        if not isinstance(item.get('id'), str) or not NAME.fullmatch(item['id']) or item['id'] in seen:
            raise ValueError('Routing rule IDs must be unique names')
        seen.add(item['id'])
        if item.get('action') not in ('direct','proxy','block') or (('profile' in item) != (item.get('action') == 'proxy')):
            raise ValueError('Only proxy rules require a profile')
        match = False
        for key in ('domains','suffixes','cidrs','ports','inbounds'):
            values = item.get(key, [])
            if not isinstance(values, list) or len(values) > 100:
                raise ValueError('Rule match fields require bounded lists')
            match |= bool(values)
            for value in values:
                if key in ('domains','suffixes'):
                    if domain(value) != value: raise ValueError('Routing DNS names must be normalized')
                elif key == 'cidrs': ipaddress.ip_network(value, strict=True)
                elif key == 'ports':
                    if type(value) is not int or not 1 <= value <= 65535: raise ValueError('Invalid destination port')
                elif value not in inbounds: raise ValueError('Unknown routing inbound')
        if not match and item.get('all') is not True:
            raise ValueError('Catch-all routing requires explicit all=true')
        # Xray combines exact and suffix domain values with OR; all other fields AND.
        if item.get('all') not in (None, True): raise ValueError('Invalid catch-all flag')
    for value in assignments(state):
        if value not in ('@active','@fastest') and value not in state.get('profiles', {}):
            raise ValueError('Listener/routing profile is missing')
    dns = state.get('dns_policy', {'mode':'system'})
    if not isinstance(dns, dict) or set(dns) - {'mode','hosts'} or dns.get('mode') not in ('system','static'):
        raise ValueError('DNS mode must be system or static')
    hosts = dns.get('hosts', {})
    if not isinstance(hosts, dict) or len(hosts) > 1000 or (hosts and dns['mode'] != 'static'):
        raise ValueError('Static DNS supports at most 1000 explicit hosts')
    for host, addresses in hosts.items():
        if domain(host) != host or not isinstance(addresses, list) or not 1 <= len(addresses) <= 16:
            raise ValueError('Static DNS requires normalized names and IP address lists')
        for address in addresses: ipaddress.ip_address(address)


def render(config, state, resolve):
    validate(state)
    active = bool(state.get('listeners') or state.get('routing_rules'))
    suffix = '#' if 'api' in config else ''
    def target(assignment):
        return {'balancerTag':'khroxy-fastest'} if suffix and assignment == '@fastest' else {
            'outboundTag':'profile-' + resolve(state, assignment) + suffix}
    defaults = []
    for name, item in sorted(state.get('listeners', {}).items()):
        kind = item['protocol']; auth = item.get('auth'); settings = {}
        if kind == 'shadowsocks':
            settings = {'method':item['method'],'password':auth['password'],'network':'tcp'}
        elif auth:
            settings['accounts'] = [{'user':auth['username'],'pass':auth['password']}]
            if kind == 'socks': settings['auth'] = 'password'
        if kind == 'socks': settings['udp'] = False
        tag = 'listener-' + name
        config['inbounds'].append({'tag':tag,'protocol':kind,'listen':item.get('listen','127.0.0.1'),
                                   'port':item['port'],'settings':settings})
        defaults.append(dict(type='field', inboundTag=[tag], **target(item['profile'])))
    custom = []
    for item in state.get('routing_rules', []):
        rule = {'type':'field'}
        if item.get('inbounds'): rule['inboundTag'] = item['inbounds']
        values = ['full:' + d for d in item.get('domains', [])] + ['domain:' + d for d in item.get('suffixes', [])]
        if values: rule['domain'] = values
        if item.get('cidrs'): rule['ip'] = item['cidrs']
        if item.get('ports'): rule['port'] = ','.join(map(str, item['ports']))
        if item['action'] == 'proxy': rule.update(target(item['profile']))
        else: rule['outboundTag'] = 'khroxy-explicit-' + item['action']
        # A native field rule must contain a matcher, including explicit catch-all.
        if len(rule) == 2: rule['network'] = 'tcp,udp'
        custom.append(rule)
    if any(r['action'] == 'direct' for r in state.get('routing_rules', [])):
        config['outbounds'].append({'tag':'khroxy-explicit-direct','protocol':'freedom','settings':{}})
    if active:
        config['outbounds'].insert(0, {'tag':'khroxy-explicit-block','protocol':'blackhole','settings':{'response':{'type':'http'}}})
    config['routing']['rules'] = custom + config['routing']['rules'] + defaults
    dns = state.get('dns_policy', {})
    if dns.get('mode') == 'static':
        config['dns'] = {'hosts':dns.get('hosts', {}),'servers':['https+local://127.0.0.1:9/dns-query'],
                         'disableFallback':True, 'queryStrategy':'UseIP'}
        for node in config['outbounds']:
            if node['protocol'] == 'freedom': node.setdefault('settings', {})['domainStrategy'] = 'UseIP'
            else:
                settings = node.get('settings', {})
                for server in settings.get('servers', []) + settings.get('vnext', []):
                    try: ipaddress.ip_address(server['address'])
                    except ValueError:
                        raise ValueError('Static DNS requires literal proxy-server IPs; TLS server names remain unchanged') from None
    return config


def cli(app, family, argv):
    parser = argparse.ArgumentParser(prog='khroxy ' + family)
    verbs = parser.add_subparsers(dest='action', required=True)
    verbs.add_parser('list' if family != 'dns' else 'show')
    if family == 'listener':
        p = verbs.add_parser('add'); p.add_argument('name'); p.add_argument('--protocol', choices=('http','socks','shadowsocks'), required=True)
        p.add_argument('--port', type=int, required=True); p.add_argument('--listen', default='127.0.0.1')
        p.add_argument('--profile', required=True); p.add_argument('--auth-stdin', action='store_true')
        p.add_argument('--method', choices=METHODS)
        p = verbs.add_parser('remove'); p.add_argument('name')
    elif family == 'route':
        p = verbs.add_parser('add'); p.add_argument('name'); p.add_argument('--action', dest='route_action', choices=('direct','proxy','block'), required=True)
        p.add_argument('--profile'); p.add_argument('--domain', action='append', default=[]); p.add_argument('--suffix', action='append', default=[])
        p.add_argument('--cidr', action='append', default=[]); p.add_argument('--port', type=int, action='append', default=[])
        p.add_argument('--inbound', action='append', default=[]); p.add_argument('--all', action='store_true')
        p = verbs.add_parser('remove'); p.add_argument('name')
    else:
        p = verbs.add_parser('set'); p.add_argument('mode', choices=('system','static')); p.add_argument('--hosts-file')
    args = parser.parse_args(argv)
    if args.action in ('list','show'):
        state = app.load()
        if family == 'listener':
            result = {name:dict({k:v for k,v in item.items() if k != 'auth'}, authenticated=bool(item.get('auth'))) for name,item in state.get('listeners', {}).items()}
        else: result = state.get('routing_rules', []) if family == 'route' else state.get('dns_policy', {'mode':'system'})
        print(json.dumps(result)); return
    auth = None
    if family == 'listener' and args.action == 'add' and args.auth_stdin:
        raw = app.sys.stdin.buffer.read(4097)
        if len(raw) > 4096: raise ValueError('Authentication input too large')
        auth = json.loads(raw)
    hosts = {}
    if family == 'dns' and args.hosts_file:
        path = app.Path(args.hosts_file)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024*1024:
            raise ValueError('Unsafe or oversized hosts file')
        with path.open('rb') as stream: raw = stream.read(1024*1024+1)
        if len(raw) > 1024*1024: raise ValueError('Hosts file too large')
        hosts = json.loads(raw)
    fd = app.lock()
    try:
        if app.running(): raise ValueError('Stop the managed proxy before changing listeners/routing/DNS; active sessions are not interrupted automatically')
        old = app.load(); state = copy.deepcopy(old)
        if family == 'listener':
            items = state.setdefault('listeners', {})
            if args.action == 'remove':
                if args.name not in items: raise ValueError('Unknown listener')
                del items[args.name]
            else:
                if args.name in items: raise ValueError('Listener already exists')
                item = dict(protocol=args.protocol, listen=args.listen, port=args.port, profile=args.profile)
                if auth is not None: item['auth'] = auth
                if args.method: item['method'] = args.method
                items[args.name] = item
        elif family == 'route':
            items = state.setdefault('routing_rules', [])
            if args.action == 'remove':
                if not any(item['id'] == args.name for item in items): raise ValueError('Unknown routing rule')
                state['routing_rules'] = [item for item in items if item['id'] != args.name]
            else:
                item = dict(id=args.name, action=args.route_action, domains=[domain(v) for v in args.domain],
                            suffixes=[domain(v) for v in args.suffix], cidrs=args.cidr, ports=args.port, inbounds=args.inbound)
                if args.profile: item['profile'] = args.profile
                if args.all: item['all'] = True
                items.append(item)
        else: state['dns_policy'] = {'mode':args.mode, 'hosts':hosts}
        validate(state)
        app.commit(old, state)
        print(json.dumps({'changed':True,'started':False}))
    finally: app.os.close(fd)
