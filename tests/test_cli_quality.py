import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from retrofetch.cli import main


class QualityCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gif = self.root / 'a.gif'
        self.gif.write_bytes(b'GIF89a\xa0\x00\xa0\x00')
        self.output = io.StringIO()
        self.output.isatty = lambda: True
        self.errors = io.StringIO()
        self.env = {'TERM': 'xterm-kitty', 'XDG_CONFIG_HOME': str(self.root / 'config')}

    def invoke(self, *args):
        with patch.dict(os.environ, self.env, clear=True), patch('sys.stdout', self.output), patch('sys.stderr', self.errors):
            return main(['--data-dir', str(self.root), *args])

    def test_default_random_uses_index_without_full_catalog(self):
        with patch('retrofetch.selection_index.select_random', return_value=SimpleNamespace(path=self.gif)) as select, \
                patch('retrofetch.cli.Catalog.load', side_effect=AssertionError('full catalog hot path')), \
                patch('retrofetch.cli.render') as render:
            self.assertEqual(self.invoke('--startup'), 0)
        select.assert_called_once_with(self.root)
        self.assertEqual(render.call_args.args[0], self.gif)

    def test_normalize_is_upfront_and_builds_index_without_terminal(self):
        self.output.isatty = lambda: False
        with patch('retrofetch.normalization.normalize_library', return_value={'successes': 1}) as normalize, \
                patch('retrofetch.selection_index.build_index', return_value={'entries': 1}) as build:
            self.assertEqual(self.invoke('--normalize'), 0)
        self.assertEqual(normalize.call_args.args[0], self.root)
        build.assert_called_once_with(self.root)

    def test_calibration_accepts_fastfetch_and_size_without_rendering(self):
        with patch('retrofetch.calibration.calibrate_layout', return_value={'reference_rows': 29}) as calibrate, \
                patch('retrofetch.cli.render', side_effect=AssertionError('render during calibration')):
            self.assertEqual(self.invoke('--calibrate-layout', '--fastfetch-config', 'none', '--size', '12x6'), 0)
        calibrate.assert_called_once_with('none', (12, 6))

    def test_filters_keep_existing_catalog_selection_path(self):
        with patch('retrofetch.selection_index.select_random', side_effect=AssertionError('index ignores filters')), \
                patch('retrofetch.cli.Catalog.load') as catalog, patch('retrofetch.cli.render'):
            catalog.return_value.select.return_value.path = self.gif
            self.assertEqual(self.invoke('--include', 'animals'), 0)
        catalog.return_value.select.assert_called_once_with(None, ['animals'], ())
