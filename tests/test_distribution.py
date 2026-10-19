"""Code-only release archive boundaries."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('distribution_check', Path(__file__).parents[1] / 'scripts/check_distribution.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


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
                archive.writestr('retrofetch/cli.py', '')
                archive.writestr('docs/installation.md', '')
            checker.check(path)
