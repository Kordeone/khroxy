"""Offline checks only: no Docker daemon or host firewall is touched."""
import copy
import importlib.machinery
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
try:
    import yaml
except ImportError:
    yaml = None
p=Path(__file__).parent
loader=importlib.machinery.SourceFileLoader('khroxy_dockertest',str(p/'khroxy'))
spec=importlib.util.spec_from_loader(loader.name,loader)
mod=importlib.util.module_from_spec(spec);loader.exec_module(mod)
OUT={'protocol':'vless', 'settings':{'vnext':[{'address':'198.51.100.2','port':443,'users':[]}]}}
class ContainerFoundation(unittest.TestCase):
    def test_gateway_binds_container_interface_only_in_container_mode(self):
        state={'version':1,'profiles':{'one':OUT},'apps':{},'subscriptions':{},'active_profile':'one'}
        with patch.object(mod,'CONTAINER_MODE',True):
            config=mod.build_runtime(state)
        self.assertEqual(len(config['inbounds']),1)
        self.assertEqual(config['inbounds'][0]['listen'],'0.0.0.0')
        self.assertEqual(config['inbounds'][0]['port'],20819)
        self.assertEqual(config['routing']['rules'][0]['outboundTag'],'profile-one')
        self.assertEqual({o['protocol'] for o in config['outbounds']},{'vless'})
        with patch.object(mod,'CONTAINER_MODE',False):
            self.assertEqual(mod.build_runtime(state)['inbounds'],[])

    def test_no_gateway_without_active_or_best(self):
        state={'version':1,'profiles':{'one':OUT},'apps':{},'subscriptions':{}}
        with patch.object(mod,'CONTAINER_MODE',True):
            conf=mod.build_runtime(state)
        self.assertEqual(conf['inbounds'],[])
        self.assertEqual(conf['outbounds'],[])

    def test_fixed_app_bindings_stay_local_even_with_public_gateway(self):
        state={'version':1,'profiles':{'one':OUT},'apps':{'kilo':{'port':20820,'profile':'one','command':['kilo']}},'subscriptions':{},'active_profile':'one'}
        with patch.object(mod,'CONTAINER_MODE',True):
            conf=mod.build_runtime(state)
        self.assertEqual({i['tag']:i['listen'] for i in conf['inbounds']},
                         {'app-kilo':'127.0.0.1','gateway':'0.0.0.0'})

    def test_compose_has_localhost_only_publish_and_no_host_network(self):
        if yaml is None:
            text=(p/'compose.yaml').read_text()
            for snippet in ('127.0.0.1:20819:20819/tcp', 'cap_drop: [ALL]',
                            'read_only: true','create_host_path: false', '/mnt/storage/Projects'):
                self.assertIn(snippet, text)
            self.assertNotIn('network_mode: host', text.split('services:')[1].split('networks:')[0])
            return
        compose=yaml.safe_load((p/'compose.yaml').read_text())
        service=compose['services']['khroxy']
        self.assertEqual(service['ports'],['127.0.0.1:20819:20819/tcp'])
        self.assertNotIn('privileged',service)
        self.assertEqual(service['cap_drop'],['ALL'])
        self.assertNotIn('network_mode',service)
        self.assertEqual(service['volumes'][0]['source'],'/mnt/storage/Projects')
        self.assertFalse(service['volumes'][0]['bind']['create_host_path'])
        self.assertTrue(service['read_only'])
        self.assertTrue(service['security_opt'])
        self.assertNotIn('/var/run/docker.sock',str(service))

    def test_guard_sets_drop_before_exceptions_and_denies_docker_dns(self):
        text=(p/'docker/network-guard.sh').read_text()
        self.assertLess(text.index('iptables -w -P OUTPUT DROP'),text.index('iptables -w -A OUTPUT -m owner'))
        self.assertLess(text.index('ip6tables -w -P OUTPUT DROP'),text.index('ip6tables -w -A OUTPUT -m owner'))
        self.assertIn('127.0.0.11 -j REJECT',text)
        self.assertIn('--uid-owner "$PUID" -j ACCEPT',text)
        self.assertIn('--uid-owner "$AUID" -d "$addr" -p tcp --dport "$port"',text)
        self.assertNotIn('iptables -w -P OUTPUT ACCEPT',text)

if __name__ == '__main__':unittest.main()
