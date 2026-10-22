"""Profile publication rejects unsupported or changing inputs without replacement."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from retrofetch.native_profile import build_profile
from retrofetch.calibration import layout_contracts
import struct


class NativeProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        digest = hashlib.sha256(b'asset').hexdigest()
        row = {'id': 'art', 'name': 'Art', 'path': f'animations/{digest}.gif',
               'width': 160, 'height': 160, 'frames': 2, 'sha256': digest,
               'categories': ['retro'], 'aliases': []}
        (self.root / 'catalog.json').write_text(json.dumps({'version': 1, 'animations': [row]}))
        self.config = self.root / 'config/retrofetch/config.toml'
        self.config.parent.mkdir(parents=True)
        self.environment = mock.patch.dict(os.environ, {'HOME': str(self.root), 'PATH': '',
                'XDG_CONFIG_HOME': str(self.root / 'config'), 'XDG_STATE_HOME': str(self.root / 'state')}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_unsupported_config_preserves_destination(self):
        destination = self.root / 'native-runtime.v2.bin'
        destination.write_bytes(b'leave intact')
        self.config.write_text('size="5x5"')
        with self.assertRaisesRegex(ValueError, 'configured size'):
            build_profile(self.root, geometry=(80, 24, 8., 16.))
        self.assertEqual(destination.read_bytes(), b'leave intact')

    def test_unknown_destination_is_not_overwritten(self):
        destination = self.root / 'native-runtime.v2.bin'
        destination.write_bytes(b'leave intact')
        with mock.patch('retrofetch.runtime_cache.cached_capabilities', return_value=False):
            with self.assertRaisesRegex(ValueError, 'unrecognized native profile'):
                build_profile(self.root, geometry=(80, 24, 8., 16.))
        self.assertEqual(destination.read_bytes(), b'leave intact')

    def test_mid_preparation_config_change_is_rejected(self):
        self.config.write_text('include=["retro"]')
        def change():
            self.config.write_text('exclude=["retro"]')
            return False
        with mock.patch('retrofetch.runtime_cache.cached_capabilities', side_effect=change):
            with self.assertRaisesRegex(ValueError, 'inputs changed'):
                build_profile(self.root, geometry=(80, 24, 8., 16.))
        self.assertFalse((self.root / 'native-runtime.v2.bin').exists())

    def test_unsupported_capability_can_prepare_animation_only_filters(self):
        self.config.write_text('include=["retro"]')
        with mock.patch('retrofetch.runtime_cache.cached_capabilities', return_value=False):
            report = build_profile(self.root, geometry=(80, 24, 8., 16.))
        self.assertEqual((report['eligible'], report['supported']), (1, False))
        data = Path(report['profile']).read_bytes()
        self.assertEqual(hashlib.sha256(data[:-32]).digest(), data[-32:])

    def test_v2_layout_header_and_candidates_are_geometry_independent(self):
        old = self.root / 'native-runtime.v1.bin'
        old.write_bytes(b'legacy untouched')
        contracts = (3, 7, [(1, 31, 112), (0, 29, 0)])
        with mock.patch('retrofetch.runtime_cache.cached_capabilities', return_value=True), \
                mock.patch('retrofetch.calibration._config_files', return_value=[]), \
                mock.patch('retrofetch.calibration.layout_contracts', return_value=contracts):
            first = build_profile(self.root, geometry=(265, 62, 7., 16.))
            data = Path(first['profile']).read_bytes()
            build_profile(self.root, geometry=(129, 40, 9., 18.))
        self.assertEqual(Path(first['profile']).read_bytes(), data)
        self.assertEqual(old.read_bytes(), b'legacy untouched')
        header = struct.unpack_from('<8sIIIIIIddIi', data)
        self.assertEqual(header[:2], (b'RTFXNAT2', 2))
        self.assertEqual(header[5:], (2, 3, 0., 0., 1, 7))
        position = 56
        for _ in range(header[2]):
            for _ in range(2):
                length, = struct.unpack_from('<I', data, position)
                position += 4 + length
        for _ in range(header[3]):
            length, = struct.unpack_from('<I', data, position)
            position += 4 + length + 73
        self.assertEqual([struct.unpack_from('<III', data, position + i * 12)
                          for i in range(2)], contracts[2])
        self.assertEqual(struct.unpack_from('<I', data, position + 24), (0,))
        self.assertEqual(hashlib.sha256(data[:-32]).digest(), data[-32:])

    def test_nonfinite_geometry_is_rejected(self):
        for geometry in ((80, 24, float('inf'), 16.),
                         (80, 24, 8., float('nan'))):
            with self.subTest(geometry=geometry), self.assertRaisesRegex(ValueError, 'geometry'):
                build_profile(self.root, geometry=geometry)
        self.assertFalse((self.root / 'native-runtime.v2.bin').exists())

    def test_layout_contracts_keep_matching_history_in_newest_order(self):
        config = self.root / 'fastfetch.jsonc'
        config.write_text('{"logo":{"position":"auto","padding":{"left":2,"right":5}}}')
        key = {'files': [[str(config), 'digest']]}
        profiles = [dict(key=key, mode='fixed-height', reference_rows=29, information_columns=None),
                    dict(key={'files': []}, mode='fixed-height', reference_rows=99, information_columns=None),
                    dict(key=key, mode='width-sensitive', reference_rows=31, information_columns=112)]
        with mock.patch('retrofetch.calibration._read_state', return_value=({'version': 2, 'profiles': profiles}, b'')), \
                mock.patch('retrofetch.calibration._key', return_value=key):
            self.assertEqual(layout_contracts(), (3, 7, [(1, 31, 112), (0, 29, 0)]))

    def test_missing_or_unusable_layout_has_no_contracts(self):
        for state in (None, {'version': 1}, {'version': 2, 'profiles': []}):
            with self.subTest(state=state), mock.patch('retrofetch.calibration._read_state', return_value=(state, None)):
                self.assertEqual(layout_contracts(), (0, 0, []))
        for error in (OSError('missing'), ValueError('invalid'), TypeError('invalid')):
            with self.subTest(error=error), mock.patch('retrofetch.calibration._read_state', side_effect=error):
                self.assertEqual(layout_contracts(), (0, 0, []))

    def test_top_position_is_preserved_in_contracts(self):
        config = self.root / 'fastfetch.jsonc'
        config.write_text('{"logo":{"position":"top"}}')
        key = {'files': [[str(config), 'digest']]}
        document = {'version': 2, 'profiles': [dict(key=key, mode='fixed-height', reference_rows=20, information_columns=None)]}
        with mock.patch('retrofetch.calibration._read_state', return_value=(document, b'')), \
                mock.patch('retrofetch.calibration._key', return_value=key):
            self.assertEqual(layout_contracts(), (2, 4, [(0, 20, 0)]))

    def test_profile_snapshots_historical_layout_configuration(self):
        config = self.root / 'historical.jsonc'
        config.write_text('{"logo":{"position":"right"}}')
        from retrofetch.calibration import _state_file
        state = _state_file()
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({'owner': 'retrofetch-layout', 'version': 2, 'profiles': [
            dict(key={'files': [[str(config), None]]}, mode='fixed-height',
                 reference_rows=20, information_columns=None)]}))
        def change():
            config.write_text('{"logo":{"position":"top"}}')
            return (1, 4, [(0, 20, 0)])
        with mock.patch('retrofetch.runtime_cache.cached_capabilities', return_value=True), \
                mock.patch('retrofetch.calibration._config_files', return_value=[]), \
                mock.patch('retrofetch.calibration.layout_contracts', side_effect=change):
            with self.assertRaisesRegex(ValueError, 'inputs changed'):
                build_profile(self.root, geometry=(80, 24, 8., 16.))
        self.assertFalse((self.root / 'native-runtime.v2.bin').exists())

    def test_divergent_historical_logo_options_are_not_silently_discarded(self):
        configs = [self.root / 'left.jsonc', self.root / 'right.jsonc']
        for config, position in zip(configs, ('left', 'right')):
            config.write_text(json.dumps({'logo': {'position': position}}))
        profiles = [dict(key={'files': [[str(config), 'digest']]}, mode='fixed-height',
                         reference_rows=20, information_columns=None) for config in configs]
        def key_for_files(config, files, **kwargs):
            return {'files': [[files[0], 'digest']]}
        with mock.patch('retrofetch.calibration._read_state', return_value=({'version': 2, 'profiles': profiles}, b'')), \
                mock.patch('retrofetch.calibration._key', side_effect=key_for_files):
            with self.assertRaisesRegex(ValueError, 'differing historical'):
                layout_contracts()

    def test_unparseable_matching_layout_retains_missing_layout_behavior(self):
        config = self.root / 'invalid.jsonc'
        config.write_text('{ invalid')
        key = {'files': [[str(config), 'digest']]}
        document = {'version': 2, 'profiles': [dict(key=key, mode='fixed-height', reference_rows=20, information_columns=None)]}
        with mock.patch('retrofetch.calibration._read_state', return_value=(document, b'')), \
                mock.patch('retrofetch.calibration._key', return_value=key):
            self.assertEqual(layout_contracts(), (0, 0, []))

    def test_concurrent_destination_change_is_preserved(self):
        destination = self.root / 'native-runtime.v2.bin'
        create = tempfile.mkstemp
        def change(*args, **kwargs):
            destination.write_bytes(b'concurrent owner')
            return create(*args, **kwargs)
        with mock.patch('retrofetch.runtime_cache.cached_capabilities', return_value=False), \
                mock.patch('retrofetch.native_profile.tempfile.mkstemp', side_effect=change):
            with self.assertRaisesRegex(ValueError, 'changed during preparation'):
                build_profile(self.root, geometry=(80, 24, 8., 16.))
        self.assertEqual(destination.read_bytes(), b'concurrent owner')
        self.assertFalse(list(self.root.glob('.native-profile-*')))

    def test_excessive_historical_dependencies_are_rejected_before_publication(self):
        from retrofetch.calibration import _state_file
        state = _state_file()
        state.parent.mkdir(parents=True)
        profiles = [dict(key={'files': [[str(self.root / f'config-{i}-{j}'), None] for j in range(64)]},
                         mode='fixed-height', reference_rows=20, information_columns=None)
                    for i in range(2)]
        state.write_text(json.dumps({'owner': 'retrofetch-layout', 'version': 2, 'profiles': profiles}))
        with mock.patch('retrofetch.runtime_cache.cached_capabilities', return_value=True), \
                mock.patch('retrofetch.calibration._config_files', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'dependency'):
                build_profile(self.root, geometry=(80, 24, 8., 16.))
        self.assertFalse((self.root / 'native-runtime.v2.bin').exists())
