"""Explicit layout measurement never runs on the rendering hot path."""

import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from retrofetch.calibration import calibrate_layout, layout_padding
from retrofetch.calibration import _measure


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'fastfetch.jsonc'
        self.config.write_text('{"logo":{"position":"left","padding":{"left":2,"right":6}}}')
        self.state = self.root / 'layout.json'
        self.geometry = (120, 40, 10, 20)

    def test_explicit_calibration_measures_once_and_centers(self):
        with patch('retrofetch.calibration._measure', return_value=29) as measure:
            report = calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        measure.assert_called_once()
        self.assertEqual(report['reference_rows'], 29)
        with patch('retrofetch.calibration._measure', side_effect=AssertionError('startup measurement')):
            self.assertEqual(layout_padding(str(self.config), None, self.geometry, 8,
                                            state_file=self.state), 10)
        self.assertEqual(measure.call_args.args[1], 96)

    def test_missing_stale_and_corrupt_state_never_measures_or_writes(self):
        self.assertIsNone(layout_padding(str(self.config), None, self.geometry, 8, state_file=self.state))
        with patch('retrofetch.calibration._measure', return_value=29):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        before = self.state.read_bytes()
        self.assertIsNone(layout_padding(str(self.config), None, (80, 40, 10, 20), 8,
                                         state_file=self.state))
        self.config.write_text('{"logo":{"position":"right"}}')
        self.assertIsNone(layout_padding(str(self.config), None, self.geometry, 8, state_file=self.state))
        self.assertEqual(self.state.read_bytes(), before)
        self.state.write_text('[]')
        self.assertIsNone(layout_padding(str(self.config), None, self.geometry, 8, state_file=self.state))

    def test_top_layout_does_not_get_vertical_padding(self):
        self.config.write_text('{"logo":{"position":"top"}}')
        with patch('retrofetch.calibration._measure', return_value=29):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        self.assertIsNone(layout_padding(str(self.config), None, self.geometry, 8, state_file=self.state))

    def test_failed_measurement_preserves_existing_state_and_fastfetch_config(self):
        config = self.config.read_bytes()
        with patch('retrofetch.calibration._measure', return_value=29):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        before = self.state.read_bytes()
        with patch('retrofetch.calibration._measure', side_effect=ValueError('measurement failed')):
            with self.assertRaises(ValueError):
                calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.config.read_bytes(), config)

    def test_invalid_height_and_symlink_state_are_rejected(self):
        with patch('retrofetch.calibration._measure', return_value=0):
            with self.assertRaises(ValueError):
                calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        outside = self.root / 'outside'
        outside.write_text('preserve')
        self.state.symlink_to(outside)
        with patch('retrofetch.calibration._measure', return_value=29):
            with self.assertRaises(ValueError):
                calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        self.assertEqual(outside.read_text(), 'preserve')

    def test_measurement_keeps_native_wrapping_and_color_policy(self):
        import subprocess
        real = subprocess.Popen
        commands = []

        def launch(command, **kwargs):
            commands.append(command)
            return real(['python3', '-c',
                         "import sys; sys.stdout.write('\\x1b[?7l' + ('x'*120+'\\n')*29 + '\\x1b[?7h')"], **kwargs)

        with patch('retrofetch.calibration.subprocess.Popen', side_effect=launch):
            self.assertEqual(_measure('none', 96, self.geometry), 29)
        self.assertNotIn('--pipe', commands[0])

    @unittest.skipUnless(shutil.which('fastfetch'), 'Fastfetch unavailable')
    def test_real_nowrap_module_height_is_not_counted_as_wrapped(self):
        script = self.root / 'module.py'
        script.write_text('print("\\n".join("X" * 120 for _ in range(29)))\n')
        self.config.write_text(json.dumps({
            'logo': {'position': 'left', 'padding': {'left': 2, 'right': 6}},
            'display': {'disableLinewrap': True},
            'modules': [{'type': 'command', 'key': '',
                         'text': shlex.quote(sys.executable) + ' ' + shlex.quote(str(script))}],
        }))
        environment = {k: v for k, v in os.environ.items() if k not in ('NO_COLOR', 'TMUX', 'STY')}
        with patch.dict(os.environ, {**environment, 'TERM': 'xterm-kitty'}, clear=True):
            report = calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
            self.assertEqual(report['reference_rows'], 29)
            self.assertEqual(layout_padding(str(self.config), None, self.geometry, 8,
                                            state_file=self.state), 10)
