"""Execute opt-in shell guards without opening terminals or rendering graphics."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


class StartupHookTests(unittest.TestCase):
    def check_shell(self, shell, extension, interactive_flags, plain_flags):
        executable = shutil.which(shell)
        if executable is None:
            self.skipTest(f'{shell} unavailable; CI installs it for executable guard checks')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'bin'
            binary.mkdir()
            capture = root / 'calls'
            launcher = binary / 'retrofetch'
            launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CAPTURE"\n')
            launcher.chmod(0o755)
            command = 'source ' + shlex.quote(str(EXAMPLES / f'startup.{extension}'))
            for case, interactive, terminal, extra, available, expected in (
                ('interactive-kitty', True, 'xterm-kitty', {}, True, '--startup\n'),
                ('noninteractive', False, 'xterm-kitty', {}, True, ''),
                ('other-terminal', True, 'xterm', {}, True, ''),
                ('tmux', True, 'xterm-kitty', {'TMUX': 'session'}, True, ''),
                ('screen', True, 'xterm-kitty', {'STY': 'session'}, True, ''),
                ('missing-command', True, 'xterm-kitty', {}, False, ''),
            ):
                with self.subTest(shell=shell, case=case):
                    capture.unlink(missing_ok=True)
                    env = {'HOME': str(root), 'TERM': terminal, 'CAPTURE': str(capture),
                           'PATH': str(binary) if available else str(root / 'empty'), **extra}
                    flags = interactive_flags if interactive else plain_flags
                    result = subprocess.run([executable, *flags, command], env=env,
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(capture.read_text() if capture.exists() else '', expected)

    def test_bash_guards(self):
        self.check_shell('bash', 'bash', ['--noprofile', '--norc', '-i', '-c'],
                         ['--noprofile', '--norc', '-c'])

    def test_zsh_guards(self):
        self.check_shell('zsh', 'zsh', ['-f', '-i', '-c'], ['-f', '-c'])

    def test_fish_guards(self):
        self.check_shell('fish', 'fish', ['--no-config', '--interactive', '--command'],
                         ['--no-config', '--command'])
