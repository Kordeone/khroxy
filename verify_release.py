#!/usr/bin/env python3
"""Verify artifact checksums, exact source/zipapp identity and isolated installation."""
import argparse
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import zipfile


def verify(directory,engine=None,development=False):
    root=Path(directory);members={}
    for line in (root/'SHA256SUMS').read_text().splitlines():
        if not re.fullmatch(r'[a-f0-9]{64}  khroxy-[0-9A-Za-z.\-]+(?:-source\.tar\.gz|\.pyz)',line):raise ValueError('Unsafe checksum record')
        digest,name=line.split('  ')
        path=root/name
        if name in members or path.is_symlink() or not path.is_file() or path.stat().st_size>64*1024*1024:raise ValueError('Unsafe release artifact')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Artifact checksum mismatch')
        members[name]=path
    if len(members)!=2:raise ValueError('Exactly one source archive and one zipapp are required')
    source=next(p for n,p in members.items() if n.endswith('.tar.gz'))
    app=next(p for n,p in members.items() if n.endswith('.pyz'))
    with tempfile.TemporaryDirectory(prefix='khroxy-artifact-test-') as temporary:
        base=Path(temporary);total=0
        with gzip.open(source,'rb') as compressed:
            expanded=compressed.read(20*1024*1024+1)
        if len(expanded)>20*1024*1024:raise ValueError('Expanded source archive too large')
        with tarfile.open(fileobj=io.BytesIO(expanded),mode='r:') as archive:
            entries=archive.getmembers()
            if len(entries)>1000:raise ValueError('Too many archive members')
            for item in entries:
                path=Path(item.name)
                if not item.isfile() or path.is_absolute() or '..' in path.parts or len(path.parts)<2 or item.mode&0o7000:
                    raise ValueError('Unsafe source archive member')
                total+=item.size
                if total>16*1024*1024:raise ValueError('Source archive too large')
                destination=base/path
                destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,item.mode&0o777)
                with os.fdopen(fd,'wb') as stream:stream.write(archive.extractfile(item).read())
        roots=list(base.glob('khroxy-*'))
        if len(roots)!=1:raise ValueError('Invalid archive root')
        source_root=roots[0];manifest=json.loads((source_root/'SOURCE.json').read_bytes())
        if manifest.get('dirty') and not development:raise ValueError('Development source cannot be released')
        if not re.fullmatch('[a-f0-9]{40}',manifest.get('revision','')):raise ValueError('Missing source revision')
        expected=set(manifest['files'])|{'SOURCE.json'}
        actual={str(p.relative_to(source_root)) for p in source_root.rglob('*') if p.is_file()}
        if actual!=expected:raise ValueError('Archive member projection mismatch')
        for name,digest in manifest['files'].items():
            if hashlib.sha256((source_root/name).read_bytes()).hexdigest()!=digest:raise ValueError('Source checksum mismatch')
        with zipfile.ZipFile(app) as archive:
            if sum(item.file_size for item in archive.infolist())>16*1024*1024 or len(archive.infolist())>1000:
                raise ValueError('Expanded zipapp too large')
            wanted={'__main__.py','SOURCE.json'}|{name for name in expected if name.startswith('khroxy_lib/')}
            if set(archive.namelist())!=wanted or len(archive.namelist())!=len(wanted):raise ValueError('Unexpected zipapp contents')
            if json.loads(archive.read('SOURCE.json'))!=manifest:raise ValueError('Zipapp source revision mismatch')
            for name in wanted-{'SOURCE.json'}:
                original='khroxy' if name=='__main__.py' else name
                if hashlib.sha256(archive.read(name)).hexdigest()!=manifest['files'][original]:raise ValueError('Zipapp implementation mismatch')
        home=base/'home';home.mkdir()
        environment=dict(os.environ,HOME=str(home),XDG_RUNTIME_DIR=str(home),DBUS_SESSION_BUS_ADDRESS='unix:path='+str(home/'no-bus'),PYTHONDONTWRITEBYTECODE='1')
        for executable in (source_root/'khroxy',app):
            result=subprocess.run(['python3',str(executable),'--version'],env=environment,capture_output=True,text=True,timeout=10)
            if result.returncode or result.stdout.strip()!='KHROXY '+manifest['version']:raise ValueError('Artifact version smoke failed')
        installed=False
        if engine:
            result=subprocess.run([str(source_root/'install.sh'),'--mode','native','--xray',str(Path(engine).resolve())],input='',env=environment,capture_output=True,text=True,timeout=30)
            if result.returncode:raise ValueError('Artifact native installation failed')
            cli=home/'.local/bin/khroxy'
            for args in (('storage','status'),('automation','profiles'),('engine','check'),('uninstall','--dry-run')):
                result=subprocess.run([str(cli),*args],env=environment,capture_output=True,text=True,timeout=20)
                if result.returncode:raise ValueError('Installed artifact smoke failed')
            installed=True
        return {'version':manifest['version'],'revision':manifest['revision'],'checksums':True,'source_zipapp_identity':True,
                'source_and_zipapp_startup':True,'isolated_installation':installed,'development':bool(manifest.get('dirty'))}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory');parser.add_argument('--xray');parser.add_argument('--development',action='store_true')
    args=parser.parse_args()
    try:print(json.dumps(verify(args.directory,args.xray,args.development)))
    except (OSError,ValueError,KeyError,StopIteration,subprocess.SubprocessError) as error:parser.exit(1,str(error)+'\n')
