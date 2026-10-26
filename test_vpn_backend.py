import unittest
from unittest.mock import patch
import khroxy_vpn as w

class WindscribeBackendTests(unittest.TestCase):
    def test_assets_pinned_and_not_unverified_latest(self):
        self.assertEqual(w.WINDSCRIBE_VERSION, "v2.24.13")
        self.assertTrue(all(len(v[1]) == 64 for v in w.WINDSCRIBE_ASSETS.values()))
        self.assertIn("windscribe-cli_", w.package_for("amd64")[0])

    def test_connected_and_firewall_on(self):
        state = w.parse_status("Connect state: Connected: Toronto\nFirewall state: On\n")
        self.assertTrue(state.ready)

    def test_rejects_pending_tunnel_test(self):
        state = w.parse_status("Connect state: * Connected: Toronto\nFirewall state: On\n")
        self.assertFalse(state.ready)

    def test_rejects_firewall_off(self):
        state = w.parse_status("Connect state: Connected: Toronto\nFirewall state: Off\n")
        self.assertFalse(state.ready)

    def test_rejects_interference(self):
        state = w.parse_status("Connect state: Connected: Toronto [Network interference]\nFirewall state: On\n")
        self.assertFalse(state.ready)

    def test_never_switches_host(self):
        with patch.dict(w.os.environ, {"KHAGENT_CONTAINER": "0"}):
            with self.assertRaises(w.BackendUnavailable):
                w.require_isolated_controller()

if __name__ == "__main__":
    unittest.main()
