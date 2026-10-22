"""Installer checks never install into the real user's home."""

from pathlib import Path
import fcntl
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from retrofetch.installer import InstallationError, check_dependencies, install, main, _check_space
import hashlib
import io
import json
from types import SimpleNamespace


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prefix = self.root / "local $(touch injected)"
        self.data = self.root / "data"
        self.source = self.root / "source"
        self.source.mkdir()
        self.runtime = self.root / "state/runtime.json"
        for target, replacement in (
            ("retrofetch.installer._runtime_path", lambda: self.runtime),
            ("retrofetch.installer._stage_capabilities", lambda path: path.write_text('{"owner":"retrofetch-runtime","version":1,"supported":true}')),
        ):
            mocked = patch(target, replacement)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.tools = patch("retrofetch.installer.check_dependencies")
        self.tools.start()
        self.addCleanup(self.tools.stop)

    def test_launcher_uses_absolute_interpreter_and_ignores_path_shims(self):
        import sys
        with patch("retrofetch.installer.prepare_library", return_value={}):
            install(self.prefix, self.data, source=self.source)
        launcher = self.prefix / "bin/retrofetch"
        self.assertEqual(launcher.read_text().splitlines()[0], "#!" + sys.executable)
        shim = self.root / "python3"
        shim.write_text("#!/bin/sh\nexit 91\n")
        shim.chmod(0o755)
        result = subprocess.run([str(launcher), "--help"], capture_output=True,
                                env={**os.environ, "PATH": str(self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_preserves_absolute_symlink_and_rejects_bad_interpreters(self):
        import sys
        from retrofetch.python_launcher import launcher_text
        entry = self.root / "python-stable"
        entry.symlink_to(sys.executable)
        self.assertTrue(launcher_text(self.prefix / "lib/retrofetch", interpreter=entry)
                        .startswith("#!" + str(entry) + "\n"))
        for invalid in ("python3", self.root / "missing", str(entry) + "\ninvalid"):
            with self.subTest(interpreter=invalid), self.assertRaises(ValueError):
                launcher_text(self.prefix, interpreter=invalid)
        old = self.root / "old-python"
        old.write_text('#!/bin/sh\nprintf "[3, 10]"\n')
        old.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "3.11"):
            launcher_text(self.prefix, interpreter=old)

    def test_download_requires_explicit_acceptance_before_network(self):
        with patch("retrofetch.installer.acquire_collections") as acquire, self.assertRaises(InstallationError):
            install(self.prefix, self.data, cache_dir=self.root / "cache")
        acquire.assert_not_called()
        self.assertFalse(self.prefix.exists())

    def test_failed_publication_restores_library_and_runtime(self):
        self.data.mkdir()
        (self.data / "old").write_text("keep")
        (self.data / "catalog.json").write_text('{"version":1,"animations":[]}')
        with patch("retrofetch.installer.prepare_library", return_value={}), \
             patch("retrofetch.installer.os.replace", side_effect=OSError("failed")), self.assertRaises(OSError):
            install(self.prefix, self.data, source=self.source)
        self.assertEqual((self.data / "old").read_text(), "keep")

    def test_noninteractive_cli_refuses_download(self):
        with patch("sys.stdin.isatty", return_value=False), patch("sys.stderr", new_callable=io.StringIO), \
             patch("retrofetch.installer.acquire_collections") as acquire:
            code = main(["--prefix", str(self.prefix), "--data-dir", str(self.data)])
        self.assertEqual(code, 1)
        acquire.assert_not_called()
        self.assertFalse(self.prefix.exists())

    def test_space_check_combines_same_filesystem_budgets(self):
        with patch("retrofetch.installer.shutil.disk_usage", return_value=SimpleNamespace(free=64 * 1024**3)), \
             self.assertRaises(InstallationError):
            _check_space(self.root / "cache", self.data)
        with patch("retrofetch.installer.shutil.disk_usage", return_value=SimpleNamespace(free=65 * 1024**3)) as usage:
            _check_space(self.root / "cache", self.data)
        self.assertEqual(usage.call_count, 1)

    def test_symlink_ancestor_is_rejected(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        with patch("retrofetch.installer.prepare_library") as prepare, self.assertRaises(InstallationError):
            install(link / "local", self.data, source=self.source)
        prepare.assert_not_called()
        self.assertEqual(list(real.iterdir()), [])

    def test_unmanaged_library_is_preserved(self):
        self.data.mkdir()
        (self.data / "private").write_text("keep")
        with patch("retrofetch.installer.prepare_library") as prepare, self.assertRaises(InstallationError):
            install(self.prefix, self.data, source=self.source)
        prepare.assert_not_called()
        self.assertEqual((self.data / "private").read_text(), "keep")

    def test_runtime_publication_failure_rolls_back_all_outputs(self):
        with patch("retrofetch.installer.prepare_library", return_value={}):
            install(self.prefix, self.data, source=self.source)
            (self.data / "catalog.json").write_text('{"version":1,"animations":[]}')
            (self.data / "sentinel").write_text("original")
            before = (self.prefix / "bin/retrofetch").read_bytes(), self.runtime.read_bytes()
            rename = Path.rename
            def fail_state(path, target):
                if path.name == "runtime.json" and str(path.parent).find(".retrofetch-stage-") >= 0:
                    raise OSError("runtime publication failed")
                return rename(path, target)
            with patch.object(Path, "rename", fail_state), self.assertRaises(OSError):
                install(self.prefix, self.data, source=self.source)
        self.assertEqual((self.data / "sentinel").read_text(), "original")
        self.assertEqual(((self.prefix / "bin/retrofetch").read_bytes(), self.runtime.read_bytes()), before)

    def test_existing_launcher_remains_available_until_atomic_replacement(self):
        with patch("retrofetch.installer.prepare_library", return_value={}):
            install(self.prefix, self.data, source=self.source)
            (self.data / "catalog.json").write_text('{"version":1,"animations":[]}')
            launcher = self.prefix / "bin/retrofetch"
            before = launcher.read_bytes()
            replace = os.replace
            observed = []

            def check_launcher(candidate, target):
                if Path(target) == launcher:
                    self.assertTrue(launcher.is_file())
                    self.assertEqual(launcher.read_bytes(), before)
                    result = subprocess.run([str(launcher), "--help"], capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    observed.append(True)
                return replace(candidate, target)

            with patch("retrofetch.installer.os.replace", check_launcher):
                report = install(self.prefix, self.data, source=self.source)
            self.assertEqual(observed, [True])
            backups = [Path(path) / "retrofetch" for path in report["rollback_directories"]]
            self.assertTrue(any(path.is_file() and path.read_bytes() == before for path in backups))

    def test_legacy_catalog_passed_and_rollback_retained(self):
        self.data.mkdir()
        (self.data / "catalog.json").write_text('{"version":1,"animations":[]}')
        (self.data / "sentinel").write_text("keep")
        with patch("retrofetch.installer.prepare_library", return_value={}) as prepare:
            report = install(self.prefix, self.data, source=self.source)
        self.assertEqual(prepare.call_args.kwargs["legacy_catalog"], self.data / "catalog.json")
        self.assertTrue(any((Path(path) / "data/sentinel").is_file() for path in report["rollback_directories"]))

    def test_upgrade_retains_previous_content_addressed_assets(self):
        contents = b"previous validated GIF"
        digest = hashlib.sha256(contents).hexdigest()
        relative = "animations/" + digest + ".gif"
        (self.data / "animations").mkdir(parents=True)
        (self.data / relative).write_bytes(contents)
        (self.data / "catalog.json").write_text(json.dumps({"version": 1, "animations": [{
            "id": "legacy", "name": "legacy", "path": relative, "sha256": digest,
            "width": 1, "height": 1, "frames": 2, "categories": [], "aliases": []}]}))
        with patch("retrofetch.installer.prepare_library", return_value={}):
            install(self.prefix, self.data, source=self.source)
        self.assertEqual((self.data / relative).read_bytes(), contents)

    def test_capability_failure_leaves_existing_library_and_launcher(self):
        with patch("retrofetch.installer.prepare_library", return_value={}):
            install(self.prefix, self.data, source=self.source)
            (self.data / "catalog.json").write_text('{"version":1,"animations":[]}')
            before = self.runtime.read_bytes(), (self.prefix / "bin/retrofetch").read_bytes()
            with patch("retrofetch.installer._stage_capabilities", side_effect=ValueError("unsupported")), self.assertRaises(ValueError):
                install(self.prefix, self.data, source=self.source)
        self.assertTrue((self.data / "catalog.json").is_file())
        self.assertEqual((self.runtime.read_bytes(), (self.prefix / "bin/retrofetch").read_bytes()), before)

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
        with patch("retrofetch.installer.acquire_collections", return_value=self.source) as acquire, \
             patch("retrofetch.installer.prepare_library", return_value={}), \
             patch("retrofetch.installer._check_space"):
            install(self.prefix, self.data, cache_dir=self.root / "cache", accept_source_notices=True)
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
