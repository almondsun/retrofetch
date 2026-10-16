import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from retrofetch.gif import decode_gif
from retrofetch.normalization import normalize_library


def animation(path, width, height, positions, *, static=False):
    arguments = []
    for index, (x, y) in enumerate(positions):
        arguments.extend([
            '(', '-dispose', 'Background', '-size', f'{width}x{height}',
            'xc:none', '-fill', 'red',
            '-draw', f'rectangle {x},{y} {x + 1},{y + 1}',
            '-set', 'delay', str(7 + index * 6), ')',
        ])
    subprocess.run(['magick', *arguments, '-loop', '0', str(path)], check=True, capture_output=True)


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'animations').mkdir()

    def add(self, name, width, height, positions):
        path = self.root / f'{name}.gif'
        animation(path, width, height, positions)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        target = self.root / 'animations' / f'{digest}.gif'
        path.rename(target)
        row = {
            'id': name, 'name': name, 'path': f'animations/{digest}.gif',
            'sha256': digest, 'width': width, 'height': height,
            'frames': len(positions), 'categories': ['test'], 'aliases': [],
        }
        return row

    def publish(self, rows):
        (self.root / 'catalog.json').write_text(json.dumps({'version': 1, 'animations': rows}))

    def test_stable_union_center_timing_originals_and_idempotence(self):
        row = self.add('moving', 80, 50, [(5, 8), (25, 18)])
        self.publish([row])
        original = (self.root / 'catalog.json').read_bytes()
        source = (self.root / row['path']).read_bytes()
        result = normalize_library(self.root)
        self.assertEqual(result['successes'], 1)
        current = json.loads((self.root / 'catalog.json').read_text())['animations'][0]
        decoded = decode_gif(self.root / current['path'])
        self.assertEqual((decoded.info.width, decoded.info.height), (160, 160))
        self.assertEqual(decoded.info.delays, (7, 13))
        self.assertTrue(decoded.animated)
        self.assertTrue(decoded.transparent)
        self.assertEqual((self.root / row['path']).read_bytes(), source)
        self.assertEqual((self.root / 'catalog.original.json').read_bytes(), original)
        bounds = []
        for frame in decoded.pixels:
            visible = [i // 4 for i in range(0, len(frame), 4) if frame[i + 3]]
            bounds.append((min(i % 160 for i in visible), min(i // 160 for i in visible)))
        self.assertEqual((bounds[1][0] - bounds[0][0], bounds[1][1] - bounds[0][1]), (20, 10))
        before = (self.root / 'catalog.json').read_bytes()
        repeated = normalize_library(self.root)
        self.assertTrue(repeated['reused'])
        self.assertEqual((self.root / 'catalog.json').read_bytes(), before)

    def test_wide_tall_and_tiny_never_upscale(self):
        rows = [self.add('wide', 400, 20, [(1, 5), (350, 5)]),
                self.add('tall', 20, 400, [(5, 1), (5, 350)]),
                self.add('tiny', 10, 10, [(1, 1), (4, 1)])]
        self.publish(rows)
        report = normalize_library(self.root)
        self.assertEqual(report['successes'], 3)
        catalog = json.loads((self.root / 'catalog.json').read_text())
        tiny = next(row for row in catalog['animations'] if row['id'] == 'tiny')
        decoded = decode_gif(self.root / tiny['path'])
        self.assertEqual(sum(decoded.pixels[0][3::4]) // 255, 4)

    def test_large_solid_artwork_retains_transparent_canvas_border(self):
        path = self.root / 'solid.gif'
        arguments = []
        for x in (80, 110):
            arguments.extend(['(', '-dispose', 'Background', '-size', '200x200', 'xc:none',
                              '-fill', 'red', '-draw', 'rectangle 1,1 198,198',
                              '-fill', 'blue', '-draw', f'rectangle {x},80 {x+10},90',
                              '-set', 'delay', '10', ')'])
        subprocess.run(['magick', *arguments, '-loop', '0', str(path)], check=True, capture_output=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.rename(self.root / 'animations' / (digest + '.gif'))
        self.publish([dict(id='solid', name='solid', path=f'animations/{digest}.gif',
                           sha256=digest, width=200, height=200, frames=2,
                           categories=['test'], aliases=[])])
        report = normalize_library(self.root)
        self.assertEqual(report['successes'], 1)
        row = json.loads((self.root / 'catalog.json').read_text())['animations'][0]
        decoded = decode_gif(self.root / row['path'])
        self.assertTrue(decoded.transparent)
        self.assertTrue(decoded.animated)

    def test_partial_static_failure_and_unowned_backup(self):
        row = self.add('still', 10, 10, [(1, 1), (1, 1)])
        self.publish([row])
        original = (self.root / 'catalog.json').read_bytes()
        with self.assertRaises(ValueError):
            normalize_library(self.root)
        self.assertEqual((self.root / 'catalog.json').read_bytes(), original)
        self.assertFalse((self.root / 'catalog.original.json').exists())
        row = self.add('motion', 10, 10, [(1, 1), (4, 1)])
        self.publish([row])
        report = normalize_library(self.root, limit=1)
        self.assertTrue(report['partial'])
        self.assertFalse((self.root / 'catalog.original.json').exists())
        self.assertFalse((self.root / 'normalization.json').exists())
        (self.root / 'catalog.original.json').write_text('user content')
        with self.assertRaises(ValueError):
            normalize_library(self.root)
        self.assertEqual((self.root / 'catalog.original.json').read_text(), 'user content')

    def test_source_catalog_change_preserves_first_backup(self):
        first = self.add('first', 10, 10, [(1, 1), (4, 1)])
        self.publish([first])
        normalize_library(self.root)
        backup = (self.root / 'catalog.original.json').read_bytes()
        second = self.add('second', 20, 20, [(1, 1), (6, 1)])
        self.publish([second])
        normalize_library(self.root)
        self.assertEqual((self.root / 'catalog.original.json').read_bytes(), backup)
        self.assertEqual(json.loads((self.root / 'catalog.json').read_text())['animations'][0]['id'], 'second')

    def test_duplicate_content_transforms_once_and_preserves_identity_fields(self):
        from unittest.mock import patch
        from retrofetch.normalization import _normalize

        row = self.add('first', 20, 20, [(1, 1), (6, 1)])
        duplicate = dict(row, id='second', name='second', aliases=['first-alias'])
        self.publish([row, duplicate])
        with patch('retrofetch.normalization._normalize', wraps=_normalize) as transform:
            report = normalize_library(self.root)
        self.assertEqual(transform.call_count, 1)
        self.assertEqual(report['unique_files'], 1)
        current = json.loads((self.root / 'catalog.json').read_text())['animations']
        self.assertEqual(current[1]['aliases'], ['first-alias'])
        self.assertEqual(current[1]['categories'], duplicate['categories'])

    def test_corrupt_derivative_and_backup_are_not_silently_reused(self):
        row = self.add('motion', 20, 20, [(1, 1), (6, 1)])
        self.publish([row])
        normalize_library(self.root)
        catalog = (self.root / 'catalog.json').read_bytes()
        current = json.loads(catalog)['animations'][0]
        (self.root / current['path']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            normalize_library(self.root)
        self.assertEqual((self.root / 'catalog.json').read_bytes(), catalog)
        (self.root / 'catalog.original.json').write_text('corrupt backup')
        with self.assertRaises(ValueError):
            normalize_library(self.root)

    def test_original_catalog_can_be_restored_without_losing_assets(self):
        from retrofetch.catalog import Catalog

        row = self.add('motion', 20, 20, [(1, 1), (6, 1)])
        self.publish([row])
        normalize_library(self.root)
        backup = self.root / 'catalog.original.json'
        (self.root / 'catalog.json').write_bytes(backup.read_bytes())
        restored = Catalog.load(self.root).select('motion')
        self.assertEqual(restored.sha256, row['sha256'])
        self.assertEqual((restored.width, restored.height), (20, 20))

    def test_symlink_asset_is_rejected_without_publishing(self):
        row = self.add('motion', 20, 20, [(1, 1), (6, 1)])
        self.publish([row])
        source = self.root / row['path']
        relocated = self.root / 'outside.gif'
        source.rename(relocated)
        source.symlink_to(relocated)
        original = (self.root / 'catalog.json').read_bytes()
        with self.assertRaises(ValueError):
            normalize_library(self.root)
        self.assertEqual((self.root / 'catalog.json').read_bytes(), original)
        self.assertFalse((self.root / 'catalog.original.json').exists())
