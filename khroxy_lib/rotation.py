# KHROXY managed module
"""Rotation policy independent of UI and fixed/active application assignments."""
import argparse
import copy
import http.client
import json
import math
import time
from urllib.parse import quote
from .storage import Store

DEFAULT={'enabled':False,'interval':1800,'cooldown':300,'failure_threshold':2,'last_switch':0}


def policy(state):
    if not isinstance(state.get('rotation_policy',{}),dict):raise ValueError('Invalid rotation policy')
    value=dict(DEFAULT,**state.get('rotation_policy',{}))
    if (type(value['enabled']) is not bool or type(value['interval']) is not int or not 30<=value['interval']<=86400 or
        type(value['cooldown']) is not int or not 0<=value['cooldown']<=86400 or
        type(value['failure_threshold']) is not int or not 1<=value['failure_threshold']<=10 or
        not isinstance(value['last_switch'],(int,float)) or not math.isfinite(value['last_switch']) or value['last_switch']<0):
        raise ValueError('Invalid rotation policy')
    return value


def eligible(state,name,now=None):
    now=time.time() if now is None else now
    option=state.get('profile_options',{}).get(name,{})
    return (name in state['profiles'] and option.get('enabled',True) and
            (not policy(state)['enabled'] or state.get('rotation_state',{}).get(name,{}).get('cooldown_until',0)<=now))


def observe(state,results,now=None):
    now=time.time() if now is None else now;settings=policy(state)
    records=state.setdefault('rotation_state',{})
    if not isinstance(records,dict):raise ValueError('Invalid rotation state')
    for name,result in results.items():
        if name not in state['profiles']: continue
        item=records.setdefault(name,{'failures':0,'cooldown_until':0})
        if type(item.get('failures')) is not int or item['failures']<0:raise ValueError('Invalid rotation failure state')
        if result['ok']: item.update(failures=0,cooldown_until=0)
        else:
            item['failures']+=1
            if settings['enabled'] and item['failures']>=settings['failure_threshold']:
                item['cooldown_until']=now+settings['cooldown']


def choose(app,state,now=None,force=False):
    now=time.time() if now is None else now;settings=policy(state)
    current=state.get('fastest_profile')
    if current in state['profiles'] and state.get('profile_options',{}).get(current,{}).get('enabled',True) and state.get('profile_options',{}).get(current,{}).get('pinned'):
        return current,'pinned'
    best=app.choose_stable_best(state)
    if not settings['enabled'] and not force:return best,'latency'
    current_health=state.get('health',{}).get(current,{})
    failures=state.get('rotation_state',{}).get(current,{}).get('failures',0)
    if current and eligible(state,current,now) and not current_health.get('ok') and failures<settings['failure_threshold'] and not force:
        return current,'confirming-failure'
    if current_health.get('ok') and now-settings['last_switch']<settings['cooldown'] and not force:
        return current,'cooldown'
    due=force or now-settings['last_switch']>=settings['interval']
    if due and current_health.get('ok'):
        alternatives=copy.deepcopy(state)
        alternatives.get('health',{}).pop(current,None)
        other=app.choose_best(alternatives,max_age=max(900,app.health_settings(state)['interval']*2))
        return (other or best),'schedule'
    return best,'confirmed-failure' if not current_health.get('ok') else 'latency'


def note_switch(app,old,new,reason):
    if new.get('fastest_profile')==old.get('fastest_profile'):return
    settings=policy(new);settings['last_switch']=time.time();new['rotation_policy']=settings
    if Store(app.STATE).exists:
        Store(app.STATE).append('rotations',new.get('fastest_profile'),
            {'from':old.get('fastest_profile'),'to':new.get('fastest_profile'),'reason':reason,'status':'applied'})


def sing_request(settings,method,target=None):
    connection=http.client.HTTPConnection('127.0.0.1',settings['port'],timeout=3)
    try:
        payload=json.dumps({'name':target}) if target is not None else None
        connection.request(method,'/proxies/'+quote('khroxy-fastest',safe=''),body=payload,
                           headers={'Authorization':'Bearer '+settings['secret'],'Content-Type':'application/json'})
        response=connection.getresponse();raw=response.read(65537)
        if len(raw)>65536 or response.status not in (200,204):raise ValueError('Engine selector API unavailable')
        return json.loads(raw) if raw else {}
    finally:connection.close()


def switch_sing(app,old,proposed):
    if not proposed.get('session_safe_fastest') or not app.RUNTIME.is_file():return False
    try:
        desired=app.build_runtime(proposed);installed=json.loads(app.RUNTIME.read_text())
        expected=next(item for item in desired['outbounds'] if item['type']=='selector')
        previous=next(item for item in installed['outbounds'] if item['type']=='selector')
        previous['default']=expected['default']
        if installed!=desired:return False
        settings=proposed['engine_management']['sing-box'];target='profile-'+proposed['fastest_profile']+'#'
        prior='profile-'+old['fastest_profile']+'#'
        try:
            sing_request(settings,'PUT',target)
            if sing_request(settings,'GET').get('now')!=target:raise ValueError('Selector target unverified')
            app.write_file(app.RUNTIME,app.serial(desired))
            return True
        except (OSError,ValueError,http.client.HTTPException):
            try:
                sing_request(settings,'PUT',prior)
                if sing_request(settings,'GET').get('now')!=prior:raise ValueError('Rollback target unverified')
            except (OSError,ValueError,http.client.HTTPException):
                proposed['rotation_error']='target-unverified; automatic changes suspended'
            app.log_event('health_api_fail',detail='unavailable')
            return False
    except (OSError,ValueError,KeyError,StopIteration):return False


def cli(app,argv):
    parser=argparse.ArgumentParser(prog='khroxy rotation');sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('status');sub.add_parser('now');sub.add_parser('disable')
    p=sub.add_parser('enable');p.add_argument('--interval',type=int,default=1800);p.add_argument('--cooldown',type=int,default=300);p.add_argument('--failure-threshold',type=int,default=2)
    p=sub.add_parser('pin');p.add_argument('name');p.add_argument('--off',action='store_true')
    args=parser.parse_args(argv)
    if args.action=='now':app.background_health(force_rotation=True);return
    fd=app.lock()
    try:
        state=app.load()
        if args.action=='status': print(json.dumps({'policy':policy(state),'pending':state.get('pending_fastest'),'status':state.get('health_status'),'error':state.get('rotation_error')}));return
        if args.action=='pin':
            app.check_name(args.name);Store(app.STATE).update_metadata(args.name,{'pinned':not args.off})
            print(json.dumps({'profile':args.name,'pinned':not args.off}));return
        settings=policy(state);settings['enabled']=args.action=='enable'
        if args.action=='enable':
            if state.get('rotation_error'):
                if app.running(): raise ValueError('Stop the managed runtime before resuming unverified rotation')
                state.pop('rotation_error',None)
            settings.update(interval=args.interval,cooldown=args.cooldown,failure_threshold=args.failure_threshold)
        state['rotation_policy']=settings;policy(state)
        app.engines.prepare_management(state)
        app.write_file(app.STATE,app.serial(state))
        print(json.dumps({'policy':settings,'scope':'@fastest only; fixed and @active assignments retained'}))
    finally:app.os.close(fd)
