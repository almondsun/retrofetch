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
import stat
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
    if config == 'none' or (config is None and 'NO_CONFIG' in os.environ):
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


def _key(config, files, *, legacy=False, executable=None):
    from .executable_identity import executable_identity
    if executable is None:
        selected = shutil.which('fastfetch')
        executable = executable_identity(selected, legacy=legacy) if selected else None
    return {'config': str(Path(config).expanduser().absolute()) if config not in (None, 'none') else config,
            'environment': {name: os.environ.get(name) for name in
                            ('HOME', 'XDG_CONFIG_HOME', 'XDG_CONFIG_DIRS', 'TERM',
                             'COLORTERM', 'CLICOLOR', 'CLICOLOR_FORCE')},
            # Fastfetch tests presence, not truthiness, of NO_COLOR.
            'no_color': 'NO_COLOR' in os.environ, 'no_config': 'NO_CONFIG' in os.environ,
            'profile': [2, 160, 160], 'files': _snapshots(files),
            'executable': executable}


def _logo_options(files):
    """Read unresolved logo placement shared by runtime and offline profiles."""
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
    return position, left + right


def _logo(files, columns, logo_width):
    position, horizontal_padding = _logo_options(files)
    if position == 'auto':
        position = 'left' if columns - logo_width - horizontal_padding >= 32 else 'top'
    return position, horizontal_padding


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


MAX_STATE_BYTES = 128 * 1024
MAX_PROFILES = 64
MODES = ('fixed-height', 'width-sensitive')


def _safe_state(path):
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('Calibration state must not use symlinks')
    return path


def _read_state(path):
    path = _safe_state(path)
    if not path.exists():
        return None, None
    if not path.is_file() or path.stat().st_size > MAX_STATE_BYTES:
        raise ValueError('Calibration state must be a bounded regular file')
    raw = path.read_bytes()
    if len(raw) > MAX_STATE_BYTES:
        raise ValueError('Calibration state exceeds limit')
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise ValueError('Refusing to replace unrecognized calibration state') from error
    if not isinstance(document, dict) or document.get('owner') != OWNER:
        raise ValueError('Refusing to replace unrecognized calibration state')
    version = document.get('version')
    if type(version) is not int or version not in (1, 2, 3):
        raise ValueError('Unrecognized calibration version')
    if version == 1:
        if (not isinstance(document.get('key'), dict)
                or type(document.get('reference_rows')) is not int
                or not 1 <= document['reference_rows'] <= 10000
                or document.get('position') not in ('left', 'right', 'top')):
            raise ValueError('Malformed legacy calibration state')
        return document, raw
    profiles = document.get('profiles')
    if not isinstance(profiles, list) or len(profiles) > MAX_PROFILES:
        raise ValueError('Invalid calibration profiles')
    for profile in profiles:
        if (not isinstance(profile, dict) or profile.get('mode') not in MODES
                or not isinstance(profile.get('key'), dict)
                or type(profile.get('reference_rows')) is not int
                or not 1 <= profile['reference_rows'] <= 10000):
            raise ValueError('Invalid calibration profile')
        columns = profile.get('information_columns')
        if ((profile['mode'] == 'fixed-height' and columns is not None)
                or (profile['mode'] == 'width-sensitive'
                    and (type(columns) is not int or not 1 <= columns <= 65535))):
            raise ValueError('Invalid calibration information width')
        snapshots = profile['key'].get('files')
        if (not isinstance(snapshots, list) or len(snapshots) > 64
                or any(not isinstance(item, list) or len(item) != 2
                       or not isinstance(item[0], str) or not Path(item[0]).is_absolute()
                       or (item[1] is not None and
                           (not isinstance(item[1], str) or not re.fullmatch('[0-9a-f]{64}', item[1])))
                       for item in snapshots)):
            raise ValueError('Invalid calibration configuration snapshots')
    return document, raw


def _information_area(files, geometry, size):
    _, _, width, _ = display_geometry(160, 160, geometry, size)
    position, horizontal_padding = _logo(files, geometry[0], width)
    columns = geometry[0] if position == 'top' else max(1, geometry[0] - width - horizontal_padding)
    return position, columns


def calibrate_layout(config=None, size=None, *, geometry=None, state_file=None,
                     layout_mode='width-sensitive'):
    """Serialize explicit maintenance; startup only reads the atomic state file."""
    path = _safe_state(Path(state_file) if state_file is not None else _state_file())
    _read_state(path)  # Reject unknown state before creating maintenance files.
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _safe_state(path.with_name(path.name + '.lock'))
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'a') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ValueError('Calibration lock must be a regular file')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another calibration is running') from error
        return _calibrate_locked(config, size, geometry=geometry, state_file=path,
                                 layout_mode=layout_mode)


def _calibrate_locked(config=None, size=None, *, geometry=None, state_file=None,
                      layout_mode='width-sensitive'):
    """Measure once, explicitly asserting either a width or stable-height contract."""
    if layout_mode not in MODES:
        raise ValueError('layout mode must be fixed-height or width-sensitive')
    geometry = geometry or terminal_geometry()
    path = _safe_state(Path(state_file) if state_file is not None else _state_file())
    previous, previous_raw = _read_state(path)
    files = _config_files(config)
    key = _key(config, files)
    legacy_key = _key(config, files, legacy=True)
    position, columns = _information_area(files, geometry, size)
    legacy_version = previous['version'] if previous and previous['version'] < 3 else None
    backup = _safe_state(path.with_name(path.name + f'.v{legacy_version}.bak'))
    if legacy_version is not None and backup.exists():
        if not backup.is_file() or backup.stat().st_size > MAX_STATE_BYTES or backup.read_bytes() != previous_raw:
            raise ValueError('Refusing to replace an unrelated legacy calibration backup')
    rows = _measure(config, columns, geometry)
    if type(rows) is not int or not 1 <= rows <= 10000:
        raise ValueError('Calibration did not produce a bounded information block')
    profile = {'mode': layout_mode, 'key': key, 'reference_rows': rows,
               'information_columns': columns if layout_mode == 'width-sensitive' else None}
    profiles = previous['profiles'] if previous and previous['version'] == 3 else []
    # The most recently calibrated compatible contract wins, with bounded history.
    profiles = [item for item in profiles if not (item['mode'] == layout_mode
                and item['key'] == key and item['information_columns'] == profile['information_columns'])]
    document = {'owner': OWNER, 'version': 3, 'profiles': (profiles + [profile])[-MAX_PROFILES:]}
    encoded = (json.dumps(document) + '\n').encode()
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError('Calibration state exceeds limit; remove obsolete profiles explicitly')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.layout-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encoded)
        if (_key(config, files) != key
                or _key(config, files, legacy=True) != legacy_key):
            raise ValueError('Configuration changed during calibration')
        _safe_state(path)
        _, current_raw = _read_state(path)
        if current_raw != previous_raw:
            raise ValueError('Calibration state changed during measurement')
        if legacy_version is not None and not backup.exists():
            _safe_state(backup)
            with backup.open('xb') as stream:
                stream.write(previous_raw)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return {'reference_rows': rows, 'position': position, 'information_columns': columns,
            'layout_mode': layout_mode, 'state_file': str(path)}


def migrate_layout_state(*, state_file=None):
    """Explicitly migrate proven v2 contracts, or require recalibration unchanged.

    No measurements run here. A stale contract must be calibrated explicitly in
    its existing mode; it cannot be made trustworthy merely by hashing today.
    """
    path = _safe_state(Path(state_file) if state_file is not None else _state_file())
    document, raw = _read_state(path)
    if document is None or document['version'] == 3:
        return {'profiles': len(document['profiles']) if document else 0,
                'state_file': str(path), 'migrated': False}
    if document['version'] != 2:
        raise ValueError('Explicit recalibration is required for legacy layout state')
    lock_path = _safe_state(path.with_name(path.name + '.lock'))
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'a') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ValueError('Calibration lock must be a regular file')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another calibration is running') from error
        profiles = []
        checks = []
        for profile in document['profiles']:
            old = profile['key']
            files = [item[0] for item in old['files']]
            config = old.get('config')
            if _key(config, files, legacy=True) != old:
                raise ValueError('Explicit recalibration is required for stale layout contracts')
            key = _key(config, files)
            # Recheck legacy evidence after hashing; content alone cannot justify
            # migration from a metadata-only historical contract.
            if _key(config, files, legacy=True) != old:
                raise ValueError('Configuration changed during layout migration')
            profiles.append({**profile, 'key': key})
            checks.append((config, files, old, key))
        encoded = (json.dumps({'owner': OWNER, 'version': 3, 'profiles': profiles}) + '\n').encode()
        if len(encoded) > MAX_STATE_BYTES:
            raise ValueError('Calibration state exceeds limit')
        backup = _safe_state(path.with_name(path.name + '.v2.bak'))
        if backup.exists() and (not backup.is_file() or backup.read_bytes() != raw):
            raise ValueError('Refusing to replace an unrelated legacy calibration backup')
        fd, temporary = tempfile.mkstemp(prefix='.layout-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encoded)
            for config, files, old, key in checks:
                if _key(config, files, legacy=True) != old or _key(config, files) != key:
                    raise ValueError('Configuration changed during layout migration')
            if _read_state(path)[1] != raw:
                raise ValueError('Calibration state changed during migration')
            if not backup.exists():
                with backup.open('xb') as stream:
                    stream.write(raw)
            _safe_state(path)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return {'profiles': len(profiles), 'state_file': str(path), 'migrated': True}


def layout_padding(config, size, geometry, display_height, *, state_file=None):
    """Read compatible profiles without executing modules or writing state."""
    path = Path(state_file) if state_file is not None else _state_file()
    try:
        document, _ = _read_state(path)
        if document is None or document['version'] not in (2, 3):
            return None  # Never promote a version-1 snapshot into a new contract.
        from .executable_identity import executable_identity
        selected = shutil.which('fastfetch')
        executable = (executable_identity(selected, legacy=document['version'] == 2)
                      if selected else None)
        current_keys = {}
        for profile in reversed(document['profiles']):
            key = profile['key']
            files = [record[0] for record in key['files']]
            identity = tuple(files)
            if identity not in current_keys:
                current_keys[identity] = _key(config, files, legacy=document['version'] == 2,
                                              executable=executable)
            if current_keys[identity] != key:
                continue
            position, columns = _information_area(files, geometry, size)
            if position == 'top':
                return None
            if profile['mode'] == 'width-sensitive' and columns != profile['information_columns']:
                continue
            return max(0, (profile['reference_rows'] - display_height) // 2)
        return None
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return None



def layout_contracts(config=None, *, state_file=None):
    """Extract newest-first compatible contracts without binding terminal size.

    Different placement rules cannot share the v2 profile header. Refuse that
    unusual historical state rather than silently changing Python precedence.
    """
    path = Path(state_file) if state_file is not None else _state_file()
    try:
        document, _ = _read_state(path)
        if document is None or document['version'] not in (2, 3):
            return 0, 0, []
        from .executable_identity import executable_identity
        selected = shutil.which('fastfetch')
        executable = (executable_identity(selected, legacy=document['version'] == 2)
                      if selected else None)
        current_keys = {}
        candidates = []
        placement = None
        divergent = False
        for profile in reversed(document['profiles']):
            key = profile['key']
            files = [record[0] for record in key['files']]
            identity = tuple(files)
            if identity not in current_keys:
                current_keys[identity] = _key(config, files, legacy=document['version'] == 2,
                                              executable=executable)
            if current_keys[identity] != key:
                continue
            position, padding = _logo_options(files)
            options = (('left', 'right', 'top', 'auto').index(position), padding)
            if placement is not None and placement != options:
                divergent = True
                break
            placement = options
            width_sensitive = profile['mode'] == 'width-sensitive'
            candidates.append((int(width_sensitive), profile['reference_rows'],
                               profile['information_columns'] if width_sensitive else 0))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return 0, 0, []
    if divergent:
        raise ValueError('Native profile cannot represent differing historical logo placement')
    return (*placement, candidates) if placement is not None else (0, 0, [])
