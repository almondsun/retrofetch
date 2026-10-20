"""Optional acceleration remains explicit and installer ownership stays strict."""
import json
import contextlib
import errno
import io
import os
import stat
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

    def snapshot(self):
        return {str(p.relative_to(self.prefix)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
                for p in self.prefix.rglob('*') if p.is_file()}

    def test_persistent_cross_device_marker_failure_leaves_original_tree(self):
        from retrofetch.native_install import main
        before = self.snapshot()
        replace = os.replace
        marker = self.package / installer.PACKAGE_MARKER
        def cross_device(source, target):
            if Path(target) == marker and Path(source).parent != marker.parent:
                # Temporary subdirectories on the target filesystem are valid too.
                if marker.parent not in Path(source).parents:
                    raise OSError(errno.EXDEV, 'persistent marker cross-device failure')
            return replace(source, target)
        with patch('retrofetch.native_install.os.replace', side_effect=cross_device), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--prefix', str(self.prefix), '--frontend', str(self.frontend)]), 0)
        installer._validate_targets(self.prefix)
        self.assertFalse(list(self.prefix.rglob('.native-install-*')))
        self.assertNotEqual(self.snapshot(), before)

    def test_every_publication_failure_restores_bytes_modes_and_no_garbage(self):
        from retrofetch.native_install import install_native, main
        install_native(self.prefix, self.frontend)
        self.launcher.chmod(0o751)
        (self.prefix / 'bin/retrofetch-python').chmod(0o750)
        marker = self.package / installer.PACKAGE_MARKER
        marker.chmod(0o640)
        before = self.snapshot()
        replace = os.replace
        for failed_target in (self.prefix / 'bin/retrofetch-python', marker, self.launcher):
            with self.subTest(target=failed_target):
                failed = False
                def fail(source, target):
                    nonlocal failed
                    if Path(target) == failed_target and not failed:
                        failed = True
                        raise OSError('original publication cause')
                    return replace(source, target)
                output = io.StringIO()
                with patch('retrofetch.native_install.os.replace', side_effect=fail), contextlib.redirect_stderr(output):
                    self.assertEqual(main(['--prefix', str(self.prefix), '--frontend', str(self.frontend)]), 1)
                self.assertIn('original publication cause', output.getvalue())
                self.assertEqual(self.snapshot(), before)
                self.assertFalse(list(self.prefix.rglob('.native-install-*')))

    def interrupted_publication(self, position):
        from retrofetch.native_install import install_native
        targets = (self.prefix / 'bin/retrofetch-python',
                   self.package / installer.PACKAGE_MARKER, self.launcher)
        before = self.snapshot()
        replace = os.replace
        cause = KeyboardInterrupt('interrupt after successful rename')
        def interrupt(source, target):
            result = replace(source, target)
            if Path(target) == targets[position] and Path(source).name == 'replacement':
                raise cause
            return result
        with patch('retrofetch.native_install.os.replace', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt) as caught:
                install_native(self.prefix, self.frontend)
        self.assertIs(caught.exception, cause)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.prefix.rglob('.native-install-*')))
        installer._validate_targets(self.prefix)

    def test_interrupt_after_companion_rename_restores_original_tree(self):
        self.interrupted_publication(0)

    def test_interrupt_after_marker_rename_restores_original_tree(self):
        self.interrupted_publication(1)

    def test_interrupt_after_launcher_rename_restores_original_tree(self):
        self.interrupted_publication(2)

    def test_rollback_continues_and_retains_exact_recovery_after_failure(self):
        from retrofetch.native_install import install_native
        install_native(self.prefix, self.frontend)
        companion = self.prefix / 'bin/retrofetch-python'
        companion.chmod(0o750)
        marker = self.package / installer.PACKAGE_MARKER
        before = self.snapshot()
        self.frontend.write_bytes(b'\x7fELFupdated-native-fixture')
        replace = os.replace
        attempts = []
        cause = OSError('original publication cause')
        def fail(source, target):
            attempts.append(Path(target))
            if Path(target) == self.launcher:
                raise cause
            if Path(target) == marker and attempts.count(marker) == 2:
                raise OSError('rollback marker failure')
            return replace(source, target)
        with patch('retrofetch.native_install.os.replace', side_effect=fail):
            with self.assertRaises(installer.InstallationError) as caught:
                install_native(self.prefix, self.frontend)
        self.assertIs(caught.exception.__cause__, cause)
        self.assertIn(str(cause), str(caught.exception))
        self.assertEqual(attempts.count(companion), 2)
        self.assertNotEqual(marker.read_bytes(), before[str(marker.relative_to(self.prefix))][0])
        self.assertEqual((companion.read_bytes(), stat.S_IMODE(companion.stat().st_mode)), before['bin/retrofetch-python'])
        recovery = list(self.prefix.rglob('original'))
        self.assertEqual(len(recovery), 1)
        self.assertIn(str(recovery[0]), str(caught.exception))
        self.assertEqual((recovery[0].read_bytes(), stat.S_IMODE(recovery[0].stat().st_mode)), before[str(marker.relative_to(self.prefix))])

    def test_symlink_and_concurrent_change_are_not_overwritten(self):
        from retrofetch.native_install import install_native
        companion = self.prefix / 'bin/retrofetch-python'
        companion.symlink_to(self.frontend)
        with self.assertRaises(installer.InstallationError):
            install_native(self.prefix, self.frontend)
        self.assertTrue(companion.is_symlink())
        companion.unlink()
        write = Path.write_bytes
        changed = False
        def change(path, data):
            nonlocal changed
            result = write(path, data)
            if '.native-install-' in str(path) and not changed:
                changed = True
                write(self.launcher, b'user concurrent edit')
            return result
        with patch('pathlib.Path.write_bytes', change):
            with self.assertRaisesRegex(installer.InstallationError, 'changed during native staging'):
                install_native(self.prefix, self.frontend)
        self.assertEqual(self.launcher.read_bytes(), b'user concurrent edit')
        self.assertFalse(companion.exists())
        self.assertFalse(list(self.prefix.rglob('.native-install-*')))

    def test_real_separate_filesystems_publish_without_cross_device_rename(self):
        from retrofetch.native_install import install_native
        shared = Path('/dev/shm')
        if not shared.is_dir() or shared.stat().st_dev == self.prefix.stat().st_dev:
            self.skipTest('two writable filesystems unavailable')
        try:
            remote = tempfile.TemporaryDirectory(prefix='retrofetch-test-', dir=shared)
        except OSError:
            self.skipTest('/dev/shm is not writable')
        with remote as directory:
            package = Path(directory)
            marker = package / installer.PACKAGE_MARKER
            marker.write_bytes((self.package / installer.PACKAGE_MARKER).read_bytes())
            with patch('retrofetch.installer._validate_targets', return_value=(self.launcher, package)):
                install_native(self.prefix, self.frontend)
            self.assertEqual(json.loads(marker.read_bytes())['version'], 2)
            self.assertEqual(self.launcher.read_bytes(), self.frontend.read_bytes())
            self.assertFalse(list(package.glob('.native-install-*')))
        self.assertFalse(list(self.prefix.rglob('.native-install-*')))

    def test_changed_native_launcher_rejected(self):
        from retrofetch.native_install import install_native
        install_native(self.prefix, self.frontend)
        self.launcher.write_bytes(b'\x7fELFchanged')
        with self.assertRaises(installer.InstallationError):
            installer._validate_targets(self.prefix)
