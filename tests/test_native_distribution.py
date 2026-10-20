"""Failure diagnostics for the offline release smoke runner."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
with mock.patch.object(sys, 'path', [str(SCRIPTS), *sys.path]):
    spec = importlib.util.spec_from_file_location('native_distribution_check', SCRIPTS / 'check_native_distribution.py')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)


class NativeDistributionTests(unittest.TestCase):
    def test_failed_command_prints_captured_streams_and_preserves_failure(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()) as diagnostic:
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                checker.run([sys.executable, '-c',
                             'import sys; print("compiler output"); print("compiler error", file=sys.stderr); sys.exit(7)'],
                            cwd=directory, env=dict(os.environ))
        self.assertEqual(raised.exception.returncode, 7)
        self.assertEqual(raised.exception.stdout, 'compiler output\n')
        self.assertEqual(raised.exception.stderr, 'compiler error\n')
        self.assertEqual(diagnostic.getvalue(), 'compiler output\ncompiler error\n')

    def test_timeout_prints_partial_streams_and_preserves_exception(self):
        for output in [('partial output\n', 'partial error\n'), (b'partial output\n', b'partial error\n'), (None, None)]:
            with self.subTest(output=output):
                error = subprocess.TimeoutExpired(['compiler'], 120, output=output[0], stderr=output[1])
                with mock.patch.object(checker.subprocess, 'run', side_effect=error) as run:
                    with contextlib.redirect_stderr(io.StringIO()) as diagnostic:
                        with self.assertRaises(subprocess.TimeoutExpired) as raised:
                            checker.run(['compiler'], cwd=SCRIPTS, env={})
                self.assertIs(raised.exception, error)
                self.assertEqual(diagnostic.getvalue(), '' if output[0] is None else 'partial output\npartial error\n')
                self.assertEqual(run.call_args.kwargs['timeout'], 120)

    def test_success_retains_captured_output_without_printing(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()) as diagnostic:
            result = checker.run([sys.executable, '-c', 'import sys; print("ready"); print("notice", file=sys.stderr)'],
                                 cwd=directory, env=dict(os.environ))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, 'ready\n', 'notice\n'))
        self.assertEqual(diagnostic.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
