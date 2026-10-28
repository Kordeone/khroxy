import base64
import copy
import importlib.machinery
import importlib.util
import json
import tempfile
import unittest
import uuid
from pathlib import Path

loader = importlib.machinery.SourceFileLoader('khroxy', str(Path(__file__).parent / 'khroxy'))
spec = importlib.util.spec_from_loader(loader.name, loader)
p = importlib.util.module_from_spec(spec)
loader.exec_module(p)

UUID = '01234567-89ab-4cde-8fff-0123456789ab'


class ParseTests(unittest.TestCase):
    def test_vless_ws_tls(self):
        url = f'vless://{UUID}@example.org:443?type=ws&security=tls&sni=front.org&host=front.org&path=%2Fws'
        obj = p.parse_share(url)
        self.assertEqual(obj['protocol'], 'vless')
        self.assertEqual(obj['streamSettings']['wsSettings']['path'], '/ws')
        self.assertEqual(obj['streamSettings']['tlsSettings']['serverName'], 'front.org')

    def test_vless_xhttp_reality(self):
        url = f'vless://{UUID}@example.org:443?type=xhttp&security=reality&pbk=fakepublickey&sid=1122&fp=chrome&path=%2Fsecret&mode=auto'
        obj = p.parse_share(url)
        self.assertEqual(obj['streamSettings']['xhttpSettings']['mode'], 'auto')
        self.assertEqual(obj['streamSettings']['realitySettings']['password'], 'fakepublickey')

    def test_vless_grpc_reality(self):
        url = f'vless://{UUID}@example.org:443?type=grpc&security=reality&pbk=key&serviceName=grpcname'
        obj = p.parse_share(url)
        self.assertEqual(obj['streamSettings']['grpcSettings']['serviceName'], 'grpcname')

    def test_vmess_legacy_json(self):
        src = dict(v='2', ps='test', add='example.org', port='443', id=UUID, aid='0', net='ws', path='/v', host='front.org', tls='tls', sni='front.org')
        link = 'vmess://' + base64.b64encode(json.dumps(src).encode()).decode()
        obj = p.parse_share(link)
        self.assertEqual(obj['protocol'], 'vmess')
        self.assertEqual(obj['streamSettings']['security'], 'tls')
        self.assertEqual(obj['streamSettings']['wsSettings']['host'], 'front.org')

    def test_vmess_uri(self):
        obj = p.parse_share(f'vmess://{UUID}@example.org:443?security=tls&type=ws&path=%2F')
        self.assertEqual(obj['protocol'], 'vmess')

    def test_trojan(self):
        obj = p.parse_share('trojan://verysecret@example.org:443?security=tls&sni=front.org')
        self.assertEqual(obj['settings']['servers'][0]['password'], 'verysecret')
        self.assertEqual(obj['streamSettings']['security'], 'tls')

    def test_ss_base64_userinfo(self):
        auth = base64.urlsafe_b64encode(b'aes-256-gcm:secret').decode().rstrip('=')
        obj = p.parse_share('ss://' + auth + '@example.org:8388')
        self.assertEqual(obj['protocol'], 'shadowsocks')
        self.assertEqual(obj['settings']['servers'][0]['method'], 'aes-256-gcm')

    def test_ss_fullbase64(self):
        body = base64.urlsafe_b64encode(b'aes-256-gcm:secret@example.org:8388').decode().rstrip('=')
        obj = p.parse_share('ss://' + body)
        self.assertEqual(obj['settings']['servers'][0]['password'], 'secret')

    def test_unknown_params_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported share-link parameters'):
            p.parse_share(f'vless://{UUID}@example.org:443?security=tls&somethingCritical=1')

    def test_insecure_disallowed(self):
        with self.assertRaisesRegex(ValueError, 'TLS verification bypass'):
            p.parse_share(f'vless://{UUID}@example.org:443?security=tls&allowInsecure=1')

    def test_plaintext_refused(self):
        with self.assertRaisesRegex(ValueError, 'Plaintext'):
            p.parse_share(f'vless://{UUID}@example.org:80?security=none')

    def test_missing_reality_key(self):
        with self.assertRaisesRegex(ValueError, 'pbk'):
            p.parse_share(f'vless://{UUID}@example.org:443?security=reality')

    def test_reality_ws_rejected(self):
        with self.assertRaisesRegex(ValueError, 'REALITY cannot'):
            p.parse_share(f'vless://{UUID}@example.org:443?type=ws&security=reality&pbk=key')

    def test_duplicate_param_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Repeated'):
            p.parse_share(f'vless://{UUID}@example.org:443?security=tls&security=none')

    def test_runtime_separate_per_app(self):
        state = {'version': 1,
                 'profiles': {'first': p.parse_share(f'vless://{UUID}@example.org:443?security=tls'),
                              'second': p.parse_share('trojan://pw@other.org:443')},
                 'apps': {'kilo': {'command': ['kilo'], 'port': 20820, 'profile': 'second'},
                          'codex': {'command': ['codex'], 'port': 20821, 'profile': 'first'}}}
        conf = p.build_runtime(state)
        self.assertEqual([x['listen'] for x in conf['inbounds']], ['127.0.0.1', '127.0.0.1'])
        mapping = {r['inboundTag'][0]: r['outboundTag'] for r in conf['routing']['rules']}
        self.assertEqual(mapping, {'app-codex': 'profile-first', 'app-kilo': 'profile-second'})
        self.assertNotIn('allowInsecure', json.dumps(conf))


if __name__ == '__main__':
    unittest.main(verbosity=2)

class ECHRegressionTests(unittest.TestCase):
    def test_tls_ech_encoded_config_preserved(self):
        from urllib.parse import quote
        ech = 'AF7+DQBaAAAgACA51i3Ssu4wUMV4FNCc8iRX5J+YC4Bhigz9sacl2lCfSQ=='
        url = f'vless://{UUID}@example.org:443?security=tls&ech={quote(ech)}'
        outbound = p.parse_share(url)
        self.assertEqual(outbound['streamSettings']['tlsSettings']['echConfigList'], ech)

    def test_tls_ech_dns_lookup_preserved(self):
        from urllib.parse import quote
        value = 'https://1.1.1.1/dns-query'
        url = f'trojan://secret@example.org:443?ech={quote(value, safe="")}'
        self.assertEqual(p.parse_share(url)['streamSettings']['tlsSettings']['echConfigList'], value)

    def test_reality_blank_ech_harmless(self):
        url = f'vless://{UUID}@example.org:443?security=reality&pbk=fakekey&ech='
        obj = p.parse_share(url)
        self.assertNotIn('tlsSettings', obj['streamSettings'])

    def test_reality_nonempty_ech_rejected(self):
        url = f'vless://{UUID}@example.org:443?security=reality&pbk=fakekey&ech=base64'
        with self.assertRaisesRegex(ValueError, 'ECH requires TLS'):
            p.parse_share(url)

    def test_partial_feed_keeps_supported_and_skips_unsupported(self):
        good = f'vless://{UUID}@example.org:443?security=tls'
        bad = f'vless://{UUID}@example.org:443?security=tls&unknownImportant=true'
        skipped = []
        result = p.parse_subscription((good + '\n' + bad).encode(),
                                      skip_unsupported=True, skipped=skipped)
        self.assertEqual(len(result), 1)
        self.assertEqual(skipped, [2])
        with self.assertRaisesRegex(ValueError, 'unsupported subscription link #2'):
            p.parse_subscription((good + '\n' + bad).encode())

    def test_partial_feed_does_not_hide_malformed_supported_entry(self):
        good = f'vless://{UUID}@example.org:443?security=tls'
        broken = 'vless://not-a-uuid@example.org:443?security=tls'
        with self.assertRaises(ValueError):
            p.parse_subscription((good + '\n' + broken).encode(), skip_unsupported=True)

    def test_no_supported_links_retains_previous(self):
        bad = f'vless://{UUID}@example.org:443?security=tls&unknownImportant=true'
        with self.assertRaisesRegex(ValueError, 'no supported profiles'):
            p.parse_subscription(bad.encode(), skip_unsupported=True)

class PlaintextSubscriptionRegressionTests(unittest.TestCase):
    def test_seventh_node_plaintext_skipped_only_in_partial_feed(self):
        tls = [f'vless://{str(uuid.UUID(int=i+1))}@node{i}.example:443?security=tls' for i in range(6)]
        plain = f'vless://{UUID}@example.net:80?security=none'
        valid_last = 'trojan://secret@example.org:443?security=tls'
        data = ('\n'.join([*tls, plain, valid_last]) + '\n').encode()
        skipped = []
        result = p.parse_subscription(data, skip_unsupported=True, skipped=skipped)
        self.assertEqual(len(result), 7)
        self.assertEqual(skipped, [7])
        self.assertTrue(all(out['streamSettings']['security'] == 'tls' for out in result))
        with self.assertRaisesRegex(ValueError, r'link #7: Plaintext transport refused'):
            p.parse_subscription(data)
        # The security exception remains explicit, not a global default.
        result_opt_in = p.parse_subscription(data, allow_plaintext=True)
        self.assertEqual(len(result_opt_in), 8)
        self.assertEqual(result_opt_in[6]['streamSettings']['security'], 'none')

    def test_plaintext_only_feed_rejected_even_with_partial_import_enabled(self):
        plaintext = f'vless://{UUID}@example.net:80?security=none'
        with self.assertRaisesRegex(ValueError, 'no supported profiles'):
            p.parse_subscription(plaintext.encode(), skip_unsupported=True)
