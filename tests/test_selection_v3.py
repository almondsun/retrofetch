"""Compatibility and corruption probes for the compact offline index."""
import json
import hashlib
import io
import os
from unittest.mock import patch
import random
import unittest

import test_selection_index as fixtures
from retrofetch.catalog import Catalog
from retrofetch.selection_v3 import HEADER, INDEX_NAME, build_index_v3, select_v3


class SelectionV3Tests(unittest.TestCase):
    setUp = fixtures.SelectionIndexTests.setUp
    def test_v3_parity(self):
        self.rows[0]['aliases'] = ['Straße', self.rows[1]['id']]
        self.rows[1]['categories'] = ['space/stars']
        self.rows[2]['name'] = self.rows[3]['name'] = 'Shared'
        self.catalog.write_text(json.dumps(dict(version=1, animations=self.rows)))
        build_index_v3(self.root)
        canonical = Catalog.load(self.root)
        for name, include in [('STRASSE', ()), (self.rows[1]['id'], ('animals',)),
                              ('Shared', ()), ('missing', ()), (None, ('space',)),
                              (None, ('missing',))]:
            for seed in range(3):
                args = dict(identifier=name, include=include)
                try:
                    expected = canonical.select(**args, rng=random.Random(seed))
                except ValueError as error:
                    with self.assertRaisesRegex(ValueError, '^' + str(error) + '$'):
                        select_v3(self.root, **args, rng=random.Random(seed))
                else:
                    self.assertEqual(select_v3(self.root, **args, rng=random.Random(seed)), expected)

    def test_v3_integrity_and_publication(self):
        path = self.root / INDEX_NAME
        build_index_v3(self.root)
        original = path.read_bytes()
        build_index_v3(self.root)
        self.assertEqual(original, path.read_bytes())
        for content in [original[:20], original[:-1], original + b'x', b'x' + original[1:]]:
            path.write_bytes(content)
            self.assertIsNone(select_v3(self.root, 'cat0'))
            with self.assertRaises(ValueError):
                build_index_v3(self.root)
            self.assertEqual(path.read_bytes(), content)
        path.unlink()
        target = self.root / 'untouched'
        target.write_bytes(original)
        path.symlink_to(target)
        self.assertIsNone(select_v3(self.root, 'cat0'))
        with self.assertRaises(ValueError):
            build_index_v3(self.root)
        self.assertEqual(target.read_bytes(), original)

    def test_v3_stale_catalog_and_selected_symlink(self):
        build_index_v3(self.root)
        self.catalog.write_bytes(self.catalog.read_bytes() + b' ')
        self.assertIsNone(select_v3(self.root, 'cat0'))
        build_index_v3(self.root)
        asset = self.root / self.rows[0]['path']
        asset.unlink()
        asset.symlink_to(self.root / self.rows[1]['path'])
        self.assertIsNone(select_v3(self.root, 'cat0'))

    def test_deep_metadata_falls_back_and_maintenance_preserves_bytes(self):
        build_index_v3(self.root)
        path = self.root / INDEX_NAME
        original = path.read_bytes()
        magic, version, count, digest, nr, nm, nk = HEADER.unpack_from(original)
        nested = b'[' * 100000 + b'0' + b']' * 100000
        body = (HEADER.pack(magic, version, count, digest, nr, len(nested), nk)
                + original[HEADER.size:HEADER.size + nr] + nested
                + original[HEADER.size + nr + nm:-32])
        path.write_bytes(body + hashlib.sha256(body).digest())
        config = self.root / 'settings.toml'
        config.write_text('')
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertIsNone(select_v3(self.root, 'cat0'))
        from retrofetch.cli import main
        output, errors = io.StringIO(), io.StringIO()
        output.isatty = lambda: True
        with patch.dict(os.environ, {'TERM': 'xterm-kitty', 'TMUX': '', 'STY': ''}), \
                patch('sys.stdout', output), patch('sys.stderr', errors), \
                patch('retrofetch.cli.image_size', return_value=(16, 20)), \
                patch('retrofetch.cli.render') as render:
            self.assertEqual(main(['--data-dir', str(self.root), '--config', str(config), 'cat0']), 0)
        self.assertEqual(render.call_args.args[0], self.root / self.rows[0]['path'])
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue(), '')
        with self.assertRaises(ValueError):
            build_index_v3(self.root)
        self.assertEqual({p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
