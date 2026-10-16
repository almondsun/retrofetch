"""The optional selection accelerator is read-only and never trusted blindly."""

import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from retrofetch.catalog import Catalog
from retrofetch.selection_index import INDEX_NAME, build_index, select_random


class SelectionIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='retrofetch ?# spaces ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'animations').mkdir()
        self.rows = []
        for i in range(5):
            contents = f'GIF fixture {i}'.encode()
            digest = hashlib.sha256(contents).hexdigest()
            path = f'animations/{digest}.gif'
            (self.root / path).write_bytes(contents)
            self.rows.append(dict(id=f'd1/animals/cat{i}', name=f'Cat {i}', path=path,
                                  width=16, height=20, frames=2, sha256=digest,
                                  categories=['animals'], aliases=[f'cat{i}']))
        self.catalog = self.root / 'catalog.json'
        self.catalog.write_text(json.dumps(dict(version=1, animations=self.rows)))
        self.index = self.root / INDEX_NAME

    def test_uniform_ordinal_selection_matches_catalog_and_never_writes(self):
        report = build_index(self.root)
        self.assertEqual(report['entries'], len(self.rows))
        before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        catalog = Catalog.load(self.root)
        selected = []
        for seed in range(40):
            entry = select_random(self.root, random.Random(seed))
            expected = catalog.select(rng=random.Random(seed))
            self.assertEqual(entry, expected)
            selected.append(entry.id)
        self.assertEqual(set(selected), {r['id'] for r in self.rows})
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()})

    def test_fast_selection_does_not_load_or_parse_full_catalog(self):
        build_index(self.root)
        parse = json.loads
        with patch.object(Catalog, 'load', side_effect=AssertionError('slow path')), \
             patch('retrofetch.selection_index.json.loads', wraps=parse) as loads:
            self.assertIsNotNone(select_random(self.root))
        self.assertEqual(loads.call_count, 1)
        self.assertLess(len(loads.call_args.args[0]), self.catalog.stat().st_size)

    def test_same_size_and_mtime_catalog_change_invalidates_index(self):
        build_index(self.root)
        before = self.catalog.stat()
        payload = self.catalog.read_bytes().replace(b'Cat 0', b'Bat 0')
        self.catalog.write_bytes(payload)
        os.utime(self.catalog, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(self.catalog.stat().st_size, before.st_size)
        self.assertEqual(self.catalog.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertIsNone(select_random(self.root))

    def test_missing_stale_corrupt_and_unknown_indexes_fall_back(self):
        self.assertIsNone(select_random(self.root))
        build_index(self.root)
        original = self.catalog.read_bytes()
        self.catalog.write_bytes(original + b' ')
        self.assertIsNone(select_random(self.root))
        self.catalog.write_bytes(original)
        self.index.write_bytes(b'not sqlite')
        self.assertIsNone(select_random(self.root))
        before = self.index.read_bytes()
        with self.assertRaises(ValueError):
            build_index(self.root)
        self.assertEqual(self.index.read_bytes(), before)

    def test_malformed_rows_and_metadata_never_return_an_entry(self):
        for table, column, value in [('animations', 'payload', '{'),
                                     ('metadata', 'entry_count', 0),
                                     ('metadata', 'entry_count', 8),
                                     ('metadata', 'version', 9),
                                     ('metadata', 'catalog_sha256', '0' * 64)]:
            if self.index.exists():
                self.index.unlink()
            build_index(self.root)
            with sqlite3.connect(self.index) as connection:
                connection.execute(f'UPDATE {table} SET {column} = ?', (value,))
            self.assertIsNone(select_random(self.root), (table, column))

    def test_malicious_row_paths_rejected_without_touching_other_files(self):
        for path in ('../outside.gif', '/etc/passwd', 'animations/../escape.gif'):
            if self.index.exists():
                self.index.unlink()
            build_index(self.root)
            row = {**self.rows[0], 'path': path}
            with sqlite3.connect(self.index) as connection:
                connection.execute('UPDATE animations SET payload = ?', (json.dumps(row),))
            before = self.catalog.read_bytes()
            self.assertIsNone(select_random(self.root))
            self.assertEqual(self.catalog.read_bytes(), before)

    def test_missing_or_symlink_selected_file_is_not_returned(self):
        build_index(self.root)
        for row in self.rows:
            path = self.root / row['path']
            path.unlink()
            path.symlink_to('/etc/passwd')
        self.assertIsNone(select_random(self.root))
        for row in self.rows:
            (self.root / row['path']).unlink()
        self.assertIsNone(select_random(self.root))

    def test_index_symlink_and_unrelated_sqlite_are_not_replaced(self):
        outside = self.root / 'outside'
        outside.write_bytes(b'preserve')
        self.index.symlink_to(outside)
        self.assertIsNone(select_random(self.root))
        with self.assertRaises(ValueError):
            build_index(self.root)
        self.assertEqual(outside.read_bytes(), b'preserve')
        self.index.unlink()
        with sqlite3.connect(self.index) as connection:
            connection.execute('CREATE TABLE private (secret TEXT)')
        before = self.index.read_bytes()
        with self.assertRaises(ValueError):
            build_index(self.root)
        self.assertEqual(self.index.read_bytes(), before)

    def test_failed_rebuild_keeps_previous_index(self):
        build_index(self.root)
        before = self.index.read_bytes()
        with patch('retrofetch.selection_index.os.replace', side_effect=OSError('interrupted')), self.assertRaises(OSError):
            build_index(self.root)
        self.assertEqual(self.index.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['animations', 'catalog.json', INDEX_NAME])

    def test_empty_or_invalid_catalog_does_not_publish_index(self):
        for rows in ([], [{**self.rows[0], 'width': True}], [self.rows[0], self.rows[0]]):
            self.catalog.write_text(json.dumps(dict(version=1, animations=rows)))
            with self.assertRaises(ValueError):
                build_index(self.root)
            self.assertFalse(self.index.exists())


if __name__ == '__main__':
    unittest.main()
