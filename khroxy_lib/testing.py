# KHROXY managed module
"""Bounded, cancellable probes independent of the active application runtime."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import errno
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
from . import engines

STAGES=('icmp','tcp','http','download','upload')


class Cancelled(Exception): pass
class BudgetExhausted(Exception): pass


class Guard:
    def __init__(self, timeout, cancel, byte_limit):
        self.deadline=time.monotonic()+timeout;self.cancel=cancel;self.remaining_bytes=byte_limit
        self.done=threading.Event();self.lock=threading.RLock();self.closers=[];self.children=[]
        self.watcher=threading.Thread(target=self.watch,daemon=True);self.watcher.start()

    def check(self):
        if self.cancel.is_set(): raise Cancelled()
        left=self.deadline-time.monotonic()
        if left<=0: raise TimeoutError()
        return left

    def watch(self):
        while not self.done.wait(.02):
            if self.cancel.is_set() or time.monotonic()>=self.deadline:
                self.abort();return

    def resource(self, close):
        with self.lock:
            self.check(); self.closers.append(close)

    def abort(self):
        with self.lock:
            for close in self.closers:
                try: close()
                except (OSError,ValueError): pass
            for child in self.children:
                if child.poll() is None:
                    try: child.terminate()
                    except ProcessLookupError: pass

    def spawn(self, command, capture=False):
        with self.lock:
            self.check()
            child=subprocess.Popen(command,stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,start_new_session=True)
            self.children.append(child)
            return child

    def wait(self, child, capture=False):
        while True:
            self.check()
            try:
                if capture:
                    output,_=child.communicate(timeout=min(.05,self.check()));return child.returncode,output
                return child.wait(timeout=min(.05,self.check())),b''
            except subprocess.TimeoutExpired: pass

    def close(self):
        self.done.set();self.abort();self.watcher.join(timeout=.2)
        for child in self.children:
            try: child.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                child.kill();child.wait(timeout=1)


def category(error, cancel=False, expired=False):
    if cancel or isinstance(error,Cancelled): return 'cancelled'
    if expired or isinstance(error,(TimeoutError,socket.timeout)): return 'timeout'
    if isinstance(error,ssl.SSLError): return 'tls'
    if isinstance(error,socket.gaierror): return 'dns'
    if isinstance(error,ConnectionRefusedError): return 'connection-refused'
    if isinstance(error,BudgetExhausted): return 'byte-budget-exhausted'
    if isinstance(error,http.client.HTTPException): return 'http-protocol'
    if isinstance(error,OSError): return 'network'
    return 'engine-or-configuration'


def endpoint(node):
    settings=node.get('settings',{})
    servers=settings.get('vnext',settings.get('servers',[]))
    if len(servers)!=1: raise ValueError('Upstream endpoint unavailable')
    return servers[0]['address'],int(servers[0]['port'])


def resolve(host,port,guard):
    try:
        address=ipaddress.ip_address(host)
        return [(socket.AF_INET6 if address.version==6 else socket.AF_INET, str(address))]
    except ValueError: pass
    code="import json,socket,sys; print(json.dumps(list(dict.fromkeys((x[0],x[4][0]) for x in socket.getaddrinfo(sys.argv[1],int(sys.argv[2]),type=socket.SOCK_STREAM)))[:16]))"
    child=guard.spawn([sys.executable,'-c',code,host,str(port)],capture=True)
    rc,raw=guard.wait(child,capture=True)
    if rc: raise socket.gaierror('DNS resolution failed')
    return json.loads(raw)


def close_socket(sock):
    try: sock.shutdown(socket.SHUT_RDWR)
    except OSError: pass
    sock.close()


def tcp_probe(node,guard):
    host,port=endpoint(node);addresses=resolve(host,port,guard)
    last=socket.gaierror('DNS resolution failed')
    for family,address in addresses:
        guard.check(); sock=socket.socket(family,socket.SOCK_STREAM)
        guard.resource(lambda s=sock:close_socket(s));sock.settimeout(guard.check())
        try:
            started=time.monotonic();sock.connect((address,port))
            return {'ok':True,'ms':round((time.monotonic()-started)*1000,3)}
        except OSError as error: last=error
        finally: sock.close()
    raise last


def icmp_probe(node,guard):
    ping=shutil.which('ping')
    if not ping: return {'ok':False,'category':'unsupported'}
    host,port=endpoint(node);address=resolve(host,port,guard)[0][1]
    started=time.monotonic()
    child=guard.spawn([ping,'-n','-c','1','-W',str(max(1,math.ceil(guard.check()))),'--',address])
    rc,_=guard.wait(child)
    return {'ok':rc==0,'category':'ok' if rc==0 else 'icmp-failed','ms':round((time.monotonic()-started)*1000,3)}


class Upload:
    def __init__(self,count,guard): self.left=count;self.guard=guard;self.sent=0
    def read(self,size):
        self.guard.check();count=min(size,self.left,16384)
        self.left-=count;self.sent+=count;self.guard.remaining_bytes-=count
        return b'\0'*count


def http_probe(stage,url,proxy,guard,ca_file=None):
    destination=urlsplit(url)
    if destination.scheme not in ('http','https') or not destination.hostname or destination.username or destination.password or destination.fragment:
        raise ValueError('Invalid test endpoint')
    port=destination.port or (443 if destination.scheme=='https' else 80)
    path=(destination.path or '/')+('?' + destination.query if destination.query else '')
    proxy_port=urlsplit(proxy).port
    if destination.scheme=='https':
        conn=http.client.HTTPSConnection('127.0.0.1',proxy_port,timeout=guard.check(),context=ssl.create_default_context(cafile=ca_file))
        conn.set_tunnel(destination.hostname,port)
    else:
        conn=http.client.HTTPConnection('127.0.0.1',proxy_port,timeout=guard.check());path=url
    def close():
        active=conn.sock
        if active is not None:
            try: active.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        conn.close()
    guard.resource(close);response=None
    try:
        guard.check();started=time.monotonic();body=None;headers={};upload=None
        conn.connect()
        active_socket=conn.sock
        guard.resource(lambda:close_socket(active_socket))
        if stage=='upload':
            if guard.remaining_bytes<=0: raise BudgetExhausted()
            upload=Upload(guard.remaining_bytes,guard)
            headers['Content-Length']=str(upload.left);body=upload
        conn.request('POST' if upload else 'GET',path,body=body,headers=headers)
        response=conn.getresponse();guard.check()
        header_ms=round((time.monotonic()-started)*1000,3)
        if not 200<=response.status<300:
            return {'ok':False,'category':'http-status','status':response.status,'ms':header_ms,'bytes_sent':upload.sent if upload else 0,'bytes_received':0}
        received=0
        while guard.remaining_bytes>0:
            guard.check();chunk=response.read(min(16384,guard.remaining_bytes))
            if not chunk: break
            received+=len(chunk);guard.remaining_bytes-=len(chunk)
        guard.check();elapsed=max(time.monotonic()-started,.000001)
        return {'ok':True,'category':'ok','status':response.status,'ms':header_ms,
                'bytes_sent':upload.sent if upload else 0,'bytes_received':received,
                'limited':guard.remaining_bytes==0,'bytes_per_second':round((received+(upload.sent if upload else 0))/elapsed,3)}
    finally:
        if response is not None: response.close()
        close()


@contextmanager
def proxy(app,node,state,guard):
    with socket.socket() as sock: sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base={'log':{'loglevel':'warning'},'inbounds':[{'tag':'probe','listen':'127.0.0.1','port':port,'protocol':'http','settings':{}}],
          'outbounds':[dict(node,tag='probe-out')],'routing':{'rules':[{'type':'field','inboundTag':['probe'],'outboundTag':'probe-out'}]}}
    usable=None
    for name in dict.fromkeys((state.get('engine','xray'),*engines.NAMES)):
        binary=engines.discover(name,state,app.XRAY)
        if not binary.is_file(): continue
        try: config=engines.adapt(base,name)
        except ValueError: continue
        usable=name,binary,config;break
    if usable is None: raise ValueError('No compatible test engine')
    name,binary,config=usable
    with binary.open('rb') as stream:
        if stream.read(4)!=b'\x7fELF': raise ValueError('Invalid engine binary')
    fd,path=tempfile.mkstemp(prefix='.probe-',suffix='.json',dir=app.ROOT)
    try:
        with os.fdopen(fd,'w') as stream: json.dump(config,stream,allow_nan=False)
        check=guard.spawn(engines.command(binary,name,path,check=True));rc,_=guard.wait(check)
        if rc: raise ValueError('Engine configuration failed')
        child=guard.spawn(engines.command(binary,name,path))
        while True:
            guard.check()
            if child.poll() is not None: raise ValueError('Engine startup failed')
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=min(.1,guard.check())): break
            except OSError: guard.cancel.wait(.02)
        yield f'http://127.0.0.1:{port}'
    finally: Path(path).unlink(missing_ok=True)


class Runner:
    def __init__(self,app,state,stages=('http',),concurrency=4,timeout=10,retries=0,byte_limit=262144,url=None,ca_file=None,cancel=None):
        if (not stages or set(stages)-set(STAGES) or not 1<=concurrency<=32 or not .1<=timeout<=120 or
            not 0<=retries<=3 or not 1<=byte_limit<=8*1024*1024): raise ValueError('Invalid bounded testing options')
        if any(s in ('http','download','upload') for s in stages) and not url: raise ValueError('Explicit HTTP test endpoint required')
        self.app=app;self.state=state;self.stages=tuple(dict.fromkeys(stages));self.concurrency=concurrency
        self.timeout=timeout;self.retries=retries;self.byte_limit=byte_limit;self.url=url;self.ca_file=ca_file;self.cancel=cancel or threading.Event()
        self.seen_pids=[];self.lock=threading.Lock()

    def one(self,item):
        name,node=item
        if not self.app.NAME.fullmatch(name): raise ValueError('Invalid profile name')
        if self.cancel.is_set(): return {'profile':name,'ok':None,'category':'cancelled','stages':{},'timestamp':time.time()}
        guard=Guard(self.timeout,self.cancel,self.byte_limit);results={}
        try:
            for stage in self.stages:
                if stage in ('http','download','upload'): continue
                for attempt in range(self.retries+1):
                    try:
                        result=tcp_probe(node,guard) if stage=='tcp' else icmp_probe(node,guard)
                    except (OSError,ValueError,Cancelled) as error:
                        result={'ok':False,'category':category(error,self.cancel.is_set(),time.monotonic()>=guard.deadline)}
                    result['attempts']=attempt+1
                    if result['ok'] or self.cancel.is_set(): break
                results[stage]=result
            http_stages=[stage for stage in self.stages if stage in ('http','download','upload')]
            if http_stages:
                with proxy(self.app,node,self.state,guard) as url:
                    for stage in http_stages:
                        for attempt in range(self.retries+1):
                            try: result=http_probe(stage,self.url,url,guard,self.ca_file)
                            except (OSError,ValueError,http.client.HTTPException,Cancelled,BudgetExhausted) as error:
                                result={'ok':False,'category':category(error,self.cancel.is_set(),time.monotonic()>=guard.deadline)}
                            result['attempts']=attempt+1
                            if result['ok'] or self.cancel.is_set(): break
                        results[stage]=result
        except (OSError,ValueError,Cancelled) as error:
            for stage in self.stages:
                results.setdefault(stage,{'ok':False,'category':category(error,self.cancel.is_set(),time.monotonic()>=guard.deadline),'attempts':0})
        finally:
            with self.lock: self.seen_pids.extend(child.pid for child in guard.children)
            guard.close()
        availability=[value for key,value in results.items() if key in ('http','download','upload')]
        return {'profile':name,'ok':any(v['ok'] for v in availability) if availability else None,
                'category':'cancelled' if self.cancel.is_set() else 'complete','stages':results,
                'bytes_budget':self.byte_limit,'bytes_used':self.byte_limit-guard.remaining_bytes,'timestamp':time.time()}

    def run(self,profiles):
        if len(profiles)>1000: raise ValueError('At most 1000 profiles per test run')
        with ThreadPoolExecutor(max_workers=self.concurrency,thread_name_prefix='khroxy-probe') as pool:
            return list(pool.map(self.one,sorted(profiles.items())))


def cli(app,argv):
    import argparse
    parser=argparse.ArgumentParser(prog='khroxy test-profiles')
    parser.add_argument('names',nargs='*');parser.add_argument('--stages',default='http')
    parser.add_argument('--concurrency',type=int,default=4);parser.add_argument('--timeout',type=float,default=10)
    parser.add_argument('--retries',type=int,default=0);parser.add_argument('--max-bytes',type=int,default=262144)
    parser.add_argument('--url');parser.add_argument('--ca-file')
    args=parser.parse_args(argv);state=app.load();profiles={}
    from .storage import Store
    if not Store(app.STATE).exists:
        raise ValueError('Run khroxy storage migrate before persistent testing; stop older KHROXY writers first')
    for name in args.names or sorted(state['profiles']):
        app.check_name(name)
        if name not in state['profiles']: raise ValueError('Unknown profile')
        if state.get('profile_options',{}).get(name,{}).get('enabled',True): profiles[name]=state['profiles'][name]
    cancel=threading.Event();previous=signal.signal(signal.SIGINT,lambda *args:cancel.set())
    try:
        runner=Runner(app,state,args.stages.split(','),args.concurrency,args.timeout,args.retries,args.max_bytes,args.url,args.ca_file,cancel)
        results=runner.run(profiles)
        from .diagnostics import record
        record(app,state,results)
        print(json.dumps(results))
        if cancel.is_set(): raise SystemExit(130)
    finally: signal.signal(signal.SIGINT,previous)
