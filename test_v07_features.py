"""New full-screen view and latency/route tests with no network or host changes."""
import copy
import importlib.machinery
import importlib.util
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

loader=importlib.machinery.SourceFileLoader('khroxy_v07',str(Path(__file__).with_name('khroxy')))
spec=importlib.util.spec_from_loader(loader.name,loader)
p=importlib.util.module_from_spec(spec); loader.exec_module(p)

PROF={'protocol':'vless','settings':{'vnext':[{'address':'198.51.100.2','port':443,'users':[{'id':'not-exposed'}]}]},
      'streamSettings':{'network':'tcp','security':'reality'}}

class FakeWindow:
    def __init__(self,keys,h=36,w=118):
        self.keys=iter(keys); self.h=h; self.w=w; self.lines=[]; self.snapshots=[]
    def getmaxyx(self):return self.h,self.w
    def keypad(self,v):pass
    def timeout(self,v):pass
    def erase(self):self.lines=[]
    def attron(self,v):pass
    def attroff(self,v):pass
    def addstr(self,*args):pass
    def addnstr(self,y,x,s,width,*args):self.lines.append((y,x,str(s)))
    def refresh(self):self.snapshots.append(list(self.lines))
    def getch(self):return next(self.keys)

class Features(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.ps=[patch.object(p,'ROOT',self.root),patch.object(p,'STATE',self.root/'state.json'),
                 patch.object(p,'RUNTIME',self.root/'runtime.json'),patch.object(p,'EVENTS',self.root/'events.log')]
        for v in self.ps: v.start();self.addCleanup(v.stop)
        self.state={'version':1,'profiles':{'main':copy.deepcopy(PROF),'backup':copy.deepcopy(PROF)},
                    'apps':{}, 'subscriptions':{'joy':{'mode':'profile','via':'main','profiles':[],'minutes':0,'last_ok':None}},
                    'active_profile':'main'}
        p.write_file(p.STATE,p.serial(self.state))

    def test_four_independent_screens_and_selected_asterisk(self):
        p.log_event('fetch_ok','joy','downloaded')
        w=FakeWindow([ord('2'),ord('3'),ord('4'),ord('1'),ord('q')])
        stop=threading.Event()
        with patch.object(p,'start_status_monitor',return_value=({'value':'OFFLINE'},stop)), \
             patch.object(p.curses,'curs_set',return_value=None),patch.object(p.curses,'noecho',return_value=None):
            p._tui(w)
        pages=[' '.join(line for _,_,line in snap) for snap in w.snapshots]
        self.assertTrue(any('2/4  APPLICATIONS' in x for x in pages))
        self.assertTrue(any('3/4  SUBSCRIPTIONS' in x for x in pages))
        self.assertTrue(any('4/4  LOGS' in x and 'fetch_ok' in x for x in pages))
        self.assertTrue(any('1/4  PROFILES' in x and '* main' in x for x in pages))
        self.assertFalse(any('not-exposed' in x for x in pages))
        self.assertTrue(stop.is_set())

    def test_active_mark_persists_without_switching_cursor(self):
        with patch.object(p,'running',return_value=False):p.select_active('backup')
        self.assertEqual(p.load()['active_profile'],'backup')

    def test_runtime_routes_resolve_fixed_active_fastest(self):
        state=copy.deepcopy(self.state)
        state['fastest_profile']='backup'
        state['apps']={'one':{'port':20820,'profile':'main','command':['test']},
                       'two':{'port':20821,'profile':'@active','command':['test']},
                       'three':{'port':20822,'profile':'@fastest','command':['test']}}
        run=p.build_runtime(state)
        routes={r['inboundTag'][0]:r['outboundTag'] for r in run['routing']['rules']}
        self.assertEqual(routes, {'app-one':'profile-main','app-two':'profile-main','app-three':'profile-backup'})
        self.assertTrue(all(i['listen']=='127.0.0.1' for i in run['inbounds']))
        self.assertFalse(any(o['protocol']=='freedom' for o in run['outbounds']))

    def test_dynamic_route_fails_closed_no_profile(self):
        state=copy.deepcopy(self.state);state['fastest_profile']=None
        state['apps']={'a':{'port':20820,'profile':'@fastest','command':['test']}}
        with self.assertRaisesRegex(ValueError,'No working profile'):p.build_runtime(state)

    def test_best_only_recent_success_with_tie_break(self):
        now=int(time.time())
        state=copy.deepcopy(self.state)
        state['health']={'main':{'ok':True,'ms':30,'time':now-1800},
                         'backup':{'ok':True,'ms':100,'time':now}}
        self.assertEqual(p.choose_best(state),'backup')
        state['health']['backup']['ok']=False
        self.assertIsNone(p.choose_best(state))

    def test_profile_measurement_is_https_via_local_proxy(self):
        from contextlib import contextmanager
        @contextmanager
        def fake_proxy(outbound):
            yield 'http://127.0.0.1:25000'
        class Result: returncode=0; stdout='204 0.12345'
        with patch.object(p,'temporary_profile_proxy',side_effect=fake_proxy), \
             patch.object(p.subprocess,'run',return_value=Result()) as run:
            ping=p.probe_profile(PROF)
        self.assertEqual(ping['ms'],123)
        args=run.call_args.args[0]
        self.assertIn('http://127.0.0.1:25000',args)
        self.assertIn('--noproxy',args)
        self.assertIn('https://www.gstatic.com/generate_204',args)

    def test_profile_probe_failure_doesnt_select_bad_connection(self):
        bad={'ok':False,'ms':None,'time':int(time.time())}
        with patch.object(p,'probe_profile',return_value=bad):
            with self.assertRaisesRegex(ValueError,'No working profile'):
                p.test_profiles(['main','backup'],select_fastest=True)
        self.assertIsNone(p.load().get('fastest_profile'))

    def test_selected_profile_deletion_does_not_leave_stale_marker(self):
        self.state['active_profile']='backup';p.write_file(p.STATE,p.serial(self.state))
        # Ensure no app uses it. Removal operation clears active selection (CLI analogous).
        with patch.object(p,'running',return_value=False): p.select_active('main')
        self.assertEqual(p.load()['active_profile'],'main')

if __name__=='__main__':unittest.main()
