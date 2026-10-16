"""Acquisition checks use synthetic ZIPs and mocked HTTPS responses only."""

import hashlib
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
from unittest.mock import patch
from urllib.request import Request
import zipfile

from retrofetch.acquire import AcquisitionError, _ArchiveRedirect, acquire_source, extract_zip


class Response(io.BytesIO):
    def geturl(self):
        return "https://archive.org/test"


def archive_bytes(extra=()):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for disc in range(1, 5):
            archive.writestr(f"collection/d{disc}/readme.txt", "Source rights notice")
        archive.writestr("collection/d1/animals/cat_mc.gif", b"GIF89a")
        archive.writestr("collection/d1/autorun.exe", b"never execute")
        for name, value in extra:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(name, value)
    return out.getvalue()


class AcquireTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def network(self, payload, *, digest=None, fail=False):
        metadata = json.dumps({"files": [{
            "name": "animation factory essential collection.zip",
            "size": str(len(payload)),
            "sha1": digest or hashlib.sha1(payload).hexdigest(),
        }]}).encode()

        def open_url(url, **kwargs):
            if "/metadata/" in url:
                return Response(metadata)
            if fail:
                raise OSError("interrupted connection")
            return Response(payload)

        return patch("retrofetch.acquire.urlopen", side_effect=open_url)

    def test_download_extract_and_verified_reuse(self):
        with self.network(archive_bytes()) as network:
            source = acquire_source(self.root / "cache with spaces")
            calls = network.call_count
            self.assertEqual(acquire_source(self.root / "cache with spaces"), source)
            self.assertEqual(network.call_count, calls)
        self.assertTrue((source / "collection/d1/animals/cat_mc.gif").is_file())
        self.assertFalse(list(source.rglob("*.exe")))
        self.assertEqual(len(list(source.rglob("readme.txt"))), 4)

    def test_reject_archive_paths_before_writing(self):
        cases = ["../escape.gif", "/absolute.gif", "C:/drive.gif", "a\\escape.gif",
                 "collection/d1/animals/cat_mc.gif", "file.gif/child.gif"]
        for index, name in enumerate(cases):
            extras = [(name, b"bad")]
            if name == "file.gif/child.gif":
                extras.insert(0, ("file.gif", b"bad"))
            archive = self.root / f"bad-{index}.zip"
            archive.write_bytes(archive_bytes(extras))
            destination = self.root / f"out-{index}"
            with self.subTest(name=name), self.assertRaises(AcquisitionError):
                extract_zip(archive, destination)
            self.assertFalse(destination.exists())

    def test_reject_symlink_and_resource_limits_before_writing(self):
        archive = self.root / "links.zip"
        info = zipfile.ZipInfo("link.gif")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(archive, "w") as out:
            out.writestr(info, "outside")
        with self.assertRaises(AcquisitionError):
            extract_zip(archive, self.root / "out")
        self.assertFalse((self.root / "out").exists())
        for limit in ("MAX_FILE_BYTES", "MAX_MEMBERS"):
            with patch(f"retrofetch.acquire.{limit}", 1), self.assertRaises(AcquisitionError):
                extract_zip(archive, self.root / "out")
            self.assertFalse((self.root / "out").exists())
        archive.write_bytes(archive_bytes())
        with patch("retrofetch.acquire.MAX_TOTAL_BYTES", 4), self.assertRaises(AcquisitionError):
            extract_zip(archive, self.root / "out")
        self.assertFalse((self.root / "out").exists())

    def test_corrupt_digest_and_network_failure_do_not_publish(self):
        for name, options in [("checksum", {"digest": "0" * 40}), ("network", {"fail": True})]:
            cache = self.root / name
            with self.network(archive_bytes(), **options), self.assertRaises(AcquisitionError):
                acquire_source(cache)
            self.assertFalse((cache / "source").exists())
            self.assertFalse(list(cache.glob("*.part")))

    def test_changed_cached_file_is_not_silently_reused(self):
        payload = archive_bytes()
        with self.network(payload):
            source = acquire_source(self.root / "cache")
        (source / "collection/d1/animals/cat_mc.gif").write_bytes(b"tampered")
        with self.network(payload):
            source = acquire_source(self.root / "cache")
        self.assertEqual((source / "collection/d1/animals/cat_mc.gif").read_bytes(), b"GIF89a")

    def test_unknown_source_directory_is_preserved(self):
        cache = self.root / "cache"
        (cache / "source").mkdir(parents=True)
        owned = cache / "source/private.txt"
        owned.write_text("keep")
        with self.network(archive_bytes()), self.assertRaises(AcquisitionError):
            acquire_source(cache)
        self.assertEqual(owned.read_text(), "keep")

    def test_missing_sha1_uses_crc_after_resource_preflight(self):
        payload = archive_bytes()
        metadata = json.dumps({"files": [{"name": "animation factory essential collection.zip", "size": str(len(payload))}]}).encode()
        with patch("retrofetch.acquire.urlopen", side_effect=[Response(metadata), Response(payload)]):
            source = acquire_source(self.root / "cache")
        self.assertTrue(source.is_dir())

    def test_partial_read_and_insecure_redirect_publish_nothing(self):
        class Broken(Response):
            def read(self, size=-1):
                raise IncompleteRead(b"truncated")
        class Insecure(Response):
            def geturl(self):
                return "http://archive.org/insecure"
        for name, response in (("read", Broken(b"")), ("redirect", Insecure(b""))):
            cache = self.root / name
            with patch("retrofetch.acquire.urlopen", return_value=response), self.assertRaises(AcquisitionError):
                acquire_source(cache)
            self.assertFalse((cache / "source").exists())

    def test_failed_extraction_keeps_previous_source_unchanged(self):
        cache = self.root / "cache"
        with self.network(archive_bytes()):
            source = acquire_source(cache)
        original = source / "collection/d1/readme.txt"
        original.write_text("altered but must survive failed update")
        with self.network(archive_bytes()), patch("retrofetch.acquire.extract_zip", side_effect=AcquisitionError("failure")), self.assertRaises(AcquisitionError):
            acquire_source(cache)
        self.assertEqual(original.read_text(), "altered but must survive failed update")

    def test_source_and_archive_symlinks_are_rejected(self):
        for name in ("source", "collection.zip", "collection.zip.part"):
            cache = self.root / name.replace(".", "-")
            cache.mkdir()
            outside = self.root / "private"
            outside.write_text("keep")
            (cache / name).symlink_to(outside)
            with self.network(archive_bytes()), self.assertRaises(AcquisitionError):
                acquire_source(cache)
            self.assertEqual(outside.read_text(), "keep")

    def test_redirect_rejected_before_following_other_destination(self):
        handler = _ArchiveRedirect()
        request = Request("https://archive.org/source")
        for url in ("http://archive.org/source", "https://example.com/source", "https://user:secret@archive.org/source"):
            with self.subTest(url=url), self.assertRaises(AcquisitionError):
                handler.redirect_request(request, None, 302, "redirect", {}, url)


if __name__ == "__main__":
    unittest.main()
