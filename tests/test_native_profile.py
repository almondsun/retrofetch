"""Profile publication rejects unsupported or changing inputs without replacement."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from retrofetch.native_profile import build_profile


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
        destination = self.root / 'native-runtime.v1.bin'
        destination.write_bytes(b'leave intact')
        self.config.write_text('size="5x5"')
        with self.assertRaisesRegex(ValueError, 'configured size'):
            build_profile(self.root, geometry=(80, 24, 8., 16.))
        self.assertEqual(destination.read_bytes(), b'leave intact')

    def test_unknown_destination_is_not_overwritten(self):
        destination = self.root / 'native-runtime.v1.bin'
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
        self.assertFalse((self.root / 'native-runtime.v1.bin').exists())

    def test_unsupported_capability_can_prepare_animation_only_filters(self):
        self.config.write_text('include=["retro"]')
        with mock.patch('retrofetch.runtime_cache.cached_capabilities', return_value=False):
            report = build_profile(self.root, geometry=(80, 24, 8., 16.))
        self.assertEqual((report['eligible'], report['supported']), (1, False))
        data = Path(report['profile']).read_bytes()
        self.assertEqual(hashlib.sha256(data[:-32]).digest(), data[-32:])
