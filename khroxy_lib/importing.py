# KHROXY managed module
"""Independent bounded import normalization. Never fetch embedded URLs."""
import copy
import hashlib
import json
from urllib.parse import parse_qs, unquote, urlsplit

PROTOCOLS = {'vless','vmess','trojan','shadowsocks','http','socks','hysteria2'}
LIMIT = 1024 * 1024


def clean(value):
    if not isinstance(value,str) or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError('Invalid control character in upstream configuration')
    return value


def normalize(outbound, allow_plaintext=False):
    node = copy.deepcopy(outbound)
    if not isinstance(node,dict) or node.get('protocol') not in PROTOCOLS:
        raise ValueError('Unsupported upstream protocol')
    if set(node)-{'protocol','settings','streamSettings','mux','tag'}:
        raise ValueError('Unsupported outbound fields; configuration retained unchanged')
    node.pop('tag',None)
    settings = node.get('settings',{})
    stream = node.get('streamSettings',{})
    if not isinstance(settings,dict) or not isinstance(stream,dict):
        raise ValueError('Invalid upstream settings')
    if any(key in stream for key in ('sockopt','finalmask','finalmaskSettings')):
        raise ValueError('Unsupported network/security override')
    if stream.get('security','none') not in ('none','tls','reality'):
        raise ValueError('Unsupported security mode')
    tls = stream.get('tlsSettings',{})
    if not isinstance(tls,dict) or tls.get('allowInsecure') not in (None,False):
        raise ValueError('TLS verification bypass refused')
    if stream.get('security')=='reality' and not stream.get('realitySettings',{}).get('password'):
        raise ValueError('REALITY public key missing')
    if node['protocol'] not in ('shadowsocks',) and stream.get('security','none')=='none' and not allow_plaintext:
        raise ValueError('Plaintext transport refused. Explicit opt-in required')
    servers = settings.get('vnext' if node['protocol'] in ('vless','vmess') else 'servers')
    if not isinstance(servers,list) or len(servers)!=1 or not isinstance(servers[0],dict):
        raise ValueError('Exactly one upstream destination is required')
    server = servers[0]
    server['address'] = clean(server.get('address')).lower()
    if not server['address'] or type(server.get('port')) is not int or not 1<=server['port']<=65535:
        raise ValueError('Invalid upstream destination')
    if node['protocol']=='shadowsocks' and (not server.get('method') or not server.get('password')):
        raise ValueError('Shadowsocks authentication missing')
    if node['protocol'] in ('trojan','hysteria2') and not server.get('password'):
        raise ValueError('Upstream authentication missing')
    if node['protocol'] in ('vless','vmess') and (not server.get('users') or not all(isinstance(u,dict) and u.get('id') for u in server['users'])):
        raise ValueError('Upstream identity missing')
    json.dumps(node,allow_nan=False)
    return node


def fingerprint(node):
    node = copy.deepcopy(node); node.pop('tag',None)
    for server in node.get('settings',{}).get('servers',node.get('settings',{}).get('vnext',[])):
        if isinstance(server.get('address'),str): server['address']=server['address'].lower()
    return hashlib.sha256(json.dumps(node,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def share(link, allow_plaintext=False):
    if len(link)>16000 or any(ord(c)<32 for c in link): raise ValueError('Invalid share link')
    parts = urlsplit(link)
    kind = parts.scheme.lower()
    if kind not in ('http','https','socks5','socks','hy2','hysteria2'):
        raise ValueError('Unsupported upstream link')
    host = clean(parts.hostname)
    try:
        chosen_port = parts.port
    except ValueError:
        raise ValueError('Invalid upstream port') from None
    port = chosen_port if chosen_port is not None else {'http':80,'https':443,'socks5':1080,'socks':1080,'hy2':443,'hysteria2':443}[kind]
    try:
        q = parse_qs(parts.query,keep_blank_values=True,strict_parsing=True)
    except ValueError:
        raise ValueError('Unsupported share-link parameters: malformed upstream query') from None
    if any(len(v)!=1 for v in q.values()): raise ValueError('Repeated upstream parameters')
    allowed = {'sni','alpn'} | ({'insecure','obfs','obfs-password'} if kind in ('hy2','hysteria2') else set())
    if set(q)-allowed: raise ValueError('Unsupported share-link parameters: upstream options')
    q = {k:v[0] for k,v in q.items()}
    if q.get('insecure','0').lower() not in ('0','false',''): raise ValueError('TLS verification bypass refused')
    protocol = 'hysteria2' if kind in ('hy2','hysteria2') else 'socks' if kind in ('socks5','socks') else 'http'
    server = {'address':host,'port':port}
    tls = kind in ('https','hy2','hysteria2')
    node = {'protocol':protocol,'settings':{'servers':[server]},'streamSettings':{'network':'tcp','security':'tls' if tls else 'none'}}
    if tls:
        node['streamSettings']['tlsSettings']={'serverName':clean(q.get('sni',host))}
        if q.get('alpn'): node['streamSettings']['tlsSettings']['alpn']=[clean(v) for v in q['alpn'].split(',')]
    if protocol=='hysteria2':
        password = unquote(parts.username or '')
        if parts.password is not None: password += ':'+unquote(parts.password)
        server['password']=clean(password)
        if q.get('obfs'):
            if q['obfs']!='salamander' or not q.get('obfs-password'): raise ValueError('Unsupported or incomplete Hysteria2 obfuscation')
            node['settings']['obfs']={'type':'salamander','password':clean(q['obfs-password'])}
        elif 'obfs-password' in q: raise ValueError('Hysteria2 obfuscation type missing')
    elif parts.username is not None:
        if not parts.username or parts.password is None: raise ValueError('Incomplete upstream authentication')
        server['users']=[{'user':clean(unquote(parts.username)),'pass':clean(unquote(parts.password))}]
    return normalize(node,allow_plaintext)


def json_nodes(text, allow_plaintext=False):
    try:
        document=json.loads(text)
        if isinstance(document,dict) and 'servers' in document and 'protocol' not in document:
            if set(document)-{'version','servers','bytes_used','bytes_remaining'}: raise ValueError('Unsupported SIP008 fields')
            nodes=[]
            for server in document['servers']:
                if set(server)-{'id','remarks','server','server_port','password','method','plugin','plugin_opts'}:
                    raise ValueError('Unsupported SIP008 server options')
                if server.get('plugin') or server.get('plugin_opts'):
                    raise ValueError('Unsupported SIP008 plugin')
                nodes.append({'protocol':'shadowsocks','settings':{'servers':[{'address':server['server'],'port':server['server_port'],'method':server['method'],'password':server['password']}]}})
        elif isinstance(document,dict) and 'outbounds' in document:
            nodes=document['outbounds']
        elif isinstance(document,dict) and 'protocol' in document:
            nodes=[document]
        else: raise ValueError('Unsupported JSON import format')
        if not isinstance(nodes,list) or not 1<=len(nodes)<=100: raise ValueError('Import must contain 1..100 profiles')
        return [normalize(node,allow_plaintext) for node in nodes]
    except (ValueError,KeyError,TypeError,AttributeError):
        raise ValueError('Invalid or unsupported JSON upstream configuration; no changes made') from None


def merge(state, nodes, source='local'):
    """Deduplicate endpoints while retaining all source-to-profile associations."""
    new=copy.deepcopy(state)
    known={fingerprint(out):name for name,out in new['profiles'].items()}
    owners=new.setdefault('import_sources',{})
    names=[]; added=0
    for node in nodes:
        digest=fingerprint(node)
        name=known.get(digest)
        if name is None:
            name='node-'+digest[:20]
            if name in new['profiles']: raise ValueError('Profile fingerprint collision; no changes made')
            new['profiles'][name]=node; known[digest]=name; added+=1
        names.append(name)
    owners[source]=sorted(set(owners.get(source,[]))|set(names))
    new['manual_profiles']=sorted(set(new.get('manual_profiles',[]))|set(names))
    return new, {'added':added,'reused':len(nodes)-added,'source_profiles':len(set(names))}
