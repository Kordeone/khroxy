# KHROXY managed module
"""Private immutable code snapshots and explicit stopped-runtime rollback."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from . import supervisor


def safe(path,home):
    for item in (path,*path.parents):
        if item.is_symlink():raise ValueError('Refusing symlink upgrade path')
        if item==home:break


def stopped(home):
    root=home/'.config/khroxy'
    safe(root,home)
    if supervisor.owned(supervisor.read(root,'api-owner.json'),False):
        raise ValueError('Stop the owned management API before changing code')
    if supervisor.owned(supervisor.read(root,supervisor.SESSION)) or supervisor.owned(supervisor.read(root,supervisor.OWNER),False):
        raise ValueError('Stop the owned supervisor and proxy before upgrading or rolling back code; sessions preserved')
    # Refuse a matching owned-runtime argv even when a user bus is unavailable.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        try: record = supervisor.identity(int(entry.name))
        except (OSError, ValueError, UnicodeError, KeyError, StopIteration): continue
        if record['uid'] == os.geteuid() and any(arg == str(root/'runtime.json') or arg.startswith(str(root/'sessions')+'/session-') for arg in record['argv']):
            raise ValueError('A runtime using this configuration is alive; stop it explicitly before changing code')
    for name in ('khroxy.service','khroxy-health.service','khroxy-supervisor.service','khroxy-sub-update.service'):
        path=home/'.config/systemd/user'/name;safe(path,home)
        if not path.exists():continue
        if not path.is_file() or not path.read_text().startswith('# Managed by khroxy\n'):
            raise ValueError('Foreign user unit retained')
        result=subprocess.run(['systemctl','--user','is-active','--quiet',name],capture_output=True,timeout=5)
        if result.returncode==0:raise ValueError('Stop owned proxy, health and subscription services before upgrade/rollback; no session is stopped automatically')
        if result.returncode not in (1,3,4):
            # A missing user bus returns 1 on isolated installs. Unknown states fail closed.
            raise ValueError('Could not determine owned user-service state; no upgrade performed')


def files(home):
    result={};cli=home/'.local/bin/khroxy';safe(cli,home)
    if cli.exists():
        if not cli.is_file() or 'khroxy: local-only' not in cli.read_text(errors='replace')[:500]:raise ValueError('Foreign installed CLI retained')
        result['cli']=cli.read_bytes()
    module=home/'.local/lib/khroxy/khroxy_lib';safe(module,home)
    if module.exists():
        for path in sorted(module.glob('*.py')):
            safe(path,home)
            if not path.is_file() or not path.read_bytes().startswith(b'# KHROXY managed module'):raise ValueError('Foreign installed module retained')
            result['modules/'+path.name]=path.read_bytes()
    for name in ('khroxy.service','khroxy-health.service'):
        path=home/'.config/systemd/user'/name;safe(path,home)
        if path.exists():
            if not path.is_file() or not path.read_text().startswith('# Managed by khroxy\n'):raise ValueError('Foreign installed unit retained')
            result['units/'+name]=path.read_bytes()
    return result


def snapshot(home):
    data=files(home)
    if 'cli' not in data:return None
    digest=hashlib.sha256(b''.join(name.encode()+b'\0'+hashlib.sha256(raw).digest() for name,raw in sorted(data.items()))).hexdigest()
    root=home/'.local/lib/khroxy/rollback-code';safe(root,home)
    root.mkdir(mode=0o700,parents=True,exist_ok=True);root.chmod(0o700)
    destination=root/digest
    if destination.exists():read_snapshot(home,digest);return digest
    temporary=Path(tempfile.mkdtemp(prefix='.snapshot-',dir=root))
    try:
        manifest={'schema':1,'files':{}}
        for name,raw in data.items():
            path=temporary/name;path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            path.write_bytes(raw);path.chmod(0o600);manifest['files'][name]=hashlib.sha256(raw).hexdigest()
        path=temporary/'manifest.json';path.write_text(json.dumps(manifest,sort_keys=True));path.chmod(0o600)
        os.rename(temporary,destination)
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    return digest


def read_snapshot(home,identifier):
    if not re.fullmatch('[a-f0-9]{64}',identifier):raise ValueError('Invalid code snapshot ID')
    root=home/'.local/lib/khroxy/rollback-code'/identifier;safe(root,home)
    manifest=root/'manifest.json';safe(manifest,home)
    info=manifest.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_size>65536:
        raise ValueError('Untrusted code snapshot manifest')
    document=json.loads(manifest.read_bytes())
    if document.get('schema')!=1 or not isinstance(document.get('files'),dict) or not 1<=len(document['files'])<=100:
        raise ValueError('Invalid code snapshot')
    result={}
    for name,digest in document['files'].items():
        if not re.fullmatch(r'cli|modules/[a-z_]+\.py|units/khroxy(?:-health)?\.service',name):raise ValueError('Unsafe code snapshot member')
        path=root/name;safe(path,home);info=path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_size>2*1024*1024:
            raise ValueError('Untrusted code snapshot member')
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Code snapshot checksum mismatch')
        result[name]=raw
    actual=hashlib.sha256(b''.join(name.encode()+b'\0'+hashlib.sha256(raw).digest() for name,raw in sorted(result.items()))).hexdigest()
    if actual!=identifier or 'cli' not in result:raise ValueError('Code snapshot identity mismatch')
    return result


def restore(home,identifier,atomic):
    data=read_snapshot(home,identifier);stopped(home);files(home)
    retained=snapshot(home)
    # Restore code/owned units only. Database and legacy JSON are never overwritten.
    for name,raw in sorted(data.items(),key=lambda item:item[0]=='cli'):
        path=home/'.local/bin/khroxy' if name=='cli' else home/'.local/lib/khroxy/khroxy_lib'/name[8:] if name.startswith('modules/') else home/'.config/systemd/user'/name[6:]
        safe(path,home);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        atomic(path,raw,0o755 if name=='cli' else 0o644 if name.startswith('units/') else 0o600)
    return retained
