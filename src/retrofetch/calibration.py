"""Explicit Fastfetch layout calibration; rendering only reads the saved result."""

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import pty
import re
import select
import shutil
import struct
import subprocess
import tempfile
import termios
import time
import unicodedata

from .rendering import DEFAULT_BOX, display_geometry, terminal_geometry

OWNER = 'retrofetch-layout'


def _state_file():
    base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    return base / 'retrofetch/layout.json'


def _jsonc(text):
    # Match quoted strings first, so URLs and literal comment markers survive.
    text = re.sub(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\r\n]*',
                  lambda m: m[0] if m[0].startswith('"') else '', text, flags=re.S)
    text = re.sub(r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])',
                  lambda m: m[0] if m[0].startswith('"') else '', text)
    return json.loads(text)


def _config_files(config):
    if config == 'none':
        return []
    if config is not None:
        path = Path(config).expanduser().absolute()
        if not path.is_file():
            raise ValueError('Calibration requires an existing config file or --fastfetch-config none; presets remain renderable without calibration')
        return [str(path)]
    result = subprocess.run(['fastfetch', '--list-config-paths'], capture_output=True,
                            text=True, stdin=subprocess.DEVNULL, timeout=5)
    if result.returncode:
        raise ValueError('Cannot discover Fastfetch configuration paths')
    directories = [line.removesuffix(' (*)').strip() for line in result.stdout.splitlines()]
    if len(directories) > 32 or any(not Path(p).is_absolute() for p in directories):
        raise ValueError('Unrecognized Fastfetch configuration search paths')
    return [str(Path(directory) / name) for directory in directories
            for name in ('config.jsonc', 'config.json5')]


def _snapshots(files):
    if not isinstance(files, list) or len(files) > 64 or any(not isinstance(p, str) for p in files):
        raise ValueError('Invalid calibration configuration list')
    values = []
    for value in files:
        path = Path(value)
        digest = None
        if path.exists():
            if not path.is_file() or path.stat().st_size > 512 * 1024:
                raise ValueError('Calibration configuration is not a bounded regular file')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        values.append([str(path), digest])
    return values


def _key(config, size, geometry, files):
    executable = shutil.which('fastfetch')
    stat = Path(executable).stat() if executable else None
    return {'config': str(Path(config).expanduser().absolute()) if config not in (None, 'none') else config,
            'environment': [os.environ.get(k) for k in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CONFIG_DIRS')],
            'geometry': list(geometry), 'size': list(size or DEFAULT_BOX), 'profile': [1, 160, 160],
            'files': _snapshots(files),
            'executable': [executable, stat.st_size, stat.st_mtime_ns] if stat else None}


def _logo(files, columns, logo_width):
    options = {}
    for name in files:
        path = Path(name)
        if path.is_file():
            try:
                document = _jsonc(path.read_text())
                options = document.get('logo', {})
                if not isinstance(options, dict):
                    options = {}
                break
            except (ValueError, AttributeError) as error:
                raise ValueError('Calibration supports JSON/JSONC configuration, not extended JSON5 syntax') from error
    position = options.get('position', 'left')
    padding = options.get('padding', {})
    if not isinstance(padding, dict):
        raise ValueError('Unsupported Fastfetch padding configuration')
    left, right = padding.get('left', 0), padding.get('right', 4)
    if position not in ('auto', 'left', 'right', 'top') or any(type(v) is not int or not 0 <= v <= 10000 for v in (left, right)):
        raise ValueError('Unsupported Fastfetch logo layout')
    if position == 'auto':
        position = 'left' if columns - logo_width - left - right >= 32 else 'top'
    return position, left + right


def _visible_rows(output, columns):
    text = output.decode('utf-8', errors='replace')
    nowrap = '\x1b[?7l' in text
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
    text = re.sub(r'\x1b\[[0-9;?]*[mhl]', '', text)
    if '\x1b' in text or '\x00' in text:
        raise ValueError('Cannot calibrate output containing cursor positioning or unsupported terminal controls')
    rows = 0
    for line in text.splitlines():
        width = 0
        for character in line:
            if character == '\t':
                width += 8 - width % 8
            elif not unicodedata.combining(character):
                width += 2 if unicodedata.east_asian_width(character) in ('W', 'F') else 1
        rows += 1 if nowrap else max(1, math.ceil(width / columns))
    return rows


def _measure(config, columns, geometry):
    """Run modules once, on a private PTY at the information area's width."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ,
                struct.pack('HHHH', geometry[1], columns,
                            min(65535, int(columns * geometry[2])),
                            min(65535, int(geometry[1] * geometry[3]))))
    # A private PTY retains native wrap/color policy. Pipe mode would suppress
    # Fastfetch's disableLinewrap control and incorrectly count clipped text.
    command = ['fastfetch', '--logo', 'none']
    if config is not None:
        command.extend(['--config', config])
    error_file = tempfile.TemporaryFile()
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=slave, stderr=error_file)
        os.close(slave)
        slave = None
        output = bytearray()
        deadline = time.monotonic() + 15
        while True:
            if time.monotonic() > deadline:
                raise ValueError('Fastfetch calibration timed out')
            if select.select([master], [], [], 0.05)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > 2 * 1024 * 1024:
                    raise ValueError('Fastfetch calibration output exceeds limit')
            elif process.poll() is not None:
                break
        if process.wait(timeout=1):
            raise ValueError('Fastfetch calibration failed')
        return _visible_rows(output, columns)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        if slave is not None:
            os.close(slave)
        error_file.close()


def calibrate_layout(config=None, size=None, *, geometry=None, state_file=None):
    geometry = geometry or terminal_geometry()
    path = Path(state_file) if state_file is not None else _state_file()
    files = _config_files(config)
    key = _key(config, size, geometry, files)
    _, _, width, _ = display_geometry(160, 160, geometry, size)
    position, horizontal_padding = _logo(files, geometry[0], width)
    columns = geometry[0] if position == 'top' else max(1, geometry[0] - width - horizontal_padding)
    rows = _measure(config, columns, geometry)
    if type(rows) is not int or not 1 <= rows <= 10000:
        raise ValueError('Calibration did not produce a bounded information block')
    document = {'owner': OWNER, 'version': 1, 'key': key, 'reference_rows': rows, 'position': position}
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('Calibration state must not use symlinks')
    if path.exists():
        try:
            previous = json.loads(path.read_text())
        except (ValueError, OSError) as error:
            raise ValueError('Refusing to replace unrecognized calibration state') from error
        if not isinstance(previous, dict) or previous.get('owner') != OWNER:
            raise ValueError('Refusing to replace unrecognized calibration state')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.layout-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(document, stream)
            stream.write('\n')
        if _key(config, size, geometry, files) != key:
            raise ValueError('Configuration changed during calibration')
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return {'reference_rows': rows, 'position': position, 'information_columns': columns, 'state_file': str(path)}


def layout_padding(config, size, geometry, display_height, *, state_file=None):
    path = Path(state_file) if state_file is not None else _state_file()
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 128 * 1024:
            return None
        document = json.loads(path.read_text())
        if not isinstance(document, dict) or document.get('owner') != OWNER or document.get('version') != 1:
            return None
        rows = document.get('reference_rows')
        if type(rows) is not int or not 1 <= rows <= 10000:
            return None
        key = document.get('key')
        if not isinstance(key, dict) or not isinstance(key.get('files'), list):
            return None
        files = [record[0] for record in key['files'] if isinstance(record, list) and len(record) == 2]
        if _key(config, size, geometry, files) != key or document.get('position') not in ('left', 'right'):
            return None
        return max(0, (rows - display_height) // 2)
    except (OSError, ValueError, TypeError, KeyError):
        return None
