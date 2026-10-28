#!/usr/bin/env python3
"""Build deterministic standalone source/zipapp artifacts; never publish or deploy."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import zipfile
from khroxy_lib import VERSION

ROOT=Path(__file__).resolve().parent
TOP=('HISTORY.md','khroxy','installer.py','install.sh','setup.py','workspace.sh','compose.yaml',
     'build_release.py','verify_release.py','publish_release.py','README.md','CHANGELOG.md','SECURITY.md',
     'PUBLISH.md','ENGINE_LOCK.json','retire-codex-xray.sh','.gitattributes')

DOCS=('AUTOMATION.md','DIAGNOSTICS.md','ENGINES.md','IMPORTS.md','NETWORKING.md',
      'RELEASE.md','RELEASE_NOTES.md','ROTATION.md','SCANNING.md','STORAGE.md',
      'SUPERVISOR.md','TESTING.md','khroxy.1','tui-baseline.json')

def inputs(root=None,allow_untracked=False):
    root=ROOT if root is None else root
    names=set(TOP)
    names.update('docs/'+name for name in DOCS)
    names.update('docker/'+name for name in ('Dockerfile','Containerfile','entrypoint.sh','network-guard.sh','scheduler.sh','runtime-paths.py','verify-isolation.py','caveman.py','.env.example'))
    names.update(('.gitignore','.dockerignore','.containerignore'))
    for directory,pattern in (('khroxy_lib','*.py'),('tests_support','*.py'),('completions','*'),('.github/workflows','*.yml')):
        names.update(str(p.relative_to(root)) for p in (root/directory).glob(pattern) if p.is_file())
    names.update(p.name for p in root.glob('test_*.py'))
    tracked=None
    if (root/'.git').exists():
        tracked=set(subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0'))
        ignored=subprocess.run(['git','check-ignore','--no-index','--stdin'],cwd=root,input='\n'.join(sorted(names))+'\n',capture_output=True,text=True)
        if ignored.returncode not in (0,1):raise ValueError('Could not verify ignored release inputs')
        names-=set(ignored.stdout.splitlines())
        if not allow_untracked:names&=tracked
    elif (root/'SOURCE.json').is_file():
        names&=set(json.loads((root/'SOURCE.json').read_bytes())['files'])
    result={}
    for name in sorted(names):
        path=root/name
        if path.is_symlink() or not path.is_file():raise ValueError('Missing or unsafe release input: '+name)
        # Container engine staging, settings/auth/cache and experimental controllers never ship.
        if name=='docker/xray' or name.startswith('windscribe/') or path.suffix in ('.deb','.log','.key','.pem'):
            continue
        result[name]=(path.read_bytes(),0o755 if os.access(path,os.X_OK) else 0o644)
    return result


def build(destination,allow_dirty=False):
    root=ROOT;manifest=root/'SOURCE.json'
    if (root/'.git').exists():
        revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
        epoch=int(subprocess.check_output(['git','show','-s','--format=%ct','HEAD'],cwd=root,text=True).strip())
        dirty=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip()
        if dirty and not allow_dirty:raise ValueError('Release requires clean committed source')
    elif manifest.is_file():
        source=json.loads(manifest.read_bytes());revision=source['revision'];epoch=source['epoch'];dirty=source.get('dirty',False)
        if dirty and not allow_dirty:raise ValueError('Source snapshot is a development build')
    else:raise ValueError('Git revision or validated SOURCE.json required')
    if 'SOURCE_DATE_EPOCH' in os.environ:epoch=int(os.environ['SOURCE_DATE_EPOCH'])
    if epoch<0:raise ValueError('Invalid source timestamp')
    data=inputs(root,allow_untracked=allow_dirty)
    source={'version':VERSION,'revision':revision,'epoch':epoch,'dirty':bool(dirty),
            'files':{name:hashlib.sha256(raw).hexdigest() for name,(raw,mode) in data.items()}}
    if manifest.is_file():
        for name,digest in json.loads(manifest.read_bytes())['files'].items():
            if name not in data or hashlib.sha256(data[name][0]).hexdigest()!=digest:raise ValueError('Source input checksum mismatch')
    data['SOURCE.json']=((json.dumps(source,sort_keys=True,indent=2)+'\n').encode(),0o644)
    destination=Path(destination)
    if destination.is_symlink():raise ValueError('Refusing symlink artifact directory')
    destination.mkdir(mode=0o700,parents=True,exist_ok=True)
    prefix='khroxy-'+VERSION
    tar_buffer=io.BytesIO()
    with tarfile.open(fileobj=tar_buffer,mode='w',format=tarfile.PAX_FORMAT) as archive:
        for name,(raw,mode) in sorted(data.items()):
            info=tarfile.TarInfo(prefix+'/'+name);info.size=len(raw);info.mode=mode;info.mtime=epoch
            info.uid=info.gid=0;info.uname=info.gname='';archive.addfile(info,io.BytesIO(raw))
    source_blob=gzip.compress(tar_buffer.getvalue(),compresslevel=9,mtime=0)
    zip_buffer=io.BytesIO()
    zip_buffer.write(b'#!/usr/bin/env python3\n')
    with zipfile.ZipFile(zip_buffer,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        app_data={'__main__.py':data['khroxy'][0],**{name:raw for name,(raw,mode) in data.items() if name.startswith('khroxy_lib/')},'SOURCE.json':data['SOURCE.json'][0]}
        for name,raw in sorted(app_data.items()):
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.create_system=3;info.external_attr=0o100644<<16
            archive.writestr(info,raw,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    artifacts={prefix+'-source.tar.gz':source_blob,prefix+'.pyz':zip_buffer.getvalue()}
    artifacts['SHA256SUMS']=(''.join(hashlib.sha256(raw).hexdigest()+'  '+name+'\n' for name,raw in sorted(artifacts.items()))).encode()
    # Refuse replacement: tests and reviewers receive exactly these immutable bytes.
    for name in artifacts:
        if (destination/name).exists() or (destination/name).is_symlink():raise ValueError('Artifact already exists; use an empty directory')
    for name,raw in artifacts.items():
        path=destination/name
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o755 if name.endswith('.pyz') else 0o644)
        with os.fdopen(fd,'wb') as stream:stream.write(raw)
    return {'version':VERSION,'revision':revision,'dirty':bool(dirty),'artifacts':sorted(artifacts)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    parser.add_argument('--allow-dirty',action='store_true',help='Development tests only; never publish these artifacts')
    args=parser.parse_args()
    try:print(json.dumps(build(args.output,args.allow_dirty)))
    except (OSError,ValueError,subprocess.SubprocessError) as error:parser.exit(1,str(error)+'\n')
