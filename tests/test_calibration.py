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

    def test_fixed_height_reuses_rows_columns_font_and_size_without_measurement(self):
        with patch('retrofetch.calibration._measure', return_value=30) as measure:
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state,
                             layout_mode='fixed-height')
        measure.assert_called_once()
        before = self.state.read_bytes()
        with patch('retrofetch.calibration._measure', side_effect=AssertionError('startup measurement')):
            for geometry in ((120, 50, 10, 20), (80, 40, 10, 20), (120, 40, 7, 16)):
                with self.subTest(geometry=geometry):
                    self.assertEqual(layout_padding(str(self.config), None, geometry, 10,
                                                    state_file=self.state), 10)
            self.assertEqual(layout_padding(str(self.config), (12, 5), self.geometry, 5,
                                            state_file=self.state), 12)
        self.assertEqual(self.state.read_bytes(), before)

    def test_width_sensitive_profiles_coexist_and_match_effective_width(self):
        with patch('retrofetch.calibration._measure', return_value=30):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        second = (100, 40, 10, 20)
        with patch('retrofetch.calibration._measure', return_value=20):
            calibrate_layout(str(self.config), geometry=second, state_file=self.state)
        self.assertEqual(layout_padding(str(self.config), None, self.geometry, 8, state_file=self.state), 11)
        self.assertEqual(layout_padding(str(self.config), None, second, 8, state_file=self.state), 6)
        self.assertIsNone(layout_padding(str(self.config), None, (99, 40, 10, 20), 8, state_file=self.state))
        self.assertEqual(layout_padding(str(self.config), None, (120, 50, 10, 20), 8, state_file=self.state), 11)

    def test_environment_and_binary_identity_invalidate_fixed_height(self):
        with patch('retrofetch.calibration._measure', return_value=30):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state,
                             layout_mode='fixed-height')
        for key, value in (('TERM', 'different'), ('XDG_CONFIG_DIRS', '/different')):
            with self.subTest(environment=key), patch.dict(os.environ, {key: value}):
                self.assertIsNone(layout_padding(str(self.config), None, self.geometry, 8, state_file=self.state))
        changed = dict(os.environ)
        if 'NO_COLOR' in changed:
            changed.pop('NO_COLOR')
        else:
            changed['NO_COLOR'] = ''
        with patch.dict(os.environ, changed, clear=True):
            self.assertIsNone(layout_padding(str(self.config), None, self.geometry, 8, state_file=self.state))
        binary = self.root / 'other-fastfetch'
        binary.write_text('different executable')
        with patch('retrofetch.calibration.shutil.which', return_value=str(binary)):
            self.assertIsNone(layout_padding(str(self.config), None, self.geometry, 8, state_file=self.state))

    def test_legacy_backup_unknown_state_and_parent_symlinks(self):
        legacy = dict(owner='retrofetch-layout', version=1, key={}, reference_rows=29, position='left')
        self.state.write_text(json.dumps(legacy))
        original = self.state.read_bytes()
        self.assertIsNone(layout_padding(str(self.config), None, self.geometry, 8, state_file=self.state))
        with patch('retrofetch.calibration._measure', return_value=30):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        self.assertEqual(self.state.with_name(self.state.name + '.v1.bak').read_bytes(), original)
        self.assertEqual(json.loads(self.state.read_text())['version'], 2)
        self.state.write_text('{"owner":"someone-else","version":2,"profiles":[]}')
        with patch('retrofetch.calibration._measure') as measure, self.assertRaises(ValueError):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        measure.assert_not_called()
        outside = self.root / 'real'
        outside.mkdir()
        link = self.root / 'linked'
        link.symlink_to(outside, target_is_directory=True)
        with patch('retrofetch.calibration._measure') as measure, self.assertRaises(ValueError):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=link / 'layout.json')
        measure.assert_not_called()

    def test_fixed_height_auto_reevaluates_current_position(self):
        self.config.write_text('{"logo":{"position":"auto"}}')
        with patch('retrofetch.calibration._measure', return_value=30):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state,
                             layout_mode='fixed-height')
        self.assertEqual(layout_padding(str(self.config), None, (90, 40, 7, 16), 10, state_file=self.state), 10)
        self.assertIsNone(layout_padding(str(self.config), None, (40, 40, 7, 16), 10, state_file=self.state))

    def test_profiles_bound_replacement_and_size_compatibility(self):
        with patch('retrofetch.calibration._measure', return_value=30):
            for columns in range(100, 166):
                calibrate_layout(str(self.config), geometry=(columns, 40, 10, 20), state_file=self.state)
        document = json.loads(self.state.read_text())
        self.assertEqual(len(document['profiles']), 64)
        self.assertIsNone(layout_padding(str(self.config), None, (100, 40, 10, 20), 8, state_file=self.state))
        # A smaller logo box at wider viewport can leave the same info columns.
        self.assertEqual(layout_padding(str(self.config), (12, 5), (159, 40, 10, 20), 5,
                                        state_file=self.state), 12)
        with patch('retrofetch.calibration._measure', return_value=20):
            calibrate_layout(str(self.config), geometry=(165, 40, 10, 20), state_file=self.state)
        self.assertEqual(len(json.loads(self.state.read_text())['profiles']), 64)
        self.assertEqual(layout_padding(str(self.config), None, (165, 40, 10, 20), 8,
                                        state_file=self.state), 6)

    def test_invalid_modes_oversized_and_unrelated_backup_preserve_state(self):
        with patch('retrofetch.calibration._measure') as measure, self.assertRaises(ValueError):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state,
                             layout_mode='automatic')
        measure.assert_not_called()
        self.state.write_bytes(b' ' * (128 * 1024 + 1))
        before = self.state.read_bytes()
        with patch('retrofetch.calibration._measure') as measure, self.assertRaises(ValueError):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        measure.assert_not_called()
        self.assertEqual(self.state.read_bytes(), before)
        self.state.write_text(json.dumps(dict(owner='retrofetch-layout', version=1,
                                             key={}, reference_rows=29, position='left')))
        backup = self.state.with_name(self.state.name + '.v1.bak')
        backup.write_text('unrelated')
        before = self.state.read_bytes()
        with patch('retrofetch.calibration._measure') as measure, self.assertRaises(ValueError):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        measure.assert_not_called()
        self.assertEqual(backup.read_text(), 'unrelated')
        self.assertEqual(self.state.read_bytes(), before)

    def test_atomic_failure_keeps_owned_state_and_read_rejects_parent_symlink(self):
        with patch('retrofetch.calibration._measure', return_value=30):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state,
                             layout_mode='fixed-height')
        before = self.state.read_bytes()
        with patch('retrofetch.calibration._measure', return_value=40), \
             patch('retrofetch.calibration.os.replace', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        self.assertEqual(self.state.read_bytes(), before)
        link = self.root / 'linked'
        link.symlink_to(self.root, target_is_directory=True)
        self.assertIsNone(layout_padding(str(self.config), None, self.geometry, 8,
                                         state_file=link / self.state.name))

    def test_concurrent_explicit_writer_lock_refuses_without_measurement(self):
        import fcntl
        with patch('retrofetch.calibration._measure', return_value=30):
            calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
        before = self.state.read_bytes()
        lock_path = self.state.with_name(self.state.name + '.lock')
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch('retrofetch.calibration._measure') as measure, self.assertRaises(ValueError):
                calibrate_layout(str(self.config), geometry=self.geometry, state_file=self.state)
            measure.assert_not_called()
        self.assertEqual(self.state.read_bytes(), before)
