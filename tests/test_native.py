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


if __name__ == '__main__':
    unittest.main()
