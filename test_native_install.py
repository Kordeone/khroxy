"""No-host-change tests for independent Khroxy install."""
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_install as mod

class NativeInstallerTests(unittest.TestCase):
    def test_readonly(self):
        with patch.object(mod.os, "geteuid", return_value=1000), \
             patch.object(mod.platform, "system", return_value="Linux"), \
             patch.object(mod.platform, "machine", return_value="x86_64"):
            self.assertEqual(mod.main(["--check"]), 0)

    def test_root_denied(self):
        with patch.object(mod.os, "geteuid", return_value=0):
            with self.assertRaises(mod.Refused):
                mod.check()

    def test_refuse_symlink_destination(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            target = root / "link"
            target.symlink_to(root / "somewhere")
            with self.assertRaises(mod.Refused):
                mod.install_file(b"safe", target)

    def test_version_pins_and_digests(self):
        self.assertEqual(mod.XRAY_VERSION, "v26.10.21")
        self.assertTrue(all(len(sha) == 64 for _, sha in mod.XRAY_ASSETS.values()))

if __name__ == "__main__":
    unittest.main()
