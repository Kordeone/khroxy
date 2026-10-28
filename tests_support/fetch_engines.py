"""Fetch only checksum-pinned optional test engines into an explicit directory."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen
import zipfile


def fetch(destination,lock):
    destination=Path(destination);destination.mkdir(mode=0o700,parents=True,exist_ok=True)
    pins=json.loads(Path(lock).read_bytes())
    for name,entry in pins['engines'].items():
        path=destination/(name+'.archive')
        if not path.exists():
            with urlopen(entry['url'],timeout=60) as response:
                raw=response.read(64*1024*1024+1)
            if len(raw)>64*1024*1024:raise ValueError('Engine archive too large')
            if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('Engine archive checksum mismatch')
            path.write_bytes(raw)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('Engine archive checksum mismatch')
        if entry['archive']=='zip':
            with zipfile.ZipFile(path) as archive:raw=archive.read(entry['member'])
        else:
            with tarfile.open(path,'r:gz') as archive:raw=archive.extractfile(entry['member']).read()
        executable=destination/name;executable.write_bytes(raw);executable.chmod(0o700)
    return {name:str(destination/name) for name in pins['engines']}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True);parser.add_argument('--lock',default='ENGINE_LOCK.json')
    args=parser.parse_args();print(json.dumps(fetch(args.output,args.lock)))
