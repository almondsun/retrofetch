"""Code-only release archive boundaries."""
import importlib.util
from pathlib import Path
import io
import tarfile
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('distribution_check', Path(__file__).parents[1] / 'scripts/check_distribution.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


REQUIRED_MODULES = {'retrofetch/cli.py', 'retrofetch/native_install.py',
                    'retrofetch/native_profile.py', 'retrofetch/selection_v3.py'}


class DistributionTests(unittest.TestCase):
    def test_private_and_generated_paths_are_rejected(self):
        for name in ('tmp/inventory.jsonl', '.retrofetch-library-work/manifest.json',
                     'odd/tasks/internal.md', '.vscode/settings.json', 'recovery/report.json',
                     'source-archives/readme.txt'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'release.whl'
                with zipfile.ZipFile(path, 'w') as archive:
                    archive.writestr(name, '{}')
                with self.assertRaises(ValueError):
                    checker.check(path)

    def test_code_and_public_documentation_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'release.whl'
            with zipfile.ZipFile(path, 'w') as archive:
                for name in REQUIRED_MODULES:
                    archive.writestr(name, '')
                archive.writestr('docs/installation.md', '')
            checker.check(path)

    def test_missing_native_sdist_inputs_are_rejected(self):
        for missing in ('native/retrofetch.c', 'scripts/build_native.py'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'release.tar.gz'
                with tarfile.open(path, 'w:gz') as archive:
                    for name in {'native/retrofetch.c', 'scripts/build_native.py', *('src/' + name for name in REQUIRED_MODULES)} - {missing}:
                        entry = tarfile.TarInfo('release/' + name)
                        archive.addfile(entry, io.BytesIO(b''))
                with self.assertRaisesRegex(ValueError, 'missing required'):
                    checker.check(path)

    def test_missing_wheel_opt_in_modules_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'release.whl'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('retrofetch/cli.py', '')
            with self.assertRaisesRegex(ValueError, 'missing required'):
                checker.check(path)

    def test_wheels_reject_embedded_elf_even_without_native_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'release.whl'
            with zipfile.ZipFile(path, 'w') as archive:
                for name in REQUIRED_MODULES:
                    archive.writestr(name, '')
                archive.writestr('retrofetch/frontend', b'\x7fELFbinary')
            with self.assertRaisesRegex(ValueError, 'native binary'):
                checker.check(path)

    def test_complete_native_source_distribution_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'release.tar.gz'
            with tarfile.open(path, 'w:gz') as archive:
                for name in {'native/retrofetch.c', 'scripts/build_native.py', *('src/' + name for name in REQUIRED_MODULES)}:
                    archive.addfile(tarfile.TarInfo('release/' + name), io.BytesIO(b''))
            checker.check(path)
