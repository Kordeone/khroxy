# KHROXY managed module
"""Bounded TCP reachability for explicit literal-IP candidate lists only."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import errno
import fcntl
import os
import hashlib
import ipaddress
import json
import math
import selectors
import socket
import threading
import time
from .storage import Store, encode
from .testing import Cancelled, category


def targets(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 1000:
        raise ValueError('Provide 1..1000 explicit IP/port objects; ranges and names are unsupported')
    result=[];seen=set()
    for item in values:
        if not isinstance(item, dict) or set(item) != {'ip','port'} or not isinstance(item['ip'],str):
            raise ValueError('Candidate requires only literal ip and port fields')
        ip=ipaddress.ip_address(item['ip'])
        if ip.is_multicast or ip.is_unspecified or str(ip)=='255.255.255.255' or '%' in item['ip']:
            raise ValueError('Multicast, unspecified, scoped and broadcast addresses are refused')
        if type(item['port']) is not int or not 1 <= item['port'] <= 65535:
            raise ValueError('Invalid candidate TCP port')
        key=(str(ip),item['port'])
        if key not in seen:result.append({'ip':key[0],'port':key[1]});seen.add(key)
    return result


def connect(item, timeout, cancel):
    start=time.monotonic()
    with socket.socket(socket.AF_INET6 if ':' in item['ip'] else socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.setblocking(False)
        code=sock.connect_ex((item['ip'],item['port']))
        if code not in (0,errno.EINPROGRESS,errno.EWOULDBLOCK,errno.EALREADY):
            raise OSError(code,'Candidate connection failed')
        with selectors.DefaultSelector() as selector:
            selector.register(sock,selectors.EVENT_WRITE)
            while True:
                if cancel.is_set():raise Cancelled()
                left=start+timeout-time.monotonic()
                if left<=0:raise TimeoutError()
                if selector.select(min(.05,left)):
                    code=sock.getsockopt(socket.SOL_SOCKET,socket.SO_ERROR)
                    if code:raise OSError(code,'Candidate connection failed')
                    return round((time.monotonic()-start)*1000,3)


class Scanner:
    def __init__(self, candidates, concurrency=4, rate=5, timeout=2, cancel=None):
        self.candidates=targets(candidates)
        if type(concurrency) is not int or not 1<=concurrency<=32 or not isinstance(rate,(int,float)) or not math.isfinite(rate) or not .1<=rate<=20 or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or not .1<=timeout<=10:
            raise ValueError('Limits: workers 1..32, rate 0.1..20 connections/s, timeout 0.1..10 s')
        self.concurrency=concurrency;self.rate=rate;self.timeout=timeout;self.cancel=cancel or threading.Event()
        self.lock=threading.Lock();self.next=0
    def probe(self,item):
        with self.lock:
            now=time.monotonic();when=max(now,self.next);self.next=when+1/self.rate
        if self.cancel.wait(max(0,when-time.monotonic())):return None
        result={'kind':'edge-scan-result',**item,'timestamp':time.time(),'ok':False}
        try:result.update(ok=True,ms=connect(item,self.timeout,self.cancel),category='ok')
        except (OSError,Cancelled) as error:result['category']=category(error,self.cancel.is_set())
        return result if not self.cancel.is_set() else None
    def run(self, done=None):
        # At most 1000 queued jobs; cancellation wakes every rate wait/socket loop.
        pool=ThreadPoolExecutor(max_workers=self.concurrency)
        futures=[pool.submit(self.probe,item) for item in self.candidates]
        try:
            from concurrent.futures import as_completed
            for future in as_completed(futures):
                value=future.result()
                if value is not None:
                    if done:done(value)
                    yield value
        except BaseException:
            self.cancel.set()
            raise
        finally:
            if self.cancel.is_set():
                for future in futures:future.cancel()
            pool.shutdown(wait=True,cancel_futures=True)


def records(store,name):
    with store.connection() as db:
        rows=db.execute("SELECT data FROM metadata WHERE json_extract(data,'$.scan')=? AND json_extract(data,'$.kind') IN ('edge-scan-manifest','edge-scan-result') ORDER BY id",(name,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def resume(store,name,candidates,reuse=False):
    candidates=targets(candidates)
    fingerprint=hashlib.sha256(encode(candidates).encode()).hexdigest()
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        rows=[json.loads(r[0]) for r in db.execute("SELECT data FROM metadata WHERE json_extract(data,'$.scan')=? AND json_extract(data,'$.kind') IN ('edge-scan-manifest','edge-scan-result') ORDER BY id",(name,))]
        manifests=[r for r in rows if r['kind']=='edge-scan-manifest']
        if manifests:
            if not reuse:raise ValueError('Scan name exists; use --resume or a new name')
            if manifests[0]['fingerprint']!=fingerprint:raise ValueError('Resume requires the identical normalized candidate list')
        else:
            manifest={'kind':'edge-scan-manifest','scan':name,'fingerprint':fingerprint,'count':len(candidates)}
            db.execute('INSERT INTO metadata(profile,timestamp,data) VALUES(NULL,?,?)',(time.time(),encode(manifest)))
    completed={(r['ip'],r['port']) for r in rows if r['kind']=='edge-scan-result'}
    return [item for item in candidates if (item['ip'],item['port']) not in completed]


def ranked(store,name):
    latest={}
    for item in records(store,name):
        if item['kind']=='edge-scan-result':latest[(item['ip'],item['port'])]=item
    return sorted(latest.values(),key=lambda r:(not r['ok'],r.get('ms',float('inf')),r['ip'],r['port']))


def cli(app,argv):
    parser=argparse.ArgumentParser(prog='khroxy scan');sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('run');p.add_argument('name');p.add_argument('--file',required=True);p.add_argument('--resume',action='store_true')
    p.add_argument('--concurrency',type=int,default=4);p.add_argument('--rate',type=float,default=5);p.add_argument('--timeout',type=float,default=2)
    p=sub.add_parser('results');p.add_argument('name');p.add_argument('--limit',type=int,default=100);p.add_argument('--offset',type=int,default=0)
    args=parser.parse_args(argv);app.check_name(args.name);store=Store(app.STATE)
    if args.action=='results':
        if not 1<=args.limit<=1000 or args.offset<0:raise ValueError('Invalid pagination')
        items=ranked(store,args.name);print(json.dumps({'total':len(items),'results':items[args.offset:args.offset+args.limit]}));return
    path=app.Path(args.file)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:raise ValueError('Unsafe or oversized candidate file')
    with path.open('rb') as stream:raw=stream.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('Candidate file too large')
    lock=os.open(store.root/('.scan-'+args.name+'.lock'),os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('This scan is already running') from None
        candidates=targets(json.loads(raw));cancel=threading.Event()
        # Validate all budgets before writing the manifest; resume state never enables broader scans.
        scanner=Scanner(candidates,args.concurrency,args.rate,args.timeout,cancel)
        pending=resume(store,args.name,candidates,args.resume)
        if not pending:print(json.dumps({'complete':True,'remaining':0}));return
        scanner.candidates=pending
        def save(result):store.append('metadata',None,dict(result,scan=args.name),result['timestamp'])
        try:
            for result in scanner.run(save):print(json.dumps(result),flush=True)
        except KeyboardInterrupt:
            cancel.set();print(json.dumps({'cancelled':True,'resumable':True}));raise SystemExit(130)
    finally:os.close(lock)
