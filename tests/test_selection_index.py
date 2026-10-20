"""The optional selection accelerator is read-only and never trusted blindly."""

import hashlib
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from retrofetch.catalog import Catalog
from retrofetch.selection_index import INDEX_NAME, build_index, select_random, select_indexed


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

    def legacy_index(self):
        path = self.root / 'selection.sqlite3'
        with sqlite3.connect(path) as connection:
            connection.execute('PRAGMA application_id = 0x52544658')
            connection.execute('CREATE TABLE metadata (version, catalog_sha256, entry_count)')
            connection.execute('INSERT INTO metadata VALUES (1, ?, ?)',
                               (hashlib.sha256(self.catalog.read_bytes()).hexdigest(), len(self.rows)))
            connection.execute('CREATE TABLE animations (ordinal INTEGER PRIMARY KEY, payload TEXT)')
            connection.executemany('INSERT INTO animations VALUES (?, ?)',
                                   [(i, json.dumps(row)) for i, row in enumerate(self.rows)])
        return path

    def rewrite_binary(self, *, rows=None, header=None, offsets=None):
        records = [json.dumps(row, separators=(',', ':')).encode() for row in
                   (self.rows if rows is None else rows)]
        positions = [0]
        for record in records:
            positions.append(positions[-1] + len(record))
        fields = [b'RTFXIDX2', 2, len(records),
                  hashlib.sha256(self.catalog.read_bytes()).digest(), positions[-1]]
        if header:
            for field, value in header.items():
                fields[field] = value
        body = (struct.pack('<8sII32sQ', *fields)
                + struct.pack('<' + 'Q' * len(positions), *(positions if offsets is None else offsets))
                + b''.join(records))
        self.index.write_bytes(body + hashlib.sha256(body).digest())

    def test_named_filtered_parity_and_semantic_errors(self):
        self.rows[0]['aliases'] = [self.rows[1]['id']]
        self.rows[1]['categories'] = ['space']
        self.rows[2]['name'] = self.rows[3]['name'] = 'Shared'
        self.catalog.write_text(json.dumps(dict(version=1, animations=self.rows)))
        build_index(self.root)
        catalog = Catalog.load(self.root)
        for identifier, include, exclude in [
                (None, ['animals'], []), ('CAT0', [], []),
                (self.rows[1]['id'], [], []),
                (self.rows[1]['id'], ['animals'], []),
                ('Shared', [], []), ('missing', [], []), (None, ['none'], [])]:
            for seed in range(5):
                arguments = dict(identifier=identifier, include=include, exclude=exclude)
                try:
                    expected = catalog.select(**arguments, rng=random.Random(seed))
                except ValueError as error:
                    with self.assertRaises(ValueError) as caught:
                        select_indexed(self.root, **arguments, rng=random.Random(seed))
                    self.assertEqual(str(caught.exception), str(error))
                else:
                    self.assertEqual(select_indexed(self.root, **arguments,
                                                   rng=random.Random(seed)), expected)

    def test_named_bulk_decode_and_only_selected_entry_construction(self):
        build_index(self.root)
        from retrofetch.catalog import _entry_from_row
        with patch('retrofetch.selection_index.json.loads', wraps=json.loads) as loads, \
             patch('retrofetch.selection_index._entry_from_row', wraps=_entry_from_row) as entries:
            self.assertEqual(select_indexed(self.root, 'cat3').id, self.rows[3]['id'])
        self.assertEqual(loads.call_count, 1)
        self.assertEqual(entries.call_count, 1)

    def test_invalid_unselected_metadata_falls_back(self):
        for change in ({'aliases': [False]}, {'categories': 'animals'}, {'width': True},
                       {'path': '../escape.gif'}, {'sha256': 'bad'}, {'id': ''}):
            rows = [dict(row) for row in self.rows]
            rows[-1].update(change)
            self.rewrite_binary(rows=rows)
            self.assertIsNone(select_indexed(self.root, 'cat0'), change)
        rows = [dict(row) for row in self.rows]
        rows[-1]['id'] = rows[0]['id'].upper()
        self.rewrite_binary(rows=rows)
        self.assertIsNone(select_indexed(self.root, 'cat0'))

    def test_named_integrity_and_selected_paths_fall_back_without_writes(self):
        build_index(self.root)
        original = self.catalog.read_bytes()
        self.catalog.write_bytes(original.replace(b'Cat 0', b'Bat 0'))
        self.assertIsNone(select_indexed(self.root, 'cat0'))
        self.catalog.write_bytes(original)
        selected = self.root / self.rows[0]['path']
        selected.unlink()
        self.assertIsNone(select_indexed(self.root, 'cat0'))
        selected.symlink_to('/etc/passwd')
        before = self.index.read_bytes(), self.catalog.read_bytes()
        self.assertIsNone(select_indexed(self.root, 'cat0'))
        self.assertEqual(before, (self.index.read_bytes(), self.catalog.read_bytes()))
        # Unselected missing or unsafe files do not prevent a valid selection.
        self.assertEqual(select_indexed(self.root, 'cat1').id, self.rows[1]['id'])

    def test_named_and_filtered_do_not_use_legacy(self):
        self.legacy_index()
        self.assertIsNone(select_indexed(self.root, 'cat0'))
        self.assertIsNone(select_indexed(self.root, include=['animals']))

    def test_binary_build_is_deterministic_and_preserves_legacy(self):
        legacy = self.legacy_index()
        old = legacy.read_bytes()
        report = build_index(self.root)
        self.assertEqual(Path(report['index']).name, 'selection.v2.idx')
        self.assertEqual(self.index.read_bytes()[:8], b'RTFXIDX2')
        first = self.index.read_bytes()
        build_index(self.root)
        self.assertEqual(first, self.index.read_bytes())
        self.assertEqual(old, legacy.read_bytes())

    def test_binary_runtime_does_not_import_sqlite(self):
        build_index(self.root)
        code = ('import sys; from pathlib import Path; '
                'from retrofetch.selection_index import select_random; '
                'assert select_random(Path(sys.argv[1])) is not None; '
                'assert "sqlite3" not in sys.modules; '
                'assert "tempfile" not in sys.modules')
        result = subprocess.run([sys.executable, '-c', code, str(self.root)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_only_when_binary_absent(self):
        legacy = self.legacy_index()
        self.assertIsNotNone(select_random(self.root))
        self.index.write_bytes(b'invalid v2')
        self.assertIsNone(select_random(self.root))
        self.assertTrue(legacy.exists())

    def test_checksum_truncation_and_rehashed_structure_rejected(self):
        build_index(self.root)
        original = self.index.read_bytes()
        for contents in (original[:-1], original + b'x', original[:-1] + bytes([original[-1] ^ 1])):
            self.index.write_bytes(contents)
            self.assertIsNone(select_random(self.root))
        for header in ({0: b'UNKNOWN!'}, {1: 3}, {2: 0}, {2: 1000001},
                       {3: bytes(32)}, {4: 0}):
            self.rewrite_binary(header=header)
            self.assertIsNone(select_random(self.root), header)
        for offsets in ([1, 2, 3, 4, 5, 6], [0, 0, 2, 3, 4, 5],
                        [0, 2, 1, 3, 4, 5], [0, 1, 2, 3, 4, 2**64 - 1]):
            self.rewrite_binary(offsets=offsets)
            self.assertIsNone(select_random(self.root), offsets)

    def test_binary_selected_schema_and_path_rejected_after_rehash(self):
        for change in ({'path': '../outside.gif'}, {'width': True}, {'frames': 1}):
            self.rewrite_binary(rows=[{**row, **change} for row in self.rows])
            self.assertIsNone(select_random(self.root))

    def test_bounded_index_and_record_rejected(self):
        build_index(self.root)
        with patch('retrofetch.selection_index.MAX_INDEX_BYTES', 32):
            self.assertIsNone(select_random(self.root))
        self.rewrite_binary(rows=[{**row, 'name': 'x' * (2 * 1024 * 1024)} for row in self.rows])
        self.assertIsNone(select_random(self.root))

    def test_nonregular_indexes_rejected_without_opening_fifo(self):
        self.index.mkdir()
        self.assertIsNone(select_random(self.root))
        with self.assertRaises(ValueError):
            build_index(self.root)
        self.index.rmdir()
        os.mkfifo(self.index)
        self.assertIsNone(select_random(self.root))
        with self.assertRaises(ValueError):
            build_index(self.root)

    def test_destination_created_during_build_is_preserved(self):
        make_temp = tempfile.mkstemp
        def create_unrelated(*args, **kwargs):
            self.index.write_bytes(b'unrelated concurrent file')
            return make_temp(*args, **kwargs)
        with patch('tempfile.mkstemp', side_effect=create_unrelated), self.assertRaises(ValueError):
            build_index(self.root)
        self.assertEqual(self.index.read_bytes(), b'unrelated concurrent file')
        self.assertFalse(list(self.root.glob('.selection-index-*')))

    def test_catalog_change_during_build_does_not_publish(self):
        contents = self.catalog.read_bytes()
        with patch.object(Path, 'read_bytes', side_effect=[contents, contents + b' ']), self.assertRaises(ValueError):
            build_index(self.root)
        self.assertFalse(self.index.exists())
        self.assertFalse(list(self.root.glob('.selection-index-*')))

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
            legacy = self.root / 'selection.sqlite3'
            legacy.unlink(missing_ok=True)
            self.legacy_index()
            with sqlite3.connect(legacy) as connection:
                connection.execute(f'UPDATE {table} SET {column} = ?', (value,))
            self.assertIsNone(select_random(self.root), (table, column))

    def test_malicious_row_paths_rejected_without_touching_other_files(self):
        for path in ('../outside.gif', '/etc/passwd', 'animations/../escape.gif'):
            legacy = self.root / 'selection.sqlite3'
            legacy.unlink(missing_ok=True)
            self.legacy_index()
            row = {**self.rows[0], 'path': path}
            with sqlite3.connect(legacy) as connection:
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
