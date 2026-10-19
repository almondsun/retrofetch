import tempfile
from pathlib import Path
import unittest
from retrofetch.iso9660 import inspect_iso, extract_iso, IsoError


def both(value, width):
    return value.to_bytes(width, 'little') + value.to_bytes(width, 'big')


def record(name, sector, size, directory=False):
    length = 33 + len(name) + (len(name) % 2 == 0)
    b = bytearray(length)
    b[0] = length
    b[2:10] = both(sector, 4)
    b[10:18] = both(size, 4)
    b[25] = 2 if directory else 0
    b[28:32] = both(1, 2)
    b[32] = len(name)
    b[33:33+len(name)] = name
    return b


def image(name=b'CAT.GIF;1', *, truncated=False, cycle=False):
    b = bytearray(24*2048)
    p = memoryview(b)[16*2048:17*2048]
    p[:7] = b'\x01CD001\x01'
    p[80:88] = both(24, 4)
    p[128:132] = both(2048, 2)
    p[156:190] = record(b'\0', 20, 2048, True)
    b[17*2048:17*2048+7] = b'\xffCD001\x01'
    rows = record(b'\0',20,2048,True)+record(b'\1',20,2048,True)
    rows += record(name,20 if cycle else 23,6,cycle)
    b[20*2048:20*2048+len(rows)] = rows
    b[23*2048:23*2048+6] = b'GIF89a'
    return bytes(b[:23*2048+3] if truncated else b)


class IsoTests(unittest.TestCase):
    def test_extract_supported_and_skip_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'x.iso';p.write_bytes(image())
            report=extract_iso(p,Path(tmp)/'out')
            self.assertEqual((Path(tmp)/'out/CAT.GIF').read_bytes(),b'GIF89a')
            self.assertEqual(len(report['files']),1)
            p.write_bytes(image(b'RUN.EXE;1'))
            self.assertEqual(inspect_iso(p)['entries'][0]['path'],'RUN.EXE')

    def test_reject_traversal_truncation_cycle_and_limits_before_output(self):
        for payload in (image(b'../BAD.GIF;1'),image(truncated=True),image(cycle=True)):
            with self.subTest(payload=payload[-6:]), tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp)/'x.iso';p.write_bytes(payload)
                with self.assertRaises(IsoError):extract_iso(p,Path(tmp)/'out')
                self.assertFalse((Path(tmp)/'out').exists())

    def test_reject_symlink_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'x.iso';p.write_bytes(image())
            (root/'link').symlink_to(root,target_is_directory=True)
            with self.assertRaises(IsoError):extract_iso(p,root/'link/out')

    def test_reject_overlaps_endian_duplicates_and_resource_bounds(self):
        from unittest.mock import patch
        cases = []
        overlap = bytearray(image())
        first = 20*2048 + 68
        overlap[first+2:first+10] = both(20,4)
        cases.append(overlap)
        endian = bytearray(image());endian[16*2048+84] ^= 1;cases.append(endian)
        duplicate = bytearray(image())
        row = record(b'CAT.GIF;1',23,6)
        duplicate[first+len(row):first+2*len(row)] = row
        cases.append(duplicate)
        for payload in cases:
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'x.iso';path.write_bytes(payload)
                with self.assertRaises(IsoError):inspect_iso(path)
        for limit in ('MAX_ENTRIES','MAX_DIRECTORY_BYTES','MAX_FILE_BYTES','MAX_TOTAL_BYTES'):
            with tempfile.TemporaryDirectory() as tmp, patch('retrofetch.iso9660.'+limit,0):
                path=Path(tmp)/'x.iso';path.write_bytes(image())
                with self.assertRaises(IsoError):inspect_iso(path)

    def test_known_truncated_payload_is_reported_not_extracted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'x.iso';path.write_bytes(image(truncated=True))
            report=extract_iso(path,Path(tmp)/'out',known_truncation=True)
            self.assertEqual(report['files'],{})
            self.assertEqual(report['omitted'],[{'path':'CAT.GIF','offset':23*2048,'size':6}])

    def test_unused_missing_volume_padding_does_not_discard_intact_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload=bytearray(image())
            payload[16*2048+80:16*2048+88]=both(174,4)
            path=Path(tmp)/'x.iso';path.write_bytes(payload)
            self.assertEqual(len(inspect_iso(path)['entries']),1)
