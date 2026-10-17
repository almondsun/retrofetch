import tempfile
import unittest
from pathlib import Path
from test_gif import fixture

from retrofetch.collections import organize_collections, provenance, preserve_selectors


class CollectionTests(unittest.TestCase):
    def test_shared_variants_combine_candidates_without_losing_legacy_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = ['essential-collection/d1/animals/cat_lc.gif',
                     'essential-collection/d2/animals/cat_lc.gif',
                     'anime-factory/disc-01/animals/cat_sc.gif',
                     'anime-factory/disc-01/animals/cat_hc.gif',
                     '250k-animations/animals/cat_lc.gif']
            for index, name in enumerate(paths):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if index == 0:
                    fixture(path)
                    shared = path.read_bytes()
                else:
                    path.write_bytes(shared if index < 3 else str(index).encode())
            groups = {str(Path(name).with_suffix('')): [root / name] for name in paths}
            # Real grouping already joins a family's size/background variants.
            groups['anime-factory/disc-01/animals/cat_sc'] += groups.pop('anime-factory/disc-01/animals/cat_hc')
            result, origins = organize_collections(root, groups)
            self.assertIn('d1/animals/cat_lc', result)
            self.assertIn('d2/animals/cat_lc', result)
            self.assertEqual(len(result['d1/animals/cat_lc']), 4)
            self.assertEqual(len(result['d2/animals/cat_lc']), 4)
            self.assertIn('250k-animations/animals/cat_lc', result)
            self.assertEqual(len(result), 3)  # Same name without shared bytes stays distinct.
            self.assertEqual({v['collection'] for v in origins['d1/animals/cat_lc']},
                             {'essential-collection', 'anime-factory'})

    def test_corrupt_shared_bytes_do_not_merge_unrelated_families(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            groups = {}
            for name in ('essential-collection/d1/cat', 'anime-factory/disc-01/dog'):
                path = root / (name + '.gif')
                path.parent.mkdir(parents=True)
                path.write_bytes(b'corrupt')
                groups[name] = [path]
            result, _ = organize_collections(root, groups)
            self.assertEqual(len(result), 2)

    def test_shared_multiframe_still_cannot_establish_family_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            groups = {}
            for name in ('essential-collection/d1/cat', 'anime-factory/disc-01/dog'):
                path = root / (name + '.gif')
                path.parent.mkdir(parents=True)
                fixture(path, static=True)
                groups[name] = [path]
            result, _ = organize_collections(root, groups)
            self.assertEqual(len(result), 2)

    def test_single_collection_preserves_original_ids_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'd1/animals/cat_lc.gif'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'source')
            groups, origins = organize_collections(root, {'d1/animals/cat': [path]})
            self.assertEqual(list(groups), ['d1/animals/cat'])
            self.assertEqual(origins, {})
            records = provenance(root)
            self.assertEqual(records[0]['url'], 'https://archive.org/details/animationfactoryessentialcollection')

    def test_selector_pinning_prevents_new_name_ambiguity(self):
        old = [{'id': 'd1/cat', 'name': 'cat', 'aliases': ['old-cat'], 'categories': ['animals']}]
        current = [dict(old[0], aliases=[]),
                   {'id': 'anime-factory/cat', 'name': 'cat', 'aliases': ['old-cat'], 'categories': ['new']}]
        preserve_selectors(current, old)
        self.assertIn('cat', current[0]['aliases'])
        self.assertIn('old-cat', current[0]['aliases'])
        self.assertNotIn('old-cat', current[1]['aliases'])
        self.assertEqual(current[0]['categories'], ['animals'])
        with self.assertRaisesRegex(ValueError, 'legacy'):
            preserve_selectors(current[1:], old)
