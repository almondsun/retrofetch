"""Catalog validation and selection are read-only."""

import hashlib
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from retrofetch.catalog import Catalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'animations').mkdir()
        self.digest = hashlib.sha256(b'gif').hexdigest()
        self.file = self.root / 'animations' / (self.digest + '.gif')
        self.file.write_bytes(b'gif')
        self.rows = [self.row('disc/animals/cat', 'Cat', ['animals/cats'], ['old/cat']),
                     self.row('disc/space/cat', 'Cat', ['space'], []),
                     self.row('disc/animals/dog', 'Dog', ['animals/dogs'], [])]
        self.write()

    def row(self, identity, name, categories, aliases):
        return dict(id=identity, name=name, categories=categories, aliases=aliases,
                    path='animations/' + self.digest + '.gif', width=10,
                    height=20, frames=2, sha256=self.digest)

    def write(self):
        self.manifest = self.root / 'catalog.json'
        self.manifest.write_text(json.dumps(dict(version=1, animations=self.rows)))

    def test_filters_search_and_categories(self):
        catalog = Catalog.load(self.root)
        self.assertEqual([e.name for e in catalog.filtered(['ANIMALS'], ['*/cats'])], ['Dog'])
        self.assertEqual(len(catalog.search('CAT')), 2)
        self.assertEqual(len(catalog.search('old/')), 1)
        self.assertEqual(len(catalog.search('space')), 1)
        self.assertEqual(catalog.categories(), ['animals/cats', 'animals/dogs', 'space'])

    def test_named_selection_and_random_injection(self):
        catalog = Catalog.load(self.root)
        self.assertEqual(catalog.select('OLD/CAT').id, self.rows[0]['id'])
        self.assertEqual(catalog.select('Dog').name, 'Dog')
        self.assertEqual(catalog.select(rng=random.Random(4)), random.Random(4).choice(catalog.entries))
        for name in ['Cat', 'missing']:
            with self.assertRaises(ValueError):
                catalog.select(name)
        with self.assertRaises(ValueError):
            catalog.select('old/cat', exclude=['animals'])
        with self.assertRaises(ValueError):
            catalog.select(include=['none'])

    def test_canonical_ids_precede_reciprocal_aliases(self):
        first, second = self.rows[:2]
        first['aliases'] = [second['id']]
        second['aliases'] = [first['id']]
        self.write()
        catalog = Catalog.load(self.root)
        self.assertEqual(catalog.select(first['id']).id, first['id'])
        self.assertEqual(catalog.select(second['id']).id, second['id'])
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            catalog.select('Cat')
        # Filters apply before canonical-ID and alias precedence.
        self.assertEqual(catalog.select(first['id'], include=['space']).id, second['id'])

    def test_missing_file_checked_only_when_selected(self):
        catalog = Catalog.load(self.root)
        self.file.unlink()
        with self.assertRaises(ValueError):
            catalog.select('Dog')

    def test_schema_rejections_preserve_bytes(self):
        for key, value in [('width', True), ('height', 0), ('frames', 1),
                           ('sha256', 'bad'), ('aliases', [3]), ('categories', 'space'),
                           ('path', '../escape.gif'), ('path', '/tmp/escape.gif'),
                           ('path', 'animations/other.gif')]:
            with self.subTest(key=key, value=value):
                original = self.rows[0][key]
                self.rows[0][key] = value
                self.write()
                before = self.manifest.read_bytes(), self.file.read_bytes()
                with self.assertRaises(ValueError):
                    Catalog.load(self.root)
                self.assertEqual(before, (self.manifest.read_bytes(), self.file.read_bytes()))
                self.rows[0][key] = original
        self.rows.append(self.rows[0])
        self.write()
        with self.assertRaises(ValueError):
            Catalog.load(self.root)

    def test_invalid_manifest_and_symlink_escape(self):
        for content in ['{', '{"version":true,"animations":[]}', '{"version":2,"animations":[]}', '[]']:
            self.manifest.write_text(content)
            with self.assertRaises(ValueError):
                Catalog.load(self.root)
        self.write()
        self.file.unlink()
        self.file.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            Catalog.load(self.root).select('Dog')

    def test_load_does_not_resolve_unselected_asset_paths(self):
        original = Path.resolve
        resolved = []

        def track(path, *args, **kwargs):
            resolved.append(path)
            return original(path, *args, **kwargs)

        with patch.object(Path, 'resolve', track):
            catalog = Catalog.load(self.root)
        self.assertEqual(resolved, [self.root])
        self.assertEqual(catalog.select('Dog').path, self.file)

    def test_selection_rejects_in_root_asset_and_directory_symlinks(self):
        catalog = Catalog.load(self.root)
        real = self.root / 'original.gif'
        self.file.rename(real)
        self.file.symlink_to(real)
        with self.assertRaises(ValueError):
            catalog.select('Dog')
        self.file.unlink()
        real.rename(self.file)
        (self.root / 'animations').rename(self.root / 'originals')
        (self.root / 'animations').symlink_to(self.root / 'originals', target_is_directory=True)
        with self.assertRaises(ValueError):
            catalog.select('Dog')
