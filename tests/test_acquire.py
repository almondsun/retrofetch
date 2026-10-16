"""Acquisition checks use synthetic ZIPs and mocked HTTPS responses only."""

import io
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
from unittest.mock import patch
import zipfile

from retrofetch.acquire import AcquisitionError, extract_zip


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

if __name__ == "__main__":
    unittest.main()
