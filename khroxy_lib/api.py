# KHROXY managed module
"""Opt-in authenticated loopback management; no raw configurations or exports."""
import argparse
import fcntl
import signal
import time
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
from socketserver import ThreadingMixIn
import stat
import threading
from urllib.parse import parse_qs, urlsplit
from . import automation, supervisor
from .storage import exclusive_file


def token_file(path):
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or not 32<=info.st_size<=256:
            raise ValueError('API token file must be private, regular and owned by this user')
        raw=os.read(fd,257).strip()
    finally:os.close(fd)
    if not 32<=len(raw)<=128 or any(c not in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_' for c in raw):
        raise ValueError('Invalid API token file')
    return raw.decode('ascii')


class HeaderBudget:
    def __init__(self,reader):self.reader=reader;self.total=0
    def readline(self,limit=-1):
        line=self.reader.readline(min(limit,16385) if limit>=0 else 16385);self.total+=len(line)
        if self.total>16384:raise http.client.LineTooLong('request headers')
        return line
    def __getattr__(self,name):return getattr(self.reader,name)


class Handler(BaseHTTPRequestHandler):
    server_version='KHROXY';sys_version=''
    def setup(self):
        self.request.settimeout(3);super().setup();self.rfile=HeaderBudget(self.rfile)
    def log_message(self,*args):pass
    def send(self,code,value):
        raw=json.dumps(value,allow_nan=False,separators=(',',':')).encode()
        self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Connection','close')
        self.end_headers();self.wfile.write(raw);self.close_connection=True
    def authorized(self):
        if self.headers.get('Origin') is not None:
            self.send(403,{'error':'browser-origin-denied'});return False
        host=self.headers.get('Host','')
        if host not in self.server.hosts:
            self.send(400,{'error':'invalid-host'});return False
        value=self.headers.get('Authorization','')
        if len(self.headers.get_all('Authorization',[]))!=1 or not hmac.compare_digest(value,'Bearer '+self.server.token):
            self.send(401,{'error':'authentication-required'});return False
        return True
    def handle_api(self,mutate=False):
        if not self.authorized():return
        try:
            if len(self.path)>2048:raise ValueError()
            parts=urlsplit(self.path)
            if parts.scheme or parts.netloc or parts.fragment:raise ValueError()
            query=parse_qs(parts.query,keep_blank_values=True,max_num_fields=8)
            if any(len(v)!=1 for v in query.values()):raise ValueError()
            query={k:v[0] for k,v in query.items()}
            if mutate:
                if query or self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type')!='application/json':raise ValueError()
                length=int(self.headers.get('Content-Length','-1'))
                if not 0<length<=4096:
                    self.send(413,{'error':'bounded-json-body-required'});return
                raw=self.rfile.read(length)
                if len(raw)!=length:raise ValueError()
                body=json.loads(raw)
                if not isinstance(body,dict):raise ValueError()
                if parts.path=='/v1/selection':
                    if set(body)-{'name','target'} or not isinstance(body.get('name'),str) or body.get('target','active') not in ('active','fastest'):raise ValueError()
                    self.server.app.check_name(body['name'])
                    result=automation.selection(self.server.app,body['name'],body.get('target','active'))
                elif parts.path=='/v1/rotation':
                    if body:raise ValueError()
                    result=automation.selection(self.server.app,rotate=True)
                else:self.send(404,{'error':'not-found'});return
            elif parts.path=='/v1/status':
                if query:raise ValueError()
                result=automation.status(self.server.app)
            elif parts.path=='/v1/profiles':
                if set(query)-{'limit','offset','protocol','enabled','search'}:raise ValueError()
                if query.get('enabled') not in (None,'true','false'):raise ValueError()
                result=automation.profiles(self.server.app,int(query.get('limit',100)),int(query.get('offset',0)),
                    query.get('protocol'),None if 'enabled' not in query else query['enabled']=='true',query.get('search',''))
            else:self.send(404,{'error':'not-found'});return
            self.send(200,result)
        except (OSError,ValueError,KeyError,TypeError,TimeoutError,RecursionError):self.send(400,{'error':'invalid-or-unavailable-operation'})
    def do_GET(self):self.handle_api()
    def do_POST(self):self.handle_api(True)


class Server(ThreadingMixIn,HTTPServer):
    daemon_threads=True;request_queue_size=8;allow_reuse_address=False
    def __init__(self,address,app,token):
        if address[0] not in ('127.0.0.1','::1'):raise ValueError('Management API supports loopback addresses only')
        self.address_family=socket.AF_INET6 if address[0]=='::1' else socket.AF_INET
        self.app=app;self.token=token;self.slots=threading.BoundedSemaphore(8)
        super().__init__(address,Handler)
        port=self.server_address[1]
        self.hosts={f'127.0.0.1:{port}',f'localhost:{port}',f'[::1]:{port}'}
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):request.close();return
        try:super().process_request(request,address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()
    def handle_error(self,request,address):pass


def cli(app,argv):
    parser=argparse.ArgumentParser(prog='khroxy api');sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('token');p.add_argument('--output',required=True)
    p=sub.add_parser('serve');p.add_argument('--token-file',required=True);p.add_argument('--listen',default='127.0.0.1')
    p.add_argument('--port',type=int,default=21090)
    args=parser.parse_args(argv)
    if args.action=='token':
        exclusive_file(args.output,(secrets.token_urlsafe(32)+'\n').encode());print(json.dumps({'private_token_written':True}));return
    if args.listen not in ('127.0.0.1','::1') or not 1024<=args.port<=65535:raise ValueError('API requires explicit loopback and unprivileged port')
    token=token_file(args.token_file)
    lock=os.open(app.ROOT/'.api-lock',os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Owned management API is already running') from None
        supervisor.write(app.ROOT,'api-owner.json',supervisor.identity(os.getpid()))
        with Server((args.listen,args.port),app,token) as server:
            print(json.dumps({'api':'ready','listen':args.listen,'port':args.port,'authentication':True}),flush=True)
            try:server.serve_forever(poll_interval=.1)
            except KeyboardInterrupt:pass
    finally:
        record=supervisor.read(app.ROOT,'api-owner.json')
        if record.get('pid')==os.getpid(): (app.ROOT/'api-owner.json').unlink(missing_ok=True)
        os.close(lock)


def stop(root):
    record=supervisor.read(root,'api-owner.json')
    if supervisor.owned(record,False):
        supervisor.safe_signal(record,signal.SIGTERM,token_required=False)
        deadline=time.monotonic()+3
        while supervisor.owned(record,False) and time.monotonic()<deadline:time.sleep(.02)
        if supervisor.owned(record,False):raise ValueError('Owned API did not stop; uninstall refused')
