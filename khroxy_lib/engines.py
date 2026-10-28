# KHROXY managed module
"""Optional engine adapters. Reject unrepresentable options before launch."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import secrets
import socket
import subprocess
import tempfile

NAMES = ('xray','v2ray','sing-box')
CAPABILITIES = {'xray':{'vless','vmess','trojan','shadowsocks','http','socks'},
                'v2ray':{'vless','vmess','trojan','shadowsocks','http','socks'},
                'sing-box':{'vless','vmess','trojan','shadowsocks','http','socks','hysteria2'}}


def command(binary, name, config, check=False):
    if name not in NAMES: raise ValueError('Unknown proxy engine')
    if name=='xray': return [str(binary),'run',*(['-test'] if check else []),'-config',str(config)]
    return [str(binary), 'check' if check and name=='sing-box' else 'test' if check else 'run','-c',str(config)]


def discover(name, state=None, default_xray=None):
    if name not in NAMES: raise ValueError('Unknown proxy engine')
    configured=(state or {}).get('engine_binaries',{}).get(name)
    if configured: return Path(configured).expanduser().resolve()
    if name=='xray' and default_xray is not None: return Path(default_xray)
    local=Path.home()/'.local/bin'/name
    return local if local.is_file() else Path(shutil.which(name) or local)


def identify(binary, name):
    if name not in NAMES: raise ValueError('Unknown proxy engine')
    binary=Path(binary)
    if not binary.is_file() or not os.access(binary,os.X_OK): raise ValueError('Proxy engine binary missing or not executable')
    with binary.open('rb') as stream:
        if stream.read(4)!=b'\x7fELF': raise ValueError('Proxy engine must be a trusted Linux ELF executable')
    result=subprocess.run([str(binary),'version'],capture_output=True,text=True,timeout=5)
    pattern={'xray':r'^Xray ([0-9][\w.\-]*)','v2ray':r'^V2Ray ([0-9][\w.\-]*)','sing-box':r'^sing-box version ([0-9][\w.\-]*)'}[name]
    match=re.search(pattern,result.stdout)
    if result.returncode or not match: raise ValueError('Engine binary identity/version mismatch')
    if name=='sing-box':
        pieces=match[1].split('.')
        if tuple(int(x) for x in pieces[:2])<(1,13): raise ValueError('sing-box 1.13 or newer required')
    return match[1]


def validate(binary,name,config,root):
    identify(binary,name)
    fd,path=tempfile.mkstemp(prefix='.engine-check-',suffix='.json',dir=root)
    try:
        with os.fdopen(fd,'w') as stream: json.dump(config,stream,allow_nan=False)
        result=subprocess.run(command(binary,name,path,check=True),capture_output=True,text=True,timeout=20)
        if result.returncode: raise ValueError('Engine configuration validation failed; private upstream details withheld')
    finally:
        Path(path).unlink(missing_ok=True)


def keys(value, allowed, label):
    if not isinstance(value,dict) or set(value)-set(allowed):
        raise ValueError('Unsupported '+label+' options for selected engine; nothing discarded')


def single(values,label):
    if not isinstance(values,list) or len(values)!=1 or not isinstance(values[0],dict):
        raise ValueError('Selected engine requires exactly one '+label)
    return values[0]


def sing_outbound(node):
    keys(node,('protocol','tag','settings','streamSettings'),'outbound')
    kind=node['protocol']
    if kind not in CAPABILITIES['sing-box']: raise ValueError('Protocol unsupported by sing-box adapter')
    settings=node['settings']; keys(settings,('servers','vnext','obfs'),'upstream settings')
    server=single(settings.get('vnext' if kind in ('vless','vmess') else 'servers'),'upstream')
    keys(server,('address','port','users','password','method'),'upstream destination')
    out={'type':kind,'tag':node.get('tag','profile'),'server':server['address'],'server_port':server['port']}
    if kind in ('vless','vmess'):
        user=single(server.get('users'),'upstream identity')
        keys(user,('id','encryption','flow','security','alterId'),'identity')
        out['uuid']=user['id']
        if kind=='vless':
            if user.get('encryption','none')!='none': raise ValueError('VLESS encryption unsupported by sing-box adapter')
            if user.get('flow'): out['flow']=user['flow']
        else:
            out.update(security=user.get('security','auto'),alter_id=user.get('alterId',0))
    elif kind in ('http','socks'):
        if server.get('users'):
            auth=single(server['users'],'authentication entry'); keys(auth,('user','pass'),'authentication')
            out.update(username=auth['user'],password=auth['pass'])
        if kind=='socks': out['version']='5'
    else:
        out['password']=server['password']
        if kind=='shadowsocks': out['method']=server['method']
    if settings.get('obfs'):
        if kind!='hysteria2': raise ValueError('Obfuscation only supported for Hysteria2')
        keys(settings['obfs'],('type','password'),'obfuscation'); out['obfs']=copy.deepcopy(settings['obfs'])
    stream=node.get('streamSettings',{})
    keys(stream,('network','security','tlsSettings','realitySettings','wsSettings','grpcSettings','httpupgradeSettings'),'transport')
    security=stream.get('security','none')
    if security in ('tls','reality'):
        tls=stream.get('tlsSettings',{}) if security=='tls' else stream.get('realitySettings',{})
        keys(tls,('serverName','alpn','fingerprint','allowInsecure') if security=='tls' else ('serverName','fingerprint','password','shortId','spiderX'),'TLS/REALITY')
        if tls.get('allowInsecure'): raise ValueError('TLS verification bypass refused')
        out['tls']={'enabled':True,'server_name':tls.get('serverName',server['address'])}
        if tls.get('alpn'): out['tls']['alpn']=tls['alpn']
        if tls.get('fingerprint'): out['tls']['utls']={'enabled':True,'fingerprint':tls['fingerprint']}
        if security=='reality':
            if tls.get('spiderX'): raise ValueError('REALITY spiderX unsupported by sing-box adapter')
            out['tls']['reality']={'enabled':True,'public_key':tls['password'],'short_id':tls.get('shortId','')}
    elif security!='none': raise ValueError('Unsupported TLS/security mode')
    transport=stream.get('network','tcp')
    if transport in ('ws','httpupgrade'):
        params=stream.get('wsSettings' if transport=='ws' else 'httpupgradeSettings',{})
        keys(params,('path','host'),'WebSocket/HTTP upgrade')
        out['transport']={'type':transport,'path':params.get('path','/')}
        if params.get('host'):
            if transport=='ws': out['transport']['headers']={'Host':params['host']}
            else: out['transport']['host']=params['host']
    elif transport=='grpc':
        params=stream.get('grpcSettings',{}); keys(params,('serviceName',),'gRPC')
        out['transport']={'type':'grpc','service_name':params.get('serviceName','')}
    elif transport not in ('tcp','raw'): raise ValueError('Transport unsupported by sing-box adapter')
    return out


def adapt(config, name, management=None):
    if name not in NAMES: raise ValueError('Unknown proxy engine')
    if name=='xray':
        if any(n.get('protocol') not in CAPABILITIES['xray']|{'blackhole','freedom'} for n in config['outbounds']):
            raise ValueError('Protocol unsupported by Xray adapter; Hysteria2 requires sing-box')
        return config
    if name=='v2ray':
        for node in config['outbounds']:
            if node.get('protocol') not in CAPABILITIES[name]|{'blackhole','freedom'}: raise ValueError('Protocol unsupported by V2Ray')
            stream=node.get('streamSettings',{})
            if stream.get('security')=='reality' or stream.get('network','tcp') not in ('tcp','ws','grpc'):
                raise ValueError('Transport/security unsupported by V2Ray adapter')
            if set(stream.get('tlsSettings',{}))-{'serverName','alpn','allowInsecure'}:
                raise ValueError('Xray TLS/ECH options unsupported by V2Ray adapter')
            for server in node.get('settings',{}).get('vnext',[]):
                if any(user.get('flow') for user in server.get('users',[])): raise ValueError('VLESS flow unsupported by V2Ray adapter')
        if 'api' in config:
            config=copy.deepcopy(config)
            path=config['api'].pop('listen',None)
            if not isinstance(path,str) or not path.startswith('/'):
                raise ValueError('V2Ray routing API requires a private Unix socket')
            tag=config['api']['tag']
            config['inbounds'].append({'tag':tag,'port':1,'listen':'127.0.0.1','protocol':'dokodemo-door',
                'settings':{'address':'127.0.0.1','network':'tcp'},
                'streamSettings':{'network':'domainsocket','dsSettings':{'path':path}}})
            config['routing']['rules'].insert(0,{'type':'field','inboundTag':[tag],'outboundTag':tag})
        return config
    out={'log':{'disabled':True},'inbounds':[],'outbounds':[],'route':{'rules':[]}}
    if 'api' in config:
        if not management or not 1024<=management.get('port',0)<=65535 or len(management.get('secret',''))<32:
            raise ValueError('Initialize private engine management before live Fastest')
        out['experimental']={'clash_api':{'external_controller':f"127.0.0.1:{management['port']}",
            'secret':management['secret'],'access_control_allow_origin':['http://127.0.0.1']}}
        selector=config['routing']['balancers'][0]['selector'][0]
        out['outbounds'].append({'type':'selector','tag':'khroxy-fastest','outbounds':[n['tag'] for n in config['outbounds'] if n['tag'].startswith('profile-')],
                                'default':selector,'interrupt_exist_connections':False})
    for node in config['outbounds']:
        if node['protocol']=='blackhole': continue
        if node['protocol']=='freedom': out['outbounds'].append({'type':'direct','tag':node['tag']})
        else: out['outbounds'].append(sing_outbound(node))
    blocked = {n['tag'] for n in config['outbounds'] if n['protocol']=='blackhole'}
    for inbound in config['inbounds']:
        kind=inbound['protocol']; settings=inbound.get('settings',{})
        if kind not in ('http','socks','shadowsocks'): raise ValueError('Inbound unsupported by sing-box adapter')
        node={'type':kind,'tag':inbound['tag'],'listen':inbound['listen'],'listen_port':inbound['port']}
        if kind=='shadowsocks':
            keys(settings,('method','password','network'),'Shadowsocks inbound')
            node.update(method=settings['method'],password=settings['password'],network='tcp')
        else:
            keys(settings,('accounts','auth','udp'),'HTTP/SOCKS inbound')
            if settings.get('accounts'):
                node['users']=[{'username':u['user'],'password':u['pass']} for u in settings['accounts']]
        out['inbounds'].append(node)
    for rule in config['routing']['rules']:
        keys(rule,('type','inboundTag','outboundTag','balancerTag','domain','ip','port','network'),'routing')
        target=rule.get('outboundTag',rule.get('balancerTag'))
        node={'action':'reject'} if target in blocked else {'action':'route','outbound':target}
        if rule.get('inboundTag'): node['inbound']=rule['inboundTag']
        if rule.get('ip'): node['ip_cidr']=rule['ip']
        if rule.get('port'): node['port']=[int(p) for p in rule['port'].split(',')]
        if rule.get('network'): node['network']=rule['network'].split(',')
        # Native sing-box combines domain and domain_suffix alternatives with OR.
        for prefix,key in (('full:','domain'),('domain:','domain_suffix')):
            values=[d[len(prefix):] for d in rule.get('domain',[]) if d.startswith(prefix)]
            if values: node[key]=values
        out['route']['rules'].append(node)
    out['route']['rules'].append({'action':'reject'})
    if 'dns' in config:
        out['dns']={'servers':[{'type':'hosts','tag':'khroxy-static','path':[],
                              'predefined':config['dns']['hosts']}], 'final':'khroxy-static'}
        out['route']['default_domain_resolver']='khroxy-static'
    return out


def redact(value):
    sensitive={'settings','streamSettings','tls','transport','headers','secret','token','authorization','address','server','serverName','server_name','password','pass','user','username','id','uuid','privateKey','publicKey','shortId','short_id','echConfigList'}
    if isinstance(value,dict): return {k:'<redacted>' if k in sensitive else redact(v) for k,v in value.items()}
    if isinstance(value,list): return [redact(v) for v in value]
    return value


def cli(app, argv):
    import argparse
    from .storage import exclusive_file
    parser=argparse.ArgumentParser(prog='khroxy engine')
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('list')
    p=sub.add_parser('select'); p.add_argument('name',choices=(*NAMES,'auto')); p.add_argument('--binary')
    for action in ('check','preview'):
        p=sub.add_parser(action); p.add_argument('--name',choices=NAMES)
        if action=='preview': p.add_argument('--output'); p.add_argument('--include-secrets',action='store_true')
    args=parser.parse_args(argv); state=app.load()
    if args.action=='list':
        rows=[]
        for name in NAMES:
            binary=discover(name,state,app.XRAY)
            try: version=identify(binary,name); error=None
            except (ValueError,OSError,subprocess.TimeoutExpired): version=None; error='unavailable-or-incompatible'
            rows.append(dict(engine=name,version=version,error=error,protocols=sorted(CAPABILITIES[name])))
        print(json.dumps(rows)); return
    if args.action=='select':
        fd=app.lock()
        try:
            state=app.load()
            if app.running(): raise ValueError('Stop the managed proxy before changing engines; sessions are not interrupted automatically')
            candidates=NAMES if args.name=='auto' else (args.name,)
            if args.name=='auto' and args.binary: raise ValueError('Choose an explicit engine with --binary')
            selected=None
            for name in candidates:
                proposed=copy.deepcopy(state); proposed['engine']=name
                if args.binary: proposed.setdefault('engine_binaries',{})[name]=str(Path(args.binary).expanduser().resolve())
                prepare_management(proposed)
                binary=discover(name,proposed,app.XRAY)
                try:
                    config=app.build_runtime(proposed)
                    # An automatic choice must cover stored nodes as well as current routes.
                    if args.name=='auto':
                        for node in proposed['profiles'].values():
                            adapt({'outbounds':[dict(node,tag='candidate')],'inbounds':[],'routing':{'rules':[]}},name)
                    validate(binary,name,config,app.ROOT)
                    selected=proposed,config,name
                    break
                except (ValueError,OSError,subprocess.TimeoutExpired):
                    if args.name!='auto': raise
            if selected is None: raise ValueError('No installed compatible engine; no changes made')
            proposed,config,name=selected
            previous=app.RUNTIME.read_bytes() if app.RUNTIME.is_file() else None
            app.write_file(app.RUNTIME,app.serial(config))
            try: app.write_file(app.STATE,app.serial(proposed))
            except BaseException:
                if previous is None: app.RUNTIME.unlink(missing_ok=True)
                else: app.write_file(app.RUNTIME,previous)
                raise
            print(json.dumps({'engine':name,'changed':True,'started':False}))
        finally: app.os.close(fd)
        return
    proposed=dict(state,engine=args.name or state.get('engine','xray'))
    config=app.build_runtime(proposed)
    if args.action=='check':
        binary=discover(proposed['engine'],proposed,app.XRAY)
        validate(binary,proposed['engine'],config,app.ROOT)
        print(json.dumps({'engine':proposed['engine'],'valid':True,'version':identify(binary,proposed['engine'])}))
    elif args.output:
        if not args.include_secrets: raise ValueError('Private native config output requires explicit --include-secrets')
        exclusive_file(args.output,(json.dumps(config,indent=2)+'\n').encode())
        print(json.dumps({'private_config_written':True}))
    elif args.include_secrets:
        raise ValueError('Secrets may only be written to an explicit private --output file')
    else:
        print(json.dumps(redact(config),indent=2))


def prepare_management(state):
    if state.get('engine','xray')!='sing-box': return
    if state.get('engine_management',{}).get('sing-box'): return
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    state.setdefault('engine_management',{})['sing-box']={'port':port,'secret':secrets.token_urlsafe(32)}
