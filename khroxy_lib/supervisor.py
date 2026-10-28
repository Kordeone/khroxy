# KHROXY managed module
"""Optional rootless supervisor with private IPC and kernel-verified ownership."""
import argparse
from contextlib import suppress
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from . import engines
from .storage import Store

MODE='.supervisor-mode'
SESSION='runtime-session.json'
OWNER='daemon-owner.json'
SOCKET='supervisor.sock'


def write(root,name,value):
    target=Path(root)/name
    if target.is_symlink():raise ValueError('Refusing symlink supervisor record')
    fd,tmp=tempfile.mkstemp(prefix='.supervisor-',dir=root)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream,allow_nan=False);stream.flush();os.fsync(stream.fileno())
        os.replace(tmp,target)
    finally:Path(tmp).unlink(missing_ok=True)


def read(root,name):
    path=Path(root)/name
    if path.is_symlink():raise ValueError('Refusing supervisor record symlink')
    if not path.exists():return {}
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_size>65536:
        raise ValueError('Untrusted supervisor record; no process signalled')
    value=json.loads(path.read_bytes())
    if not isinstance(value,dict):raise ValueError('Invalid supervisor record')
    return value


def enabled(root):return read(root,MODE).get('application')=='khroxy'


def identity(pid):
    if type(pid) is not int or pid<=1:raise ValueError('Invalid owned PID')
    base=Path('/proc')/str(pid)
    raw=(base/'stat').read_text().rsplit(')',1)[1].split()
    if raw[0]=='Z':raise ProcessLookupError('Owned process has exited')
    uid=int(next(line for line in (base/'status').read_text().splitlines() if line.startswith('Uid:')).split()[1])
    inode=(base/'exe').stat()
    return {'pid':pid,'uid':uid,'start_ticks':int(raw[19]),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'exe_device':inode.st_dev,'exe_inode':inode.st_ino,
            'argv':[part.decode() for part in (base/'cmdline').read_bytes().split(b'\0') if part]}


def owned(record,token_required=True):
    try:
        actual=identity(record.get('pid'))
        if actual['uid']!=os.geteuid() or any(record.get(key)!=value for key,value in actual.items()):return False
        if token_required:
            token=record.get('token')
            if not isinstance(token,str) or len(token)<32:return False
            environment=(Path('/proc')/str(actual['pid'])/'environ').read_bytes().split(b'\0')
            expected=b'KHROXY_ENGINE_OWNER='+token.encode()
            if not any(hmac.compare_digest(item,expected) for item in environment):return False
        return True
    except (OSError,ValueError,TypeError,KeyError,StopIteration,UnicodeError):return False


def adopt_intent(record):
    if not record.get('launching') or not record.get('token'):return None
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():continue
        try:
            actual=identity(int(entry.name))
            if actual['uid']!=os.geteuid() or actual['argv']!=record.get('argv'):continue
            candidate=dict(record,**actual)
            if owned(candidate):return dict(candidate,launching=False)
        except (OSError,ValueError,KeyError,StopIteration):continue
    return None


def safe_signal(record,kind,token_required=True):
    if not owned(record,token_required):return False
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
        raise ValueError('Safe pidfd signalling unavailable; manual review required')
    try:fd=os.pidfd_open(record['pid'])
    except ProcessLookupError:return False
    try:
        if not owned(record,token_required):return False
        try:signal.pidfd_send_signal(fd,kind)
        except ProcessLookupError:return False
        return True
    finally:os.close(fd)


def stop_owned(record):
    if not owned(record):return
    safe_signal(record,signal.SIGTERM);deadline=time.monotonic()+2
    while owned(record) and time.monotonic()<deadline:time.sleep(.02)
    if owned(record):safe_signal(record,signal.SIGKILL)
    deadline=time.monotonic()+1
    while owned(record) and time.monotonic()<deadline:time.sleep(.02)
    if owned(record):raise ValueError('Owned engine did not stop within budget')


def rpc(root,op,timeout=5,**values):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(timeout);client.connect(str(Path(root)/SOCKET))
        client.sendall((json.dumps(dict(op=op,**values))+'\n').encode())
        with client.makefile('rb') as stream:raw=stream.readline(8193)
    if len(raw)>8192:raise ValueError('Oversized supervisor response')
    response=json.loads(raw)
    if not response.get('ok'):raise ValueError('Supervisor operation failed: '+response.get('error','unavailable'))
    return response['result']


class Runtime:
    def __init__(self,app):self.app=app;self.root=app.ROOT;self.lock=threading.RLock();self.child=None;self.closing=False
    def event(self,code,record=None):
        self.app.log_event(code,detail='unavailable' if 'fail' in code else 'ready')
        if Store(self.app.STATE).exists:
            Store(self.app.STATE).append('sessions',None,{'event':code,'pid':(record or {}).get('pid'),'engine':(record or {}).get('engine')})
    def record(self):
        value=read(self.root,SESSION)
        if not isinstance(value.get('attempts',[]),list) or any(not isinstance(x,(int,float)) for x in value.get('attempts',[])):
            raise ValueError('Invalid supervisor restart state')
        return value
    def status(self):
        record=self.record()
        return {'runtime':'active' if owned(record) else 'offline','pid':record.get('pid') if owned(record) else None,
                'engine':record.get('engine'),'blocked':bool(record.get('blocked')),'desired':bool(record.get('desired')),
                'restart_attempts':len(record.get('attempts',[]))}
    def recover_intent(self):
        record=self.record()
        adopted=adopt_intent(record)
        if adopted:write(self.root,SESSION,adopted);self.event('runtime_adopted',adopted)
        return adopted or record
    def start(self,recovery=False):
        with self.lock:
            record=self.recover_intent()
            if self.closing:raise ValueError('Supervisor is shutting down')
            if recovery and not record.get('desired'):return self.status()
            if owned(record):return self.status()
            now=time.time();attempts=[stamp for stamp in record.get('attempts',[]) if 0<=now-stamp<60]
            if recovery and (record.get('blocked') or len(attempts)>=3):
                record.update(blocked=True,desired=True);write(self.root,SESSION,record);return self.status()
            if not recovery:attempts=[]
            attempts.append(now)
            record.update(attempts=attempts,desired=True,blocked=False)
            write(self.root,SESSION,record)
            try:
                state=self.app.load();name,binary=self.app.selected_engine(state)
                config=self.app.build_runtime(state) if recovery or not self.app.RUNTIME.exists() else json.loads(self.app.RUNTIME.read_text())
                if not config.get('inbounds'):raise ValueError('No configured proxy listeners')
                engines.validate(binary,name,config,self.root)
                directory=self.root/'sessions'
                if directory.is_symlink():raise ValueError('Refusing session directory symlink')
                directory.mkdir(mode=0o700,exist_ok=True);directory.chmod(0o700)
                token=secrets.token_hex(32);filename='session-'+secrets.token_hex(16)+'.json'
                fd=os.open(directory/filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'w') as stream:json.dump(config,stream,allow_nan=False)
                argv=engines.command(binary,name,directory/filename)
                record={'desired':True,'blocked':False,'launching':True,'token':token,'engine':name,'argv':argv,
                        'attempts':attempts,'config_name':filename,'config_hash':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()}
                write(self.root,SESSION,record)
                self.child=subprocess.Popen(argv,env=dict(os.environ,KHROXY_ENGINE_OWNER=token),stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
                record.update(identity(self.child.pid),launching=False);write(self.root,SESSION,record)
                deadline=time.monotonic()+4
                ports=[(entry.get('listen','127.0.0.1'),entry.get('port',entry.get('listen_port'))) for entry in config['inbounds'] if entry.get('streamSettings',{}).get('network')!='domainsocket']
                while True:
                    if self.child.poll() is not None:raise ValueError('Engine exited before readiness')
                    ready=True
                    for host,port in ports:
                        try:
                            with socket.create_connection(('127.0.0.1' if host=='0.0.0.0' else host,port),timeout=.1):pass
                        except OSError:ready=False;break
                    if ready:break
                    if time.monotonic()>deadline:raise ValueError('Engine listener readiness timed out')
                    time.sleep(.03)
                self.app.write_file(self.app.RUNTIME,self.app.serial(config))
                self.event('runtime_started',record);return self.status()
            except (OSError,ValueError,subprocess.SubprocessError):
                if owned(record):stop_owned(record)
                if self.child is not None:
                    with suppress(subprocess.TimeoutExpired):self.child.wait(timeout=1)
                record.update(desired=True,blocked=len(attempts)>=3,launching=False)
                write(self.root,SESSION,record);self.event('runtime_fail',record)
                raise ValueError('Engine startup failed; owned runtime offline, retry budget retained') from None
    def stop(self):
        with self.lock:
            record=self.recover_intent();stop_owned(record)
            record.update(desired=False,blocked=False);write(self.root,SESSION,record)
            if self.child is not None:
                with suppress(subprocess.TimeoutExpired):self.child.wait(timeout=1)
            self.event('runtime_stopped',record);return self.status()


def peer_allowed(connection,uid=None):
    if not hasattr(socket,'SO_PEERCRED'):return False
    _,peer_uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
    return peer_uid==(os.geteuid() if uid is None else uid)


class Server(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True;request_queue_size=8
    def __init__(self,path,runtime):
        self.runtime=runtime;self.slots=threading.BoundedSemaphore(8);self.keep_runtime=False
        super().__init__(path,Handler)
    def verify_request(self,request,address):return peer_allowed(request)
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):request.close();return
        try:super().process_request(request,address)
        except BaseException:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()
    def handle_error(self,*args):self.runtime.app.log_event('ipc_fail',detail='unavailable')


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3)
        try:
            raw=self.rfile.readline(8193)
            if len(raw)>8192:raise ValueError('oversized-request')
            value=json.loads(raw)
            if not isinstance(value,dict) or set(value)-{'op','keep_runtime'}:raise ValueError('invalid-request')
            runtime=self.server.runtime;op=value.get('op')
            if op=='status':result=runtime.status()
            elif op=='start':result=runtime.start()
            elif op=='stop':result=runtime.stop()
            elif op=='restart':runtime.stop();result=runtime.start()
            elif op=='shutdown':
                self.server.keep_runtime=value.get('keep_runtime') is True
                with runtime.lock:
                    runtime.closing=True
                    result=runtime.status() if self.server.keep_runtime else runtime.stop()
                threading.Thread(target=self.server.shutdown,daemon=True).start()
            else:raise ValueError('unsupported-operation')
            response={'ok':True,'result':result}
        except (OSError,ValueError,TypeError,subprocess.SubprocessError):response={'ok':False,'error':'invalid-or-unavailable-operation'}
        self.wfile.write((json.dumps(response)+'\n').encode())


def run(app):
    if app.ROOT.is_symlink():raise ValueError('Refusing supervisor configuration symlink')
    if not enabled(app.ROOT) and app.running():raise ValueError('Stop legacy managed runtime before starting the supervisor')
    app.ROOT.mkdir(mode=0o700,parents=True,exist_ok=True);app.ROOT.chmod(0o700)
    lock=os.open(app.ROOT/'.daemon-lock',os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        path=app.ROOT/SOCKET
        if path.exists():
            if not stat.S_ISSOCK(path.lstat().st_mode) or path.lstat().st_uid!=os.geteuid():raise ValueError('Untrusted IPC path retained')
            try:rpc(app.ROOT,'status');return
            except (ConnectionRefusedError,FileNotFoundError):path.unlink()
        write(app.ROOT,MODE,{'application':'khroxy','version':1})
        write(app.ROOT,OWNER,identity(os.getpid()))
        runtime=Runtime(app);runtime.recover_intent();stop=threading.Event()
        with Server(str(path),runtime) as server:
            path.chmod(0o600)
            def maintenance():
                while not stop.wait(.2):
                    try:
                        if runtime.child is not None:runtime.child.poll()
                        record=runtime.record()
                        if record.get('desired') and not owned(record) and not record.get('blocked'):
                            runtime.start(recovery=True)
                    except (OSError,ValueError,subprocess.SubprocessError):pass
            worker=threading.Thread(target=maintenance,daemon=True);worker.start()
            def shutdown(*args):
                runtime.closing=True
                threading.Thread(target=server.shutdown,daemon=True).start()
            signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
            try:server.serve_forever(poll_interval=.1)
            finally:
                runtime.closing=True
                stop.set();worker.join(timeout=.5)
                if not server.keep_runtime:runtime.stop()
                path.unlink(missing_ok=True)
                (app.ROOT/OWNER).unlink(missing_ok=True)
    finally:os.close(lock)


def control(app,args,timeout=15):
    operation=args[0]
    if operation=='is-active':
        active=owned(read(app.ROOT,SESSION));return subprocess.CompletedProcess(['supervisor',*args],0 if active else 3,'','')
    op='restart' if operation=='restart' else 'start' if operation in ('enable','start') else 'stop' if operation in ('disable','stop') else None
    if op is None:return subprocess.CompletedProcess(['supervisor',*args],1,'','Unsupported supervised operation')
    try:rpc(app.ROOT,op,timeout=timeout);return subprocess.CompletedProcess(['supervisor',*args],0,'','')
    except (OSError,ValueError):return subprocess.CompletedProcess(['supervisor',*args],1,'','Supervisor operation unavailable; no foreign process signalled')


def cli(app,argv):
    parser=argparse.ArgumentParser(prog='khroxy daemon');sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('run');sub.add_parser('start');sub.add_parser('status')
    p=sub.add_parser('stop');p.add_argument('--keep-runtime',action='store_true')
    sub.add_parser('proxy-start');sub.add_parser('proxy-stop');sub.add_parser('disable')
    p=sub.add_parser('service');p.add_argument('--write',action='store_true')
    args=parser.parse_args(argv)
    if args.action=='run':run(app);return
    if args.action=='service':
        path=Path.home()/'.config/systemd/user/khroxy-supervisor.service'
        text=('# Managed by khroxy\n[Unit]\nDescription=KHROXY owned runtime supervisor\n\n[Service]\nType=simple\n'
              +'ExecStart='+app.systemd_word(Path(app.__file__).resolve())+' daemon run\nRestart=on-failure\nRestartSec=3\nUMask=0077\nNoNewPrivileges=true\n\n[Install]\nWantedBy=default.target\n')
        if not args.write:print(text);return
        if path.is_symlink() or (path.exists() and not path.read_text().startswith('# Managed by khroxy\n')):raise ValueError('Foreign supervisor unit retained')
        app.write_file(path,text.encode(),0o644);print(json.dumps({'unit_written':True,'started':False}));return
    if args.action=='status':
        try:result=rpc(app.ROOT,'status',timeout=2);result['daemon']=True
        except (OSError,ValueError):result=Runtime(app).status();result['daemon']=False
        print(json.dumps(result));return
    if args.action=='start':
        try:rpc(app.ROOT,'status',timeout=1);print(json.dumps({'daemon':True,'already_running':True}));return
        except (OSError,ValueError):pass
        if not enabled(app.ROOT) and app.running():raise ValueError('Stop the legacy managed runtime before enabling the supervisor')
        app.ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
        launch=Path(sys.argv[0]).resolve() if str(sys.argv[0]).endswith('.pyz') else Path(app.__file__).resolve()
        child=subprocess.Popen([sys.executable,str(launch),'daemon','run'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            try:rpc(app.ROOT,'status',timeout=.5);print(json.dumps({'daemon':True}));return
            except (OSError,ValueError):
                if child.poll() not in (None,0):raise ValueError('Supervisor startup failed; no foreign process signalled')
                time.sleep(.03)
        raise ValueError('Supervisor readiness timed out')
    if args.action in ('stop','disable'):
        keep=args.action=='stop' and args.keep_runtime
        owner=read(app.ROOT,OWNER);previous=read(app.ROOT,SESSION)
        try:rpc(app.ROOT,'shutdown',timeout=35,keep_runtime=keep)
        except (FileNotFoundError,ConnectionRefusedError):
            if not keep:Runtime(app).stop()
        deadline=time.monotonic()+40
        while ((app.ROOT/SOCKET).exists() or owned(owner,False) or
               (not keep and (owned(previous) or owned(read(app.ROOT,SESSION))))) and time.monotonic()<deadline:
            time.sleep(.02)
        if (app.ROOT/SOCKET).exists() or owned(owner,False) or (not keep and (owned(previous) or owned(read(app.ROOT,SESSION)))):
            raise ValueError('Supervisor shutdown incomplete; records/mode retained for review')
        if args.action=='disable':(app.ROOT/MODE).unlink(missing_ok=True)
        result=Runtime(app).status();result['daemon']=False
        print(json.dumps(result));return
    print(json.dumps(rpc(app.ROOT,'start' if args.action=='proxy-start' else 'stop',timeout=35)))
