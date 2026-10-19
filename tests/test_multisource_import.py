import hashlib
from collections import Counter
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_gif import fixture
from retrofetch.catalog import Catalog
from retrofetch.importer import prepare_library
import retrofetch.importer as importer


class MultiSourceTests(unittest.TestCase):
    def test_combined_import_preserves_ids_ranks_variants_and_attributes_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'files'
            old = root / 'old'
            old_source = root / 'original'
            legacy = old_source / 'd1/animals/cat_lc.gif'
            legacy.parent.mkdir(parents=True)
            fixture(legacy)
            prepare_library(old_source, old)
            for name in ('essential-collection/d1/animals/cat_lc.gif',
                         'anime-factory/disc-01/animals/cat_lc.gif',
                         '250k-animations/animals/cat_lc.gif'):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(legacy.read_bytes())
            high = source / 'anime-factory/disc-01/animals/cat_hc.gif'
            subprocess.run(['magick', str(legacy), '-filter', 'point', '-resize', '200%', str(high)],
                           check=True, capture_output=True)
            (source / '250k-animations/readme.txt').write_text('third-party notice')
            (root / 'recovery').mkdir()
            (root / 'recovery/broken.gif.partial').write_bytes(b'partial')
            dest = root / 'stage'
            report = prepare_library(source, dest, legacy_catalog=old / 'catalog.json')
            rows = json.loads((dest / 'catalog.json').read_text())['animations']
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['id'], 'd1/animals/cat')
            self.assertEqual(rows[0]['categories'], ['animals'])
            self.assertEqual(rows[0]['width'], 8)
            self.assertEqual(rows[0]['sha256'], hashlib.sha256(high.read_bytes()).hexdigest())
            self.assertEqual(Catalog.load(dest).select('cat').id, 'd1/animals/cat')
            self.assertEqual(len(report['collections']), 3)
            refs = report['selections'][0]['sources']
            self.assertEqual({r['collection'] for r in refs},
                             {'essential-collection', 'anime-factory', '250k-animations'})
            self.assertTrue(all(r['url'].startswith('https://archive.org/details/') for r in refs))
            self.assertEqual((dest / 'notices/250k-animations/readme.txt').read_text(), 'third-party notice')
            self.assertFalse(report['partial'])
            # Existing repeated-prepare behavior is preserved without a re-decode.
            with patch('retrofetch.importer.decode_gif', side_effect=AssertionError('checkpoint reuse')):
                self.assertEqual(prepare_library(source, dest, legacy_catalog=old / 'catalog.json')['successes'], 1)

    def test_orphan_high_and_transparent_suffixes_form_one_family(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            for suffix in ('ha', 'hb', 'hc', 'sx', 'tm'):
                fixture(source / f'actor_{suffix}.gif')
            report = prepare_library(source, root / 'out')
            self.assertEqual(report['families'], 1)
            self.assertEqual(report['successes'], 1)

    def test_identical_bytes_are_decoded_once_without_merging_same_source_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            fixture(source / 'cat_lc.gif')
            (source / 'dog_lc.gif').write_bytes((source / 'cat_lc.gif').read_bytes())
            with patch('retrofetch.importer.decode_gif', wraps=importer.decode_gif) as decoder:
                report = prepare_library(source, root / 'out')
            self.assertEqual(report['successes'], 2)
            self.assertEqual(decoder.call_count, 1)

    def test_new_canonical_id_cannot_steal_a_legacy_selector(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            original = source / 'd1/animals/cat_lc.gif'
            original.parent.mkdir(parents=True)
            fixture(original)
            dest = root / 'out'
            prepare_library(source, dest)
            before = {name: (dest / name).read_bytes() for name in ('catalog.json', 'manifest.json')}
            legacy = root / 'legacy.json'
            legacy.write_bytes(before['catalog.json'])
            (source / 'cat.gif').write_bytes(original.read_bytes())
            with self.assertRaisesRegex(ValueError, 'legacy selector'):
                prepare_library(source, dest, legacy_catalog=legacy)
            self.assertEqual(Catalog.load(dest).select('cat').id, 'd1/animals/cat')
            for name, contents in before.items():
                self.assertEqual((dest / name).read_bytes(), contents)

    def test_duplicate_inspection_failures_are_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            fixture(source / 'good.gif')
            broken = b'GIF89a broken duplicate'
            for name in ('bad1.gif', 'bad2.gif'):
                (source / name).write_bytes(broken)
            counts = Counter()
            inspect = importer.inspect_gif
            def counted(path):
                counts[path.read_bytes()] += 1
                return inspect(path)
            with patch('retrofetch.importer.inspect_gif', side_effect=counted):
                prepare_library(source, root / 'out')
            self.assertEqual(counts[broken], 1)

    def test_missing_legacy_id_does_not_publish_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            fixture(source / 'cat.gif')
            dest = root / 'out'
            prepare_library(source, dest)
            before = (dest / 'catalog.json').read_bytes()
            legacy = root / 'legacy.json'
            document = json.loads(before)
            document['animations'][0]['id'] = 'missing'
            legacy.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, 'legacy'):
                prepare_library(source, dest, legacy_catalog=legacy)
            self.assertEqual((dest / 'catalog.json').read_bytes(), before)
