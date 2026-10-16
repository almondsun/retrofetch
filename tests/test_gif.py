import tempfile
import unittest
from pathlib import Path
from retrofetch.gif import inspect_gif, GifError, decode_gif
import subprocess


def fixture(path, opaque=False, static=False):
    bg = 'white' if opaque else 'none'
    second = '1,1' if static else '2,1'
    subprocess.run(
        [
            'magick',
            '-size',
            '4x4',
            'xc:' + bg,
            '-fill',
            'red',
            '-draw',
            'point 1,1',
            '-set',
            'delay',
            '7',
            '(',
            '-size',
            '4x4',
            'xc:' + bg,
            '-fill',
            'red',
            '-draw',
            'point ' + second,
            '-set',
            'delay',
            '13',
            ')',
            '-loop',
            '0',
            str(path),
        ],
        check=True,
        capture_output=True,
    )


class GifTests(unittest.TestCase):

    def test_metadata_and_visible_animation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.gif'
            fixture(p)
            info = inspect_gif(p)
            self.assertEqual(
                (info.width, info.height, info.frames, info.delays),
                (4, 4, 2, (7, 13)),
            )
            decoded = decode_gif(p, info)
            self.assertTrue(decoded.animated)
            self.assertTrue(decoded.transparent)

    def test_identical_frames_are_static(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.gif'
            fixture(p, static=True)
            self.assertFalse(decode_gif(p, inspect_gif(p)).animated)

    def test_corrupt_and_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.gif'
            p.write_bytes(b'GIF89a')
            with self.assertRaises(GifError):
                inspect_gif(p)
            q = Path(d) / 'b.gif'
            q.symlink_to(p)
            with self.assertRaises(GifError):
                inspect_gif(q)

    def test_opaque_delta_is_not_clear(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.gif'
            fixture(p, opaque=True)
            subprocess.run(
                ['magick', str(p), '-layers', 'Optimize', str(p)],
                check=True,
                capture_output=True,
            )
            self.assertFalse(decode_gif(p, inspect_gif(p)).transparent)

    def test_transparent_corner_does_not_prove_clear_background(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            arguments = []
            for index, position in enumerate((5, 6)):
                pixels = bytearray(b'\xff\xff\xff\xff' * 16)
                pixels[:4] = b'\0\0\0\0'
                pixels[position * 4:position * 4 + 4] = b'\xff\0\0\xff'
                raw = root / f'{index}.rgba'
                raw.write_bytes(pixels)
                arguments.extend([
                    '-delay', '7', '-size', '4x4', '-depth', '8', 'rgba:' + str(raw),
                ])
            output = root / 'residual-background.gif'
            subprocess.run(
                ['magick', *arguments, '-loop', '0', str(output)],
                check=True, capture_output=True,
            )
            decoded = decode_gif(output)
            self.assertTrue(decoded.animated)
            self.assertFalse(decoded.transparent)
