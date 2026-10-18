import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from retrofetch.acquire import acquire_collections, AcquisitionError
from retrofetch.sources import Source
from test_acquire import Response, archive_bytes
from test_iso9660 import image


class CollectionAcquireTests(unittest.TestCase):
    def test_combined_roots_archive_hashes_and_offline_reuse(self):
        zipdata, iso = archive_bytes(), image()
        sources = (
            Source('essential','essential-collection','item','essential.zip',len(zipdata),hashlib.sha1(zipdata).hexdigest(),'essential-collection'),
            Source('anime-01','anime-factory','item','one.iso',len(iso),hashlib.sha1(iso).hexdigest(),'anime-factory/disc-01'),
            Source('250k','250k-animations','item','two.iso',len(iso),hashlib.sha1(iso).hexdigest(),'250k-animations'))
        with tempfile.TemporaryDirectory() as tmp, patch('retrofetch.acquire.SOURCES',sources):
            with patch('retrofetch.acquire._open', side_effect=lambda url: Response(zipdata if url.endswith('.zip') else iso)):
                root = acquire_collections(Path(tmp)/'cache')
            self.assertTrue((root/'essential-collection/d1/readme.txt').is_file())
            self.assertTrue((root/'anime-factory/disc-01/CAT.GIF').is_file())
            with patch('retrofetch.acquire._open',side_effect=AssertionError('network reuse')):
                self.assertEqual(acquire_collections(Path(tmp)/'cache'),root)
            self.assertTrue((root/'.retrofetch-source.json').is_file())

    def test_failed_download_keeps_completed_cache_and_no_combined_publication(self):
        iso=image();digest=hashlib.sha1(iso).hexdigest()
        sources=(Source('a','anime-factory','item','a.iso',len(iso),digest,'anime-factory/disc-01'),Source('b','250k-animations','item','b.iso',len(iso),'0'*40,'250k-animations'))
        with tempfile.TemporaryDirectory() as tmp, patch('retrofetch.acquire.SOURCES',sources), patch('retrofetch.acquire._open',side_effect=lambda url:Response(iso)):
            root=Path(tmp)/'cache'
            with self.assertRaises(AcquisitionError):acquire_collections(root)
            self.assertTrue((root/'collections/a/source/CAT.GIF').is_file())
            self.assertFalse((root/'source').exists())

    def test_symlink_ancestor_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'link').symlink_to(root,target_is_directory=True)
            with self.assertRaises(AcquisitionError):acquire_collections(root/'link/cache')

    def test_modified_or_injected_cache_is_rebuilt_from_verified_archive(self):
        iso=image()
        spec=Source('a','250k-animations','item','a.iso',len(iso),hashlib.sha1(iso).hexdigest(),'250k-animations')
        with tempfile.TemporaryDirectory() as tmp, patch('retrofetch.acquire.SOURCES',(spec,)):
            cache=Path(tmp)/'cache'
            with patch('retrofetch.acquire._open',return_value=Response(iso)):
                root=acquire_collections(cache)
            (root/'250k-animations/injected.gif').write_bytes(b'bad')
            with patch('retrofetch.acquire._open',side_effect=AssertionError('must reuse archive')):
                root=acquire_collections(cache)
            self.assertFalse((root/'250k-animations/injected.gif').exists())
            (root/'250k-animations/CAT.GIF').write_bytes(b'changed')
            with patch('retrofetch.acquire._open',side_effect=AssertionError('must reuse archive')):
                root=acquire_collections(cache)
            self.assertEqual((root/'250k-animations/CAT.GIF').read_bytes(),b'GIF89a')

    def test_arbitrary_truncated_iso_is_not_accepted_as_known_damage(self):
        iso=image(truncated=True)
        spec=Source('anime-03','anime-factory','item','a.iso',len(iso),hashlib.sha1(iso).hexdigest(),'anime-factory/disc-03')
        with tempfile.TemporaryDirectory() as tmp, patch('retrofetch.acquire.SOURCES',(spec,)), patch('retrofetch.acquire._open',return_value=Response(iso)):
            cache=Path(tmp)/'cache'
            with self.assertRaises(AcquisitionError):acquire_collections(cache)
            self.assertFalse((cache/'source').exists())
            self.assertFalse((cache/'collections/anime-03/source').exists())
