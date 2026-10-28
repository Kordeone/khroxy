#!/usr/bin/env python3
"""Tag-only GitHub Actions publisher. Verify first, draft, upload, then publish."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlencode
from verify_release import verify


def request(url,token,method='GET',data=None,content_type='application/json'):
    raw=json.dumps(data).encode() if data is not None and content_type=='application/json' else data
    req=Request(url,data=raw,method=method,headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
        'Content-Type':content_type,'X-GitHub-Api-Version':'2022-11-28','User-Agent':'KHROXY-release'})
    with urlopen(req,timeout=60) as response:
        body=response.read(1024*1024+1)
    if len(body)>1024*1024:raise ValueError('Oversized release API response')
    return json.loads(body)


def publish(directory,notes,environment=None,api_base='https://api.github.com'):
    environment=os.environ if environment is None else environment
    repo=environment.get('GITHUB_REPOSITORY');revision=environment.get('GITHUB_SHA','');token=environment.get('GITHUB_TOKEN')
    if repo!='Kordeone/khroxy' or environment.get('GITHUB_REF')!='refs/tags/v0.2.0' or not re.fullmatch('[a-f0-9]{40}',revision) or not token:
        raise ValueError('Publication requires the authorized repository/tag Actions context')
    checked=verify(directory)
    if checked['version']!='0.2.0' or checked['revision']!=revision or checked['development']:
        raise ValueError('Release tag, tested revision and verified artifacts must agree')
    endpoint=api_base+'/repos/'+repo+'/releases'
    try:
        request(endpoint+'/tags/v0.2.0',token)
    except HTTPError as error:
        if error.code!=404:raise ValueError('Could not verify absence of an existing release') from None
    else:raise ValueError('Release already exists; never replace a published release')
    text=Path(notes).read_text()
    draft=request(endpoint,token,'POST',{'tag_name':'v0.2.0','target_commitish':revision,'name':'KHROXY v0.2.0',
        'body':text,'draft':True,'prerelease':False})
    identifier=draft['id'];upload=draft['upload_url'].split('{',1)[0]
    parsed=urlsplit(upload)
    if api_base=='https://api.github.com' and (parsed.scheme!='https' or parsed.hostname!='uploads.github.com'):
        raise ValueError('Unexpected asset upload host; draft retained unpublished')
    try:
        files=sorted(Path(directory).iterdir())
        if len(files)!=3:raise ValueError('Unexpected release artifact directory contents')
        for path in files:
            if not path.is_file() or path.is_symlink():raise ValueError('Unsafe release asset')
            raw=path.read_bytes();digest='sha256:'+hashlib.sha256(raw).hexdigest()
            response=request(upload+'?'+urlencode({'name':path.name}),token,'POST',raw,'application/octet-stream')
            if response.get('digest')!=digest or response.get('size')!=len(raw):
                raise ValueError('Uploaded asset digest/size mismatch; draft retained unpublished')
        result=request(endpoint+'/'+str(identifier),token,'PATCH',{'draft':False})
        if result.get('draft') or result.get('tag_name')!='v0.2.0':raise ValueError('Published release response unverified')
        return {'version':'0.2.0','revision':revision,'release_url':result['html_url'],'assets_verified':3}
    except (OSError,ValueError,KeyError):
        # Keep a failed upload as a private draft for owner review; never publish a partial release.
        raise ValueError('Release upload/publication failed; inspect the unpublished draft and workflow logs') from None


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory');parser.add_argument('--notes',default='docs/RELEASE_NOTES.md');args=parser.parse_args()
    try:print(json.dumps(publish(args.directory,args.notes)))
    except (OSError,ValueError,KeyError) as error:parser.exit(1,str(error)+'\n')
