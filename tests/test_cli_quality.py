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
        with patch('retrofetch.selection_index.select_indexed', create=True, return_value=SimpleNamespace(path=self.gif)) as select, \
                patch('retrofetch.cli.Catalog.load', side_effect=AssertionError('full catalog hot path')), \
                patch('retrofetch.cli.render') as render:
            self.assertEqual(self.invoke('--startup'), 0)
        select.assert_called_once_with(self.root, None, (), ())
        self.assertEqual(render.call_args.args[0], self.gif)

    def test_normalize_is_upfront_and_builds_index_without_terminal(self):
        self.output.isatty = lambda: False
        with patch('retrofetch.normalization.normalize_library', return_value={'successes': 1}) as normalize, \
                patch('retrofetch.selection_index.build_index', return_value={'entries': 1}) as build, \
                patch('retrofetch.runtime_cache.refresh_capabilities', return_value={'supported': True}):
            self.assertEqual(self.invoke('--normalize'), 0)
        self.assertEqual(normalize.call_args.args[0], self.root)
        build.assert_called_once_with(self.root)

    def test_calibration_accepts_fastfetch_and_size_without_rendering(self):
        with patch('retrofetch.calibration.calibrate_layout', return_value={'reference_rows': 29}) as calibrate, \
                patch('retrofetch.cli.render', side_effect=AssertionError('render during calibration')):
            self.assertEqual(self.invoke('--calibrate-layout', '--fastfetch-config', 'none', '--size', '12x6'), 0)
        calibrate.assert_called_once_with('none', (12, 6))

    def test_named_and_filtered_playback_uses_index(self):
        with patch('retrofetch.selection_index.select_indexed', create=True,
                   return_value=SimpleNamespace(path=self.gif)) as select, \
                patch('retrofetch.cli.Catalog.load', side_effect=AssertionError('full catalog hot path')), \
                patch('retrofetch.cli.render'):
            self.assertEqual(self.invoke('chosen', '--include', 'animals', '--exclude', 'birds'), 0)
        select.assert_called_once_with(self.root, 'chosen', ['animals'], ['birds'])

    def test_discovery_keeps_canonical_catalog(self):
        with patch('retrofetch.selection_index.select_indexed', create=True,
                   side_effect=AssertionError('indexed discovery')), \
                patch('retrofetch.cli.Catalog.load') as catalog:
            catalog.return_value.filtered.return_value = []
            self.assertEqual(self.invoke('--list', '--include', 'animals'), 0)
        catalog.assert_called_once_with(self.root)
        catalog.return_value.filtered.assert_called_once_with(['animals'], ())

    def test_index_infrastructure_failure_uses_canonical_selection(self):
        with patch('retrofetch.selection_index.select_indexed', create=True, return_value=None), \
                patch('retrofetch.cli.Catalog.load') as catalog, patch('retrofetch.cli.render'):
            catalog.return_value.select.return_value.path = self.gif
            self.assertEqual(self.invoke('--include', 'animals'), 0)
        catalog.return_value.select.assert_called_once_with(None, ['animals'], ())

    def test_index_semantic_error_does_not_fall_back(self):
        with patch('retrofetch.selection_index.select_indexed', create=True, side_effect=ValueError('Unknown animation: missing')), \
                patch('retrofetch.cli.Catalog.load', side_effect=AssertionError('semantic fallback')):
            self.assertEqual(self.invoke('missing'), 1)
        self.assertIn('Unknown animation: missing', self.errors.getvalue())

    def test_refresh_runtime_is_explicit_and_does_not_normalize(self):
        self.output.isatty = lambda: False
        with patch('retrofetch.runtime_cache.refresh_runtime', return_value={'index': {'entries': 1}, 'capabilities': {'supported': True}}) as refresh, \
                patch('retrofetch.normalization.normalize_library', side_effect=AssertionError('conversion')):
            self.assertEqual(self.invoke('--refresh-runtime'), 0)
        refresh.assert_called_once_with(self.root)

    def test_layout_mode_is_explicit_and_only_for_calibration(self):
        with patch('retrofetch.calibration.calibrate_layout', return_value={'reference_rows': 29}) as calibrate:
            self.assertEqual(self.invoke('--calibrate-layout', '--layout-mode', 'fixed-height'), 0)
        calibrate.assert_called_once_with(None, None, layout_mode='fixed-height')
        with self.assertRaises(SystemExit) as failure:
            self.invoke('--layout-mode', 'fixed-height', '--file', str(self.gif))
        self.assertEqual(failure.exception.code, 2)

    def test_cached_capability_skips_probes_but_still_renders(self):
        from retrofetch.rendering import render
        with patch('retrofetch.runtime_cache.cached_capabilities', return_value=True), \
                patch('retrofetch.rendering.supports_animation', side_effect=AssertionError('startup probes')), \
                patch('retrofetch.calibration.layout_padding', return_value=10), \
                patch('retrofetch.rendering.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            render(self.gif)
        self.assertEqual(run.call_count, 1)
        self.assertIn('--logo-animation-frame', run.call_args.args[0])

    def test_rejected_refresh_preserves_public_data_inventory(self):
        import hashlib
        import json
        digest = hashlib.sha256(self.gif.read_bytes()).hexdigest()
        row = dict(id='one', name='one', path=f'animations/{digest}.gif', sha256=digest,
                   width=160, height=160, frames=2, categories=[], aliases=[])
        (self.root / 'catalog.json').write_text(json.dumps({'version': 1, 'animations': [row]}))
        state = self.root / 'state/retrofetch/runtime.json'
        state.parent.mkdir(parents=True)
        state.write_text('{"owner":"unrelated"}')
        self.env['XDG_STATE_HOME'] = str(self.root / 'state')
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.invoke('--refresh-runtime'), 1)
        self.assertEqual({str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        self.assertIn('Unrecognized runtime capability state', self.errors.getvalue())
