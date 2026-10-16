"""Installer checks never install into the real user's home."""

from pathlib import Path
import fcntl
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from retrofetch.installer import InstallationError, check_dependencies, install


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prefix = self.root / "local $(touch injected)"
        self.data = self.root / "data"
        self.source = self.root / "source"
        self.source.mkdir()
        self.tools = patch("retrofetch.installer.check_dependencies")
        self.tools.start()
        self.addCleanup(self.tools.stop)

    def test_install_spaces_repeat_and_untouched_config(self):
        config = self.root / "config/fastfetch.jsonc"
        config.parent.mkdir()
        config.write_text("do not change")
        unrelated = self.prefix / "lib/unrelated"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("keep")
        with patch("retrofetch.installer.prepare_library", return_value={"selected": 1}) as prepare:
            report = install(self.prefix, self.data, source=self.source)
            install(self.prefix, self.data, source=self.source)
        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(report["library"], {"selected": 1})
        self.assertEqual(config.read_text(), "do not change")
        self.assertEqual(unrelated.read_text(), "keep")
        launcher = self.prefix / "bin/retrofetch"
        result = subprocess.run([str(launcher), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Fastfetch", result.stdout)
        self.assertFalse((self.root / "injected").exists())

    def test_unknown_existing_launcher_preserved_before_import(self):
        launcher = self.prefix / "bin/retrofetch"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\necho private\n")
        with patch("retrofetch.installer.prepare_library") as prepare, self.assertRaises(InstallationError):
            install(self.prefix, self.data, source=self.source)
        prepare.assert_not_called()
        self.assertIn("private", launcher.read_text())

    def test_import_failure_does_not_publish_launcher(self):
        with patch("retrofetch.installer.prepare_library", side_effect=ValueError("bad library")), self.assertRaises(ValueError):
            install(self.prefix, self.data, source=self.source)
        self.assertFalse((self.prefix / "bin/retrofetch").exists())

    def test_acquisition_only_without_local_source(self):
        with patch("retrofetch.installer.acquire_source", return_value=self.source) as acquire, \
             patch("retrofetch.installer.prepare_library", return_value={}):
            install(self.prefix, self.data, cache_dir=self.root / "cache")
        acquire.assert_called_once_with(self.root / "cache", progress=None)

    def test_missing_dependency_has_no_install_effects(self):
        with patch("retrofetch.installer.check_dependencies", side_effect=InstallationError("missing kitten")), \
             self.assertRaises(InstallationError):
            install(self.prefix, self.data)
        self.assertFalse(self.prefix.exists())
        self.assertFalse(self.data.exists())

    def test_symlink_package_destination_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        package = self.prefix / "lib/retrofetch"
        package.parent.mkdir(parents=True)
        package.symlink_to(outside, target_is_directory=True)
        with patch("retrofetch.installer.prepare_library") as prepare, self.assertRaises(InstallationError):
            install(self.prefix, self.data, source=self.source)
        prepare.assert_not_called()
        self.assertEqual(list(outside.iterdir()), [])

    def test_failed_launcher_publication_restores_managed_package(self):
        with patch("retrofetch.installer.prepare_library", return_value={}):
            install(self.prefix, self.data, source=self.source)
            launcher = self.prefix / "bin/retrofetch"
            before = launcher.read_bytes()
            package = self.prefix / "lib/retrofetch/retrofetch/__init__.py"
            before_package = package.read_bytes()
            with patch("retrofetch.installer.os.replace", side_effect=OSError("publication failed")), self.assertRaises(OSError):
                install(self.prefix, self.data, source=self.source)
        self.assertEqual(launcher.read_bytes(), before)
        self.assertEqual(package.read_bytes(), before_package)

    def test_concurrent_prefix_install_is_rejected(self):
        self.prefix.mkdir()
        with (self.prefix / ".retrofetch-install.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch("retrofetch.installer.prepare_library") as prepare, self.assertRaises(InstallationError):
                install(self.prefix, self.data, source=self.source)
        prepare.assert_not_called()

    def test_interrupted_upgrade_restores_managed_package(self):
        for phase in ("package", "launcher"):
            with self.subTest(phase=phase), patch("retrofetch.installer.prepare_library", return_value={}):
                install(self.prefix, self.data, source=self.source)
                launcher = self.prefix / "bin/retrofetch"
                package = self.prefix / "lib/retrofetch/retrofetch/__init__.py"
                before = (launcher.read_bytes(), package.read_bytes())
                original_rename = Path.rename

                def interrupt_new_package(path, target):
                    if path.name == "package":
                        raise KeyboardInterrupt()
                    return original_rename(path, target)

                failure = (patch.object(Path, "rename", interrupt_new_package)
                           if phase == "package" else
                           patch("retrofetch.installer.os.replace", side_effect=KeyboardInterrupt()))
                with failure, self.assertRaises(KeyboardInterrupt):
                    install(self.prefix, self.data, source=self.source)
                self.assertEqual((launcher.read_bytes(), package.read_bytes()), before)
                result = subprocess.run([str(launcher), "--help"], capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_dependency_capability_checks_without_running_graphics(self):
        with patch("retrofetch.installer.shutil.which", return_value=None), self.assertRaises(InstallationError):
            check_dependencies()
        with patch("retrofetch.installer.shutil.which", return_value="/mock/tool"), \
             patch("retrofetch.installer.supports_animation", return_value=False), self.assertRaises(InstallationError):
            check_dependencies()

    @unittest.skipUnless(shutil.which("bash"), "Bash is unavailable")
    def test_opt_in_bash_startup_is_guarded(self):
        binary = self.root / "bin/retrofetch"
        binary.parent.mkdir()
        binary.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$RETROFETCH_TEST_LOG"\n')
        binary.chmod(0o755)
        log = self.root / "calls"
        environment = {
            "PATH": str(binary.parent) + os.pathsep + os.environ.get("PATH", "/usr/bin"),
            "HOME": str(self.root), "TERM": "xterm-kitty", "RETROFETCH_TEST_LOG": str(log),
            "STARTUP_FILE": str(Path("examples/startup.bash").resolve()),
        }
        for interactive, overrides, expected in ((False, {}, False), (True, {"TERM": "xterm"}, False),
                                                  (True, {"TMUX": "active"}, False), (True, {"STY": "active"}, False),
                                                  (True, {}, True)):
            log.unlink(missing_ok=True)
            result = subprocess.run([shutil.which("bash"), "--noprofile", "--norc", "-ic" if interactive else "-c", '. "$STARTUP_FILE"'],
                                    env={**environment, **overrides}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(log.exists(), expected)
            if expected:
                self.assertEqual(log.read_text(), "--startup\n")


if __name__ == "__main__":
    unittest.main()
