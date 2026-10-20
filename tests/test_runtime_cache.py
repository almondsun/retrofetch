"""Owned capability metadata is refreshed explicitly, never during rendering."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from retrofetch.runtime_cache import cached_capabilities, refresh_capabilities


class RuntimeCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / 'fastfetch'
        self.binary.write_bytes(b'build-one')
        self.state = self.root / 'state/runtime.json'
        self.which = patch('retrofetch.runtime_cache.shutil.which', return_value=str(self.binary))
        self.which.start()
        self.addCleanup(self.which.stop)

    def refresh(self, supported=True):
        with patch('retrofetch.rendering.supports_animation', return_value=supported) as probe:
            report = refresh_capabilities(state_file=self.state)
        probe.assert_called_once_with()
        return report

    def test_refresh_then_read_never_probes_or_writes(self):
        self.assertIsNone(cached_capabilities(state_file=self.state))
        self.assertFalse(self.state.exists())
        self.assertTrue(self.refresh()['supported'])
        before = self.state.read_bytes()
        with patch('retrofetch.rendering.supports_animation', side_effect=AssertionError('probe')):
            self.assertIs(cached_capabilities(state_file=self.state), True)
        self.assertEqual(self.state.read_bytes(), before)

    def test_changed_binary_invalidates_cache(self):
        self.refresh()
        self.binary.write_bytes(b'build-two')
        self.assertIsNone(cached_capabilities(state_file=self.state))

    def test_unsupported_is_cached_without_becoming_missing(self):
        self.assertFalse(self.refresh(False)['supported'])
        self.assertIs(cached_capabilities(state_file=self.state), False)

    def test_bad_or_unknown_metadata_is_not_trusted_or_overwritten(self):
        self.state.parent.mkdir()
        for value in ('[]', '{}', '{"owner":"other"}', 'broken'):
            with self.subTest(value=value):
                self.state.write_text(value)
                self.assertIsNone(cached_capabilities(state_file=self.state))
                with self.assertRaises(ValueError):
                    self.refresh()
                self.assertEqual(self.state.read_text(), value)

    def test_symlink_and_parent_symlink_are_refused(self):
        outside = self.root / 'outside'
        outside.mkdir()
        self.state.parent.symlink_to(outside, target_is_directory=True)
        self.assertIsNone(cached_capabilities(state_file=self.state))
        with self.assertRaises(ValueError):
            self.refresh()
        self.assertEqual(list(outside.iterdir()), [])

    def test_failed_probe_preserves_existing_metadata(self):
        self.refresh()
        before = self.state.read_bytes()
        with patch('retrofetch.rendering.supports_animation', side_effect=OSError('failed')):
            with self.assertRaises(OSError):
                refresh_capabilities(state_file=self.state)
        self.assertEqual(self.state.read_bytes(), before)

    def test_missing_executable_does_not_publish(self):
        with patch('retrofetch.runtime_cache.shutil.which', return_value=None):
            with self.assertRaises(ValueError):
                self.refresh()
        self.assertFalse(self.state.exists())

    def test_replaced_binary_during_probe_does_not_publish(self):
        def replace():
            self.binary.write_bytes(b'new-build')
            return True
        with patch('retrofetch.rendering.supports_animation', side_effect=replace):
            with self.assertRaises(ValueError):
                refresh_capabilities(state_file=self.state)
        self.assertFalse(self.state.exists())


class RuntimeRefreshTests(unittest.TestCase):
    def setUp(self):
        import hashlib
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        digest = hashlib.sha256(b'gif').hexdigest()
        row = dict(id='one', name='one', path=f'animations/{digest}.gif', sha256=digest,
                   width=160, height=160, frames=2, categories=[], aliases=[])
        (self.data / 'catalog.json').write_text(json.dumps({'version': 1, 'animations': [row]}))
        self.state = self.root / 'state/runtime.json'
        self.state.parent.mkdir()
        self.binary = self.root / 'fastfetch'
        self.binary.write_text('binary')
        self.which = patch('retrofetch.runtime_cache.shutil.which', return_value=str(self.binary))
        self.which.start()
        self.addCleanup(self.which.stop)

    def test_rejected_unknown_state_does_not_create_index(self):
        from retrofetch.runtime_cache import refresh_runtime
        self.state.write_text('{"owner":"unrelated"}')
        before = {p.name: p.read_bytes() for p in self.data.iterdir()}
        with self.assertRaises(ValueError):
            refresh_runtime(self.data, state_file=self.state)
        self.assertEqual({p.name: p.read_bytes() for p in self.data.iterdir()}, before)
        self.assertEqual(self.state.read_text(), '{"owner":"unrelated"}')

    def test_unknown_index_does_not_publish_capabilities(self):
        from retrofetch.runtime_cache import refresh_runtime
        index = self.data / 'selection.v2.idx'
        index.write_bytes(b'not-owned')
        with self.assertRaises(ValueError):
            refresh_runtime(self.data, state_file=self.state)
        self.assertEqual(index.read_bytes(), b'not-owned')
        self.assertFalse(self.state.exists())

    def test_failed_capability_publication_restores_prior_index(self):
        from retrofetch.runtime_cache import refresh_runtime
        from retrofetch.selection_index import build_index
        build_index(self.data)
        before = (self.data / 'selection.v2.idx').read_bytes()
        catalog = self.data / 'catalog.json'
        changed = json.loads(catalog.read_text())
        changed['animations'][0]['name'] = 'different candidate'
        catalog.write_text(json.dumps(changed))
        published = []
        real = os.replace
        def fail(source, destination):
            if Path(destination) == self.state:
                raise OSError('publication failed')
            if Path(destination) == self.data / 'selection.v2.idx':
                published.append(Path(source).read_bytes())
            return real(source, destination)
        with patch('retrofetch.rendering.supports_animation', return_value=True), \
                patch('retrofetch.runtime_cache.os.replace', side_effect=fail):
            with self.assertRaises(OSError):
                refresh_runtime(self.data, state_file=self.state)
        self.assertEqual((self.data / 'selection.v2.idx').read_bytes(), before)
        self.assertNotEqual(published[0], before)
        self.assertEqual(published[-1], before)
        self.assertFalse(self.state.exists())

    def test_success_publishes_both_outputs_and_reuses_owned_state(self):
        from retrofetch.runtime_cache import refresh_runtime
        with patch('retrofetch.rendering.supports_animation', return_value=True):
            report = refresh_runtime(self.data, state_file=self.state)
        self.assertEqual(report['index']['entries'], 1)
        self.assertEqual(report['index_v3']['entries'], 1)
        self.assertTrue((self.data / 'selection.v3.idx').is_file())
        self.assertTrue(report['capabilities']['supported'])
        self.assertEqual(Path(report['index']['index']), self.data / 'selection.v2.idx')
        self.assertEqual(Path(report['capabilities']['state_file']), self.state)
        self.assertTrue((self.data / 'selection.v2.idx').is_file())
