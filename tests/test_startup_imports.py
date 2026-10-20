"""Exercise startup dependency boundaries in fresh interpreters."""

import os
import subprocess
import sys
import unittest


class StartupImportTests(unittest.TestCase):
    def run_fresh(self, script):
        result = subprocess.run([sys.executable, '-c', script], capture_output=True,
                                text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_import_defers_runtime_dependencies(self):
        self.run_fresh("""
import sys
import retrofetch.cli
for name in ('retrofetch.catalog', 'retrofetch.config', 'retrofetch.rendering',
             'retrofetch.selection_index', 'retrofetch.importer', 'dataclasses'):
    assert name not in sys.modules, name
""")

    def test_explicit_file_does_not_import_catalog_dependencies(self):
        self.run_fresh("""
import io
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from retrofetch.cli import main
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'animation.gif'
    path.write_bytes(b'GIF89a\\x01\\x00\\x01\\x00')
    output = io.StringIO()
    output.isatty = lambda: True
    with patch.dict(os.environ, {'TERM': 'xterm-kitty', 'XDG_CONFIG_HOME': directory}, clear=True), patch('sys.stdout', output), patch('retrofetch.cli.render'):
        assert main(['--file', str(path)]) == 0
    for name in ('retrofetch.catalog', 'retrofetch.selection_index',
                 'retrofetch.importer', 'retrofetch.normalization'):
        assert name not in sys.modules, name
""")
