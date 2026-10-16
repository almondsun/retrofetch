"""Public CLI checks without emitting graphics to a real terminal."""

import io
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from retrofetch.cli import main


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.gif = Path(self.directory.name) / "retro $(touch unexpected).gif"
        self.gif.write_bytes(b"GIF89a\x64\x00\x64\x00")
        self.output = io.StringIO()
        self.output.isatty = lambda: True
        self.errors = io.StringIO()

    def invoke(self, *args, results=None, environment=None):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs))
            if "--list-features" in command:
                return subprocess.CompletedProcess(command, 0, "imagemagick7\n")
            if "--help" in command:
                return subprocess.CompletedProcess(command, 0, "0 plays the animation\n")
            result = next(results, 0) if results else 0
            if isinstance(result, Exception):
                raise result
            return subprocess.CompletedProcess(command, result)

        env = {"TERM": "xterm-kitty"} if environment is None else environment
        with patch.dict(os.environ, env, clear=True), \
             patch("sys.stdout", self.output), patch("sys.stderr", self.errors), \
             patch("retrofetch.rendering.subprocess.run", side_effect=run), \
             patch("retrofetch.rendering.terminal_geometry", return_value=(120, 40, 10, 20)):
            code = main(list(args))
        return code, calls

    def test_combined_uses_native_animation_and_quotes_source(self):
        code, calls = self.invoke("--file", str(self.gif))
        self.assertEqual(code, 0)
        command, options = calls[-1]
        self.assertEqual(command[:3], ["fastfetch", "--logo-type", "kitty"])
        self.assertEqual(command[command.index("--logo") + 1], shlex.quote(str(self.gif)))
        self.assertEqual(command[command.index("--logo-animation-frame") + 1], "0")
        self.assertNotIn("--config", command)
        self.assertFalse(any(arg.startswith("--color") for arg in command))
        self.assertNotIn("shell", options)
        self.assertNotIn("capture_output", options)

    def test_explicit_fastfetch_config_is_forwarded_unchanged(self):
        config = Path(self.directory.name) / "custom.jsonc"
        original = b'{"modules":["title"],"display":{"separator":" => "}}'
        config.write_bytes(original)
        code, calls = self.invoke("--file", str(self.gif), "--fastfetch-config", str(config))
        self.assertEqual(code, 0)
        self.assertEqual(calls[-1][0][-2:], ["--config", str(config)])
        self.assertEqual(config.read_bytes(), original)

    def test_animation_only_uses_kitten_and_never_fastfetch(self):
        code, calls = self.invoke("--file", str(self.gif), "--animation-only")
        self.assertEqual(code, 0)
        command = calls[-1][0]
        self.assertEqual(command[:2], ["kitten", "icat"])
        self.assertIn("--loop=-1", command)
        self.assertIn("--stdin=no", command)
        self.assertFalse(any(call[0][0] == "fastfetch" for call in calls))
        self.assertFalse(any(arg.startswith("--place") for arg in command))

    def test_size_override_preserves_aspect_and_fits_screen(self):
        code, calls = self.invoke("--file", str(self.gif), "--size", "12x8")
        self.assertEqual(code, 0)
        command = calls[-1][0]
        self.assertEqual(command[command.index("--logo-width") + 1], "12")
        self.assertNotIn("--logo-height", command)

    def test_render_failure_falls_back_and_propagates_fallback_exit(self):
        code, calls = self.invoke("--file", str(self.gif), results=iter([1, 7]))
        self.assertEqual(code, 7)
        self.assertEqual(calls[-1][0], ["fastfetch"])
        self.assertIn("falling back", self.errors.getvalue())

    def test_missing_native_capability_falls_back_without_rendering(self):
        with patch("retrofetch.rendering.supports_animation", return_value=False):
            code, calls = self.invoke("--file", str(self.gif))
        self.assertEqual(code, 0)
        self.assertEqual([call[0] for call in calls], [["fastfetch"]])

    def test_animation_only_failure_is_explicit(self):
        code, calls = self.invoke("--file", str(self.gif), "--animation-only", results=iter([2]))
        self.assertEqual(code, 2)
        self.assertIn("animation rendering failed", self.errors.getvalue())
        self.assertEqual(len(calls), 1)

    def test_missing_helper_is_reported_without_traceback(self):
        code, _ = self.invoke("--file", str(self.gif), "--animation-only", results=iter([FileNotFoundError()]))
        self.assertEqual(code, 1)
        self.assertIn("kitten", self.errors.getvalue())

    def test_startup_skips_unsupported_terminal_without_output(self):
        code, calls = self.invoke("--startup", environment={"TERM": "dumb"})
        self.assertEqual((code, calls), (0, []))
        self.assertEqual(self.output.getvalue() + self.errors.getvalue(), "")

    def test_startup_rejects_incompatible_options_before_terminal_guard(self):
        for terminal in ("dumb", "xterm-kitty"):
            with self.subTest(terminal=terminal):
                with self.assertRaises(SystemExit) as error:
                    self.invoke("--startup", "--animation-only", "--fastfetch-config",
                                "custom.jsonc", environment={"TERM": terminal})
                self.assertEqual(error.exception.code, 2)
        self.assertIn("cannot be used", self.errors.getvalue())

    def test_manual_unsupported_terminal_fails_without_commands(self):
        code, calls = self.invoke("--file", str(self.gif), environment={"TERM": "dumb"})
        self.assertEqual((code, calls), (1, []))
        self.assertIn("Kitty", self.errors.getvalue())

    def test_invalid_gif_is_rejected_without_commands_or_data_changes(self):
        self.gif.write_bytes(b"not a GIF")
        before = self.gif.read_bytes()
        code, calls = self.invoke("--file", str(self.gif))
        self.assertEqual((code, calls), (1, []))
        self.assertEqual(self.gif.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
