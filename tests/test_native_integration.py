"""Optional acceleration remains explicit and installer ownership stays strict."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from retrofetch import installer


class NativeInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prefix = self.root / 'prefix'
        (self.prefix / 'bin').mkdir(parents=True)
        self.package = self.prefix / 'lib/retrofetch'
        self.package.mkdir(parents=True)
        (self.package / installer.PACKAGE_MARKER).write_text(json.dumps({'managed_by':'retrofetch','version':1}))
        self.launcher = self.prefix / 'bin/retrofetch'
        self.launcher.write_text('#!/usr/bin/env python3\n' + installer.MANAGED + '\npass\n')
        self.launcher.chmod(0o755)
        self.frontend = self.root / 'frontend'
        self.frontend.write_bytes(b'\x7fELFtest-native-fixture')

    def test_install_native_pair_is_owned_and_repeatable(self):
        from retrofetch.native_install import install_native
        original = self.launcher.read_bytes()
        install_native(self.prefix, self.frontend)
        self.assertEqual(self.launcher.read_bytes(), self.frontend.read_bytes())
        self.assertEqual((self.prefix / 'bin/retrofetch-python').read_bytes(), original)
        installer._validate_targets(self.prefix)
        install_native(self.prefix, self.frontend)
        self.assertEqual((self.prefix / 'bin/retrofetch-python').read_bytes(), original)

    def test_python_upgrade_can_reenable_native_with_managed_companion(self):
        from retrofetch.native_install import install_native
        install_native(self.prefix, self.frontend)
        updated = '#!/usr/bin/env python3\n' + installer.MANAGED + '\n# upgraded\n'
        self.launcher.write_text(updated)
        (self.package / installer.PACKAGE_MARKER).write_text(json.dumps({'managed_by':'retrofetch','version':1}))
        install_native(self.prefix, self.frontend)
        self.assertEqual((self.prefix / 'bin/retrofetch-python').read_text(), updated)
        installer._validate_targets(self.prefix)

    def test_unmanaged_companion_rejected_without_change(self):
        from retrofetch.native_install import install_native
        companion = self.prefix / 'bin/retrofetch-python'
        companion.write_text('user file')
        before = self.launcher.read_bytes()
        with self.assertRaises((ValueError, RuntimeError)):
            install_native(self.prefix, self.frontend)
        self.assertEqual(self.launcher.read_bytes(), before)
        self.assertEqual(companion.read_text(), 'user file')

    def test_failed_native_publication_restores_exact_pair(self):
        from retrofetch.native_install import install_native
        import os
        marker = self.package / installer.PACKAGE_MARKER
        original, ownership = self.launcher.read_bytes(), marker.read_bytes()
        replace = os.replace
        failed = False
        def fail(source, target):
            nonlocal failed
            if Path(target) == self.launcher and not failed:
                failed = True
                raise OSError('injected publication failure')
            return replace(source, target)
        with patch('retrofetch.native_install.os.replace', side_effect=fail):
            with self.assertRaises(OSError):
                install_native(self.prefix, self.frontend)
        self.assertEqual(self.launcher.read_bytes(), original)
        self.assertEqual(marker.read_bytes(), ownership)
        self.assertFalse((self.prefix / 'bin/retrofetch-python').exists())
        installer._validate_targets(self.prefix)

    def test_changed_native_launcher_rejected(self):
        from retrofetch.native_install import install_native
        install_native(self.prefix, self.frontend)
        self.launcher.write_bytes(b'\x7fELFchanged')
        with self.assertRaises(installer.InstallationError):
            installer._validate_targets(self.prefix)
