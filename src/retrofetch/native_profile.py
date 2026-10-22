"""Explicit offline preparation of a tightly bound native rendering profile.

The frontend never trusts executable names or arbitrary arguments from this file.
A profile accelerates only its exact environment and dependency bytes; validated
layout contracts adapt to current terminal geometry.
"""
import hashlib
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile

MAGIC = b'RTFXNAT2'
NAME = 'native-runtime.v2.bin'
MAX_BYTES = 128 * 1024 * 1024
ENVIRONMENT_KEYS = (
    'HOME', 'PATH', 'XDG_CONFIG_HOME', 'XDG_CONFIG_DIRS', 'XDG_STATE_HOME',
    'XDG_DATA_HOME', 'TERM', 'COLORTERM', 'CLICOLOR', 'CLICOLOR_FORCE',
    'NO_COLOR', 'NO_CONFIG', 'RETROFETCH_DATA_DIR', 'TMUX', 'STY',
)


def _string(value):
    raw = os.fsencode(value)
    if not raw or b'\0' in raw or len(raw) > 1024 * 1024:
        raise ValueError('Native profile string exceeds bounds')
    return struct.pack('<I', len(raw)) + raw


def _snapshot(path):
    path = path.absolute()
    if path.exists():
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError('Native profile dependency is not a bounded regular file')
        before = path.stat()
        digest = hashlib.sha256(path.read_bytes()).digest()
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        old = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        if identity != old:
            raise ValueError('Native profile dependency changed during preparation')
        exists = 1
    else:
        digest, exists, identity = bytes(32), 0, (0,) * 5
    return _string(str(path)) + bytes([exists]) + digest + struct.pack('<5Q', *identity)


def build_profile(data_dir, *, geometry=None):
    """Publish a profile during explicit maintenance, never during startup.

    Custom size/Fastfetch config remain Python-only. Geometry is injectable for
    isolated tests; otherwise capture the current terminal without any probes.
    """
    from .calibration import _config_files, _read_state, _state_file, layout_contracts
    from .catalog import Catalog
    from .config import load_settings
    from .rendering import terminal_geometry
    from .runtime_cache import _path, cached_capabilities

    root = Path(data_dir).resolve(strict=True)
    environment = sorted((key, os.environ[key]) for key in ENVIRONMENT_KEYS if key in os.environ)
    config_base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config').expanduser()
    dependencies = [root / 'catalog.json', root / 'selection.v3.idx',
                    config_base / 'retrofetch/config.toml', _path(None), _state_file()]
    executable = shutil.which('fastfetch')
    if executable:
        dependencies.append(Path(executable).absolute())
    snapshots = {path: _snapshot(path) for path in dependencies}
    settings = load_settings()
    if settings.size is not None or settings.fastfetch_config is not None:
        raise ValueError('Native profile does not support configured size or Fastfetch config')
    geometry = terminal_geometry() if geometry is None else geometry
    columns, rows, cell_width, cell_height = geometry
    if not (2 <= columns <= 65535 and 3 <= rows <= 65535
            and math.isfinite(cell_width) and math.isfinite(cell_height)
            and 0 < cell_width <= 65535 and 0 < cell_height <= 65535):
        raise ValueError('Unsupported native profile geometry')
    catalog = Catalog.load(root)
    eligible = catalog.filtered(settings.include, settings.exclude)
    eligible_ids = {entry.id for entry in eligible}
    ordinals = [i for i, entry in enumerate(catalog.entries) if entry.id in eligible_ids]
    if not ordinals:
        raise ValueError('No animations match the category filters')
    supported = cached_capabilities() is True
    files = _config_files(None) if supported else []
    # Historical matching contracts may reference additional configuration paths.
    # Snapshot these before extracting keys/options, not after their first use.
    if supported:
        try:
            document, _ = _read_state(_state_file())
            if document is not None and document['version'] == 2:
                files = list(dict.fromkeys([*files, *(record[0]
                    for profile in document['profiles'] for record in profile['key']['files'])]))
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            pass  # Layout readers retain the established missing-layout behavior.
    paths = list(dict.fromkeys(Path(value).absolute() for value in files))
    if len(set(snapshots).union(paths)) > 128:
        raise ValueError('Native profile dependency count exceeds bounds')
    for path in paths:
        snapshots[path] = _snapshot(path)
    position, padding, candidates = layout_contracts() if supported else (0, 0, [])
    if (any(_snapshot(path) != snapshot for path, snapshot in snapshots.items())
            or environment != sorted((key, os.environ[key]) for key in ENVIRONMENT_KEYS if key in os.environ)):
        raise ValueError('Native profile inputs changed during preparation')
    snapshot_bytes = list(snapshots.values())
    # Fixed v2 header retains v1 field offsets; geometry slots carry layout
    # candidate count, raw logo position and reserved zero doubles instead.
    body = struct.pack('<8sIIIIIIddIi', MAGIC, 2, len(environment), len(snapshots),
                       len(ordinals), len(candidates), position, 0., 0.,
                       int(supported), padding)
    for name, value in environment:
        # Empty environment values are meaningful and use an explicit length.
        raw = os.fsencode(value)
        body += _string(name) + struct.pack('<I', len(raw)) + raw
    body += b''.join(snapshot_bytes)
    body += b''.join(struct.pack('<III', *candidate) for candidate in candidates)
    body += struct.pack('<' + 'I' * len(ordinals), *ordinals)
    body += hashlib.sha256(body).digest()
    if len(body) > MAX_BYTES:
        raise ValueError('Native profile exceeds bounds')
    destination = root / NAME
    if any(p.is_symlink() for p in (destination, *destination.parents)):
        raise ValueError('Native profile must not use symlinks')
    previous = destination.read_bytes() if destination.exists() else None
    if previous is not None and (not 88 <= len(previous) <= MAX_BYTES
            or previous[:8] != MAGIC or struct.unpack_from('<I', previous, 8)[0] != 2
            or hashlib.sha256(previous[:-32]).digest() != previous[-32:]):
        raise ValueError('Refusing to replace an unrecognized native profile')
    fd, name = tempfile.mkstemp(prefix='.native-profile-', dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
        if (destination.read_bytes() if destination.exists() else None) != previous:
            raise ValueError('Native profile changed during preparation')
        if any(p.is_symlink() for p in (destination, *destination.parents)):
            raise ValueError('Native profile must not use symlinks')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {'profile': str(destination), 'eligible': len(ordinals), 'supported': supported}
