"""Isolated native frontend checks; never emit graphics to the host terminal."""
import fcntl
import termios
import hashlib
import json
import os
from pathlib import Path
import pty
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.fallback = cls.root / 'python-launcher'
        cls.fallback.write_text('#!/usr/bin/python3\nimport json,sys\nprint("FALLBACK:"+json.dumps(sys.argv[1:]))\n')
        cls.fallback.chmod(0o755)
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_native.py'), '--output-dir', str(cls.root),
                        '--python-launcher', str(cls.fallback)] + (['--sanitize'] if os.environ.get('RETROFETCH_TEST_SANITIZE') else []), check=True, capture_output=True)
        cls.binary = cls.root / 'retrofetch'

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.home = Path(self.work.name)
        self.env = dict(os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / 'config'),
                        XDG_STATE_HOME=str(self.home / 'state'), TERM='xterm-kitty', TMUX='', STY='')
        self.helpers = self.home / 'bin'
        self.helpers.mkdir()
        helper = self.helpers / 'kitten'
        helper.write_text('#!/usr/bin/python3\nimport json,sys\nprint("KITTEN:"+json.dumps(sys.argv[1:]))\n')
        helper.chmod(0o755)
        self.env['PATH'] = str(self.helpers) + ':' + os.environ['PATH']
        self.gif = self.home / 'art.gif'
        self.gif.write_bytes(b'GIF89a' + struct.pack('<HH', 160, 160))

    def run_native(self, args, tty=False):
        if not tty:
            return subprocess.run([str(self.binary), *args], env=self.env, capture_output=True, text=True)
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        try:
            result = subprocess.run([str(self.binary), *args], env=self.env, stdout=slave, stderr=subprocess.PIPE)
            os.close(slave)
            slave = None
            chunks = []
            while True:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            return subprocess.CompletedProcess(args, result.returncode, b''.join(chunks).decode(), result.stderr.decode())
        finally:
            os.close(master)
            if slave is not None:
                os.close(slave)

    def index(self, keys=None):
        root = self.home / 'data'
        root.mkdir()
        (root / 'animations').mkdir()
        digest = hashlib.sha256(self.gif.read_bytes()).digest()
        catalog = json.dumps({'version': 1, 'animations': [{
            'id': 'art', 'name': 'Art', 'path': 'animations/' + digest.hex() + '.gif',
            'width': 160, 'height': 160, 'frames': 2, 'sha256': digest.hex(),
            'categories': ['retro'], 'aliases': []}]}).encode()
        (root / 'catalog.json').write_bytes(catalog)
        (root / 'animations' / (digest.hex() + '.gif')).write_bytes(self.gif.read_bytes())
        metadata = json.dumps({'category_names': [], 'rows': [['art', 'Art', [], []]]}).encode()
        keybytes = b''
        for key, role, ordinal in keys or [('art', 0, 0)]:
            encoded = key.encode()
            keybytes += struct.pack('<I', len(encoded)) + encoded + struct.pack('<BI', role, ordinal)
        body = struct.pack('<8sII32sQQQ', b'RTFXIDX3', 3, 1, hashlib.sha256(catalog).digest(), 44, len(metadata), len(keybytes))
        body += struct.pack('<32sIII', digest, 160, 160, 2) + metadata + keybytes
        (root / 'selection.v3.idx').write_bytes(body + hashlib.sha256(body).digest())
        return root

    def prepare_profile(self, root, padding=3):
        from retrofetch.native_profile import build_profile
        helper = self.helpers / 'fastfetch'
        helper.write_text('#!/usr/bin/python3\nimport json,sys\nprint("FASTFETCH:"+json.dumps(sys.argv[1:]))\nsys.exit(4 if "--logo" in sys.argv and "--logo-type" in sys.argv and '+repr(self.env.get('FAIL_RENDER') == '1')+' else 0)\n')
        helper.chmod(0o755)
        with mock.patch.dict(os.environ, self.env, clear=True), \
                mock.patch('retrofetch.runtime_cache.cached_capabilities', return_value=True), \
                mock.patch('retrofetch.calibration._config_files', return_value=[]), \
                mock.patch('retrofetch.calibration.layout_padding', return_value=padding):
            return build_profile(root, geometry=(80, 24, 8., 16.))

    def test_prepared_combined_uses_finite_fastfetch(self):
        root = self.index()
        self.prepare_profile(root)
        result = self.run_native(['--data-dir', str(root), 'art'], tty=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('FASTFETCH:', result.stdout)
        self.assertIn('--logo-padding-top', result.stdout)
        self.assertNotIn('FALLBACK:', result.stdout)
        self.assertEqual(result.stderr, '')

    def test_new_earlier_path_executable_invalidates_prepared_profile(self):
        earlier = self.home / 'earlier'
        earlier.mkdir()
        self.env['PATH'] = str(earlier) + ':' + self.env['PATH']
        root = self.index()
        self.prepare_profile(root)
        replacement = earlier / 'fastfetch'
        replacement.write_text('#!/bin/sh\necho UNBOUND_FASTFETCH\n')
        replacement.chmod(0o755)
        result = self.run_native(['--data-dir', str(root), 'art'], tty=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('FALLBACK:', result.stdout)
        self.assertNotIn('UNBOUND_FASTFETCH', result.stdout)

    def test_combined_failure_runs_plain_fastfetch(self):
        self.env['FAIL_RENDER'] = '1'
        root = self.index()
        self.prepare_profile(root)
        result = self.run_native(['--data-dir', str(root), 'art'], tty=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.count('FASTFETCH:'), 2)
        self.assertIn('FASTFETCH:[]', result.stdout)
        self.assertIn('falling back to Fastfetch', result.stderr)

    def test_profile_environment_or_dependency_change_falls_back(self):
        root = self.index()
        self.prepare_profile(root)
        self.env['COLORTERM'] = 'changed'
        result = self.run_native(['--data-dir', str(root)], tty=True)
        self.assertIn('FALLBACK:', result.stdout)
        self.env.pop('COLORTERM')
        helper = self.helpers / 'fastfetch'
        os.utime(helper, None)
        self.assertIn('FALLBACK:', self.run_native(['--data-dir', str(root)], tty=True).stdout)

    def test_prepared_filter_configuration(self):
        root = self.index()
        config = self.home / 'config/retrofetch/config.toml'
        config.parent.mkdir(parents=True)
        config.write_text('include=["retro"]')
        report = self.prepare_profile(root)
        self.assertEqual(report['eligible'], 1)
        result = self.run_native(['--data-dir', str(root), 'ART'], tty=True)
        self.assertIn('FASTFETCH:', result.stdout)
        config.write_text('exclude=["retro"]')
        self.assertIn('FALLBACK:', self.run_native(['--data-dir', str(root)], tty=True).stdout)

    def test_missing_padding_warning_and_startup_quiet(self):
        root = self.index()
        self.prepare_profile(root, padding=None)
        result = self.run_native(['--data-dir', str(root)], tty=True)
        self.assertIn('layout missing, stale, or top-positioned', result.stderr)
        result = self.run_native(['--data-dir', str(root), '--startup'], tty=True)
        self.assertEqual(result.stderr, '')

    def test_unknown_options_preserve_exact_argv(self):
        args = ['--animation-only', '--unknown', 'a b', '--size=2x3']
        result = self.run_native(args)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout.removeprefix('FALLBACK:')), args)

    def test_file_animation_only_uses_native_geometry(self):
        result = self.run_native(['--animation-only', '--file', str(self.gif)], tty=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('KITTEN:', result.stdout)
        self.assertIn('--use-window-size=20,10,160,160', result.stdout)
        self.assertNotIn('FALLBACK:', result.stdout)

    def test_startup_guard_is_silent(self):
        result = self.run_native(['--startup'])
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_named_index_and_duplicate_same_entry(self):
        root = self.index([('art', 1, 0), ('art', 1, 0)])
        result = self.run_native(['--animation-only', '--data-dir', str(root), 'ART'], tty=True)
        self.assertIn('KITTEN:', result.stdout)
        self.assertNotIn('FALLBACK:', result.stdout)

    def test_random_index(self):
        root = self.index()
        self.assertIn('KITTEN:', self.run_native(['--animation-only', '--data-dir', str(root)], tty=True).stdout)

    def test_damaged_index_falls_back_without_render(self):
        root = self.index()
        path = root / 'selection.v3.idx'
        original = path.read_bytes()
        for malformed in [b'', original[:-1], original[:100], original[:-32] + bytes(32)]:
            path.write_bytes(malformed)
            result = self.run_native(['--animation-only', '--data-dir', str(root)], tty=True)
            self.assertIn('FALLBACK:', result.stdout)
            self.assertNotIn('KITTEN:', result.stdout)

    def test_catalog_change_and_bad_key_ordinal_fall_back(self):
        root = self.index([('art', 0, 1)])
        result = self.run_native(['--animation-only', '--data-dir', str(root)], tty=True)
        self.assertIn('FALLBACK:', result.stdout)
        (root / 'catalog.json').write_bytes(b'changed')
        self.assertIn('FALLBACK:', self.run_native(['--animation-only', '--data-dir', str(root)], tty=True).stdout)

    def test_resigned_malformed_index_boundaries(self):
        root = self.index()
        path = root / 'selection.v3.idx'
        original = path.read_bytes()
        changes = [(8, struct.pack('<I', 99)), (12, struct.pack('<I', 1000001)),
                   (48, struct.pack('<Q', 2**64 - 1)), (56, struct.pack('<Q', 2**64 - 1)),
                   (64, struct.pack('<Q', 2**64 - 1)), (104, struct.pack('<I', 0)),
                   (108, struct.pack('<I', 0)), (112, struct.pack('<I', 1))]
        for offset, replacement in changes:
            body = bytearray(original[:-32])
            body[offset:offset + len(replacement)] = replacement
            path.write_bytes(body + hashlib.sha256(body).digest())
            result = self.run_native(['--animation-only', '--data-dir', str(root)], tty=True)
            self.assertIn('FALLBACK:', result.stdout)
            self.assertNotIn('KITTEN:', result.stdout)
        rng = random.Random(431)
        for _ in range(32):
            body = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 512)))
            path.write_bytes(body + hashlib.sha256(body).digest())
            result = self.run_native(['--animation-only', '--data-dir', str(root)], tty=True)
            self.assertIn('FALLBACK:', result.stdout)
            self.assertNotIn('AddressSanitizer', result.stderr)
            self.assertNotIn('runtime error:', result.stderr)

    def test_animation_helper_missing_is_failure_not_success(self):
        (self.helpers / 'kitten').unlink()
        self.env['PATH'] = str(self.helpers)
        result = self.run_native(['--animation-only', '--file', str(self.gif)], tty=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('kitten executable is unavailable', result.stderr)

    def test_config_and_combined_fall_back(self):
        self.assertIn('FALLBACK:', self.run_native(['--file', str(self.gif)], tty=True).stdout)
        config = self.home / 'config/retrofetch/config.toml'
        config.parent.mkdir(parents=True)
        config.write_text('size="1x1"')
        self.assertIn('FALLBACK:', self.run_native(['--animation-only', '--file', str(self.gif)], tty=True).stdout)

    def test_symlink_asset_falls_back(self):
        root = self.index()
        asset = next((root / 'animations').iterdir())
        asset.unlink()
        asset.symlink_to(self.gif)
        self.assertIn('FALLBACK:', self.run_native(['--animation-only', '--data-dir', str(root)], tty=True).stdout)


if __name__ == '__main__':
    unittest.main()
