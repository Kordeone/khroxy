# KHROXY managed module
"""Private persistent diagnostics and optional offline country/ASN enrichment."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import time
from .importing import fingerprint
from .storage import Store


def record(app,state,results):
    store=Store(app.STATE)
    if not store.exists: raise ValueError('Run khroxy storage migrate before persistent testing; stop older KHROXY writers first')
    for result in results:
        name=result['profile']
        data=dict(result,configuration_hash=fingerprint(state['profiles'][name]))
        store.append('measurements',name,data,result.get('timestamp',time.time()))


def ranking(store,sort='latency',max_age=900,include_disabled=False):
    if sort not in ('latency','download','upload') or not 1<=max_age<=86400*365:
        raise ValueError('Invalid ranking options')
    state=store.load();now=time.time();rows=[]
    for name,node in state['profiles'].items():
        options=state.get('profile_options',{}).get(name,{})
        if not include_disabled and not options.get('enabled',True): continue
        history=store.history('measurements',name,1)
        row={'profile':name,'status':'untested','ms':None,'download_bps':None,'upload_bps':None,
             'timestamp':None,'category':'untested','country':options.get('country'),'asn':options.get('asn')}
        if history:
            item=history[0];data=item['data'];row['timestamp']=item['timestamp']
            stages=data.get('stages',{})
            revision=data.get('configuration_hash')
            if revision != fingerprint(node): row.update(status='untested',category='configuration-changed')
            elif now-item['timestamp']<0 or now-item['timestamp']>max_age: row.update(status='stale',category='stale')
            elif data.get('ok') is not None:
                row['status']='healthy' if data['ok'] else 'failed'
                failures=[stages[key].get('category','failed') for key in ('http','tcp','icmp','download','upload') if key in stages and not stages[key].get('ok')]
                row['category']='ok' if data['ok'] else (failures[0] if failures else data.get('category','unavailable'))
                row['ms']=data.get('ms')
                for stage in ('http','download','upload'):
                    if stages.get(stage,{}).get('ok'):
                        row['ms']=stages[stage].get('ms');break
                for stage in ('download','upload'):
                    if stages.get(stage,{}).get('ok'): row[stage+'_bps']=stages[stage].get('bytes_per_second')
        rows.append(row)
    field={'latency':'ms','download':'download_bps','upload':'upload_bps'}[sort]
    def key(row):
        value=row[field]
        return (0 if row['status']=='healthy' else 1, value is None,
                (value if sort=='latency' else -value) if value is not None else 0,row['profile'])
    return sorted(rows,key=key)


class GeoData:
    """Optional user-supplied JSON CIDR database. No DNS or network lookup."""
    def __init__(self,path):
        path=Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size>4*1024*1024:
            raise ValueError('Unsafe or oversized metadata file')
        try:
            payload=json.loads(path.read_bytes())
            if payload['version']!=1 or not 0<=len(payload['networks'])<=10000: raise ValueError()
            self.entries=[]
            for row in payload['networks']:
                network=ipaddress.ip_network(row['cidr'],strict=True)
                country=row.get('country');asn=row.get('asn')
                if country is not None and not re.fullmatch('[A-Z]{2}',country): raise ValueError()
                if asn is not None and (type(asn) is not int or not 1<=asn<=4294967295): raise ValueError()
                self.entries.append((network,{'country':country,'asn':asn,'metadata_time':int(time.time())}))
            self.entries.sort(key=lambda row:row[0].prefixlen,reverse=True)
        except (ValueError,KeyError,TypeError,AttributeError):
            raise ValueError('Invalid offline metadata dataset') from None

    def lookup(self,address):
        try: value=ipaddress.ip_address(address)
        except ValueError:return None
        return next((data for network,data in self.entries if value.version==network.version and value in network),None)


def cli(app,verb,argv):
    parser=argparse.ArgumentParser(prog='khroxy '+verb)
    if verb=='results':
        sub=parser.add_subparsers(dest='action',required=True)
        rank=sub.add_parser('rank');rank.add_argument('--sort',choices=('latency','download','upload'),default='latency')
        rank.add_argument('--max-age',type=int,default=900);rank.add_argument('--include-disabled',action='store_true')
        prune=sub.add_parser('prune');prune.add_argument('--before',type=float,required=True);prune.add_argument('--keep',type=int,default=10000)
    else:
        parser.add_argument('action',choices=('enrich',));parser.add_argument('--file',required=True)
    args=parser.parse_args(argv);store=Store(app.STATE)
    if verb=='results':
        if args.action=='rank': print(json.dumps(ranking(store,args.sort,args.max_age,args.include_disabled)))
        else: print(json.dumps(store.prune(args.before,args.keep)))
        return
    try: dataset=GeoData(args.file)
    except (ValueError,OSError):
        print(json.dumps({'metadata':'unavailable','updated':0,'proxy_unchanged':True}));return
    state=store.load();updated=0
    for name,node in state['profiles'].items():
        from .testing import endpoint
        try:address,_=endpoint(node)
        except (KeyError,ValueError,TypeError):continue
        data=dataset.lookup(address)
        if data:
            store.update_metadata(name,data);store.append('metadata',name,dict(data,provider='offline-json'));updated+=1
    print(json.dumps({'metadata':'offline-json','updated':updated,'proxy_unchanged':True}))
