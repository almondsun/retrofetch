import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
from test_gif import fixture
from retrofetch.importer import prepare_library, validate_library
from retrofetch.gif import GifError


class ImportTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.category = self.source / 'd1' / 'animals' / 'cats'
        self.category.mkdir(parents=True)
        self.dest = self.root / 'data'
        self.dest.mkdir()
        (self.dest / 'README.md').write_text('keep')

    def test_import_group_dedup_notices_repeat(self):
        fixture(self.category / 'cat_lc.gif')
        (self.category / 'cat_sc.gif').write_bytes((self.category / 'cat_lc.gif').read_bytes())
        (self.category / 'other_lc.gif').write_bytes((self.category / 'cat_lc.gif').read_bytes())
        (self.category / 'vp_cat.htm').write_text('<a href="cat_lc.gif">large</a><a href="cat_sc.gif">small</a><img src="../../images/end.gif">')
        (self.source / 'readme.txt').write_text('source rights')
        report = prepare_library(self.source, self.dest)
        self.assertEqual(report['successes'], 2)
        self.assertEqual(validate_library(self.dest)['animations'], 2)
        catalog = json.loads((self.dest / 'catalog.json').read_text())
        self.assertEqual(len({x['path'] for x in catalog['animations']}), 1)
        self.assertEqual(catalog['animations'][0]['categories'], ['animals/cats'])
        self.assertEqual((self.dest / 'README.md').read_text(), 'keep')
        self.assertEqual((self.dest / 'notices' / 'readme.txt').read_text(), 'source rights')
        with patch('retrofetch.importer.decode_gif', side_effect=AssertionError('should resume')):
            self.assertEqual(prepare_library(self.source, self.dest)['successes'], 2)
        asset = self.dest / catalog['animations'][0]['path']
        asset.write_bytes(b'broken')
        with self.assertRaises(ValueError):
            validate_library(self.dest)

    def test_exclude_static_corrupt_and_symlinks_source_unchanged(self):
        fixture(self.category / 'still_lc.gif', static=True)
        (self.category / 'bad.gif').write_bytes(b'bad')
        (self.category / 'link.gif').symlink_to(self.category / 'still_lc.gif')
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.category.iterdir() if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'No usable animations'):
            prepare_library(self.source, self.dest)
        result = json.loads((self.dest / 'import-failure.json').read_text())
        self.assertEqual(result['successes'], 0)
        self.assertEqual(len(result['exclusions']), 3)
        self.assertFalse((self.dest / 'catalog.json').exists())
        self.assertEqual((self.dest / 'README.md').read_text(), 'keep')
        self.assertEqual(
            before,
            {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.category.iterdir() if p.is_file()},
        )

    def test_conversion_and_fallback(self):
        fixture(self.category / 'cat_lw.gif', opaque=True)
        subprocess.run(
            [
                'magick',
                str(self.category / 'cat_lw.gif'),
                '-filter',
                'point',
                '-resize',
                '200%',
                str(self.category / 'cat_lw.gif'),
            ],
            check=True,
            capture_output=True,
        )
        result = prepare_library(self.source, self.dest)
        self.assertEqual(result['conversions'], 1)
        fixture(self.category / 'cat_sc.gif')
        with patch('retrofetch.importer.remove_uniform_matte', side_effect=GifError('uncertain')):
            # New destination prevents a validated cached conversion from being reused.
            result = prepare_library(self.source, self.root / 'fallback')
        self.assertEqual(result['fallbacks'], 1)

    def test_equal_resolution_prefers_original_clear_variant(self):
        fixture(self.category / 'cat_lb.gif', opaque=True)
        clear = self.category / 'cat_lc.gif'
        fixture(clear)
        with patch('retrofetch.importer.remove_uniform_matte',
                   side_effect=AssertionError('same-size clear original needs no conversion')):
            result = prepare_library(self.source, self.dest)
        entry = json.loads((self.dest / 'catalog.json').read_text())['animations'][0]
        self.assertEqual(entry['sha256'], hashlib.sha256(clear.read_bytes()).hexdigest())
        self.assertEqual(result['conversions'], 0)

    def test_orphan_medium_black_and_white_variants_share_the_family(self):
        fixture(self.category / 'cat_mb.gif', opaque=True)
        fixture(self.category / 'cat_mw.gif', opaque=True)
        fixture(self.category / 'cat_mc.gif')
        result = prepare_library(self.source, self.dest)
        self.assertEqual(result['families'], 1)
        self.assertEqual(result['successes'], 1)

    def test_partial_and_atomic_failure(self):
        fixture(self.category / 'a.gif')
        fixture(self.category / 'b.gif')
        result = prepare_library(self.source, self.dest, limit=1)
        self.assertTrue(result['partial'])
        self.assertFalse((self.dest / 'catalog.json').exists())
        prepare_library(self.source, self.dest)
        before = (self.dest / 'catalog.json').read_bytes()
        with patch('retrofetch.importer._publish', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                prepare_library(self.source, self.dest)
        self.assertEqual(before, (self.dest / 'catalog.json').read_bytes())

    def test_reject_destination_inside_source_and_invalid_limit(self):
        with self.assertRaises(ValueError):
            prepare_library(self.source, self.source / 'data')
        with self.assertRaises(ValueError):
            prepare_library(self.source, self.dest, limit=0)

    def test_catalog_escape_and_symlink_rejected(self):
        fixture(self.category / 'a.gif')
        prepare_library(self.source, self.dest)
        p = self.dest / 'catalog.json'
        catalog = json.loads(p.read_text())
        catalog['animations'][0]['path'] = '../outside.gif'
        p.write_text(json.dumps(catalog))
        with self.assertRaises(ValueError):
            validate_library(self.dest)

    def test_largest_clear_variant_selected(self):
        fixture(self.category / 'cat_sc.gif')
        subprocess.run(
            [
                'magick',
                str(self.category / 'cat_sc.gif'),
                '-filter',
                'point',
                '-resize',
                '200%',
                str(self.category / 'cat_lc.gif'),
            ],
            check=True,
            capture_output=True,
        )
        result = prepare_library(self.source, self.dest)
        self.assertEqual(result['successes'], 1)
        entry = json.loads((self.dest / 'catalog.json').read_text())['animations'][0]
        self.assertEqual(entry['width'], 8)
        self.assertEqual(
            (self.dest / entry['path']).read_bytes(),
            (self.category / 'cat_lc.gif').read_bytes(),
        )

    def test_malformed_checkpoint_is_rebuilt_without_traceback(self):
        fixture(self.category / 'cat_lc.gif')
        prepare_library(self.source, self.dest)
        checkpoint = self.dest / '.import-checkpoint.json'
        valid = json.loads(checkpoint.read_text())
        family = next(iter(valid['families']))
        malformed = [[], {'version': 2, 'source': str(self.source), 'families': []}]
        for field, bad in (
            ('entry', []), ('details', {}), ('converted', 'yes'), ('attempted', 1),
        ):
            document = json.loads(json.dumps(valid))
            document['families'][family][field] = bad
            malformed.append(document)
        document = json.loads(json.dumps(valid))
        document['families'][family] = []
        malformed.append(document)
        for document in malformed:
            with self.subTest(checkpoint=document):
                checkpoint.write_text(json.dumps(document))
                self.assertEqual(prepare_library(self.source, self.dest)['successes'], 1)

    def test_malformed_catalog_raises_controlled_value_error(self):
        for document in ([], {'version': 1, 'animations': [{}]},
                         {'version': 1, 'animations': [None]}):
            (self.dest / 'catalog.json').write_text(json.dumps(document))
            with self.subTest(catalog=document), self.assertRaises(ValueError):
                validate_library(self.dest)

    def test_invalid_replacement_preserves_working_library(self):
        source = self.category / 'cat_lc.gif'
        fixture(source)
        prepare_library(self.source, self.dest)
        before = {
            path.relative_to(self.dest).as_posix(): path.read_bytes()
            for path in self.dest.rglob('*')
            if path.is_file() and path.name != '.import-checkpoint.json'
        }
        source.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'No usable animations'):
            prepare_library(self.source, self.dest)
        for relative, content in before.items():
            self.assertEqual((self.dest / relative).read_bytes(), content)
        report = json.loads((self.dest / 'import-failure.json').read_text())
        self.assertEqual(report['successes'], 0)
        self.assertTrue(report['exclusions'][0]['variants'][0]['reason'])
