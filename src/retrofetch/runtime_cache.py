"""Explicitly prepared capability metadata; startup only reads validated state."""

import json
import os
from pathlib import Path
import shutil

OWNER = 'retrofetch-runtime'
VERSION = 1


def _path(state_file):
    if state_file is not None:
        return Path(state_file).absolute()
    base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    return (base / 'retrofetch/runtime.json').absolute()


def _safe(path):
    return not any(p.is_symlink() for p in (path, *path.parents))


def _identity():
    executable = shutil.which('fastfetch')
    if executable is None:
        raise ValueError('Fastfetch executable is unavailable')
    path = Path(executable).resolve(strict=True)
    stat = path.stat()
    if not path.is_file():
        raise ValueError('Fastfetch executable is not a regular file')
    return [str(path), stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns]


def _read(path):
    if not _safe(path) or not path.is_file() or path.stat().st_size > 16 * 1024:
        raise ValueError('Unrecognized runtime capability state')
    document = json.loads(path.read_text())
    if (not isinstance(document, dict) or document.get('owner') != OWNER
            or document.get('version') != VERSION
            or type(document.get('supported')) is not bool):
        raise ValueError('Unrecognized runtime capability state')
    return document


def cached_capabilities(*, state_file=None):
    """Return bool for an exact binary identity, or None without writing/probing."""
    try:
        document = _read(_path(state_file))
        return document['supported'] if document.get('executable') == _identity() else None
    except (OSError, ValueError, TypeError):
        return None


def refresh_capabilities(*, state_file=None):
    """Probe explicitly and atomically replace only recognized, owned metadata."""
    import tempfile
    from .rendering import supports_animation

    path = _path(state_file)
    if not _safe(path):
        raise ValueError('Runtime capability state must not use symlinks')
    if path.exists():
        _read(path)
    identity = _identity()
    supported = supports_animation()
    if type(supported) is not bool or _identity() != identity:
        raise ValueError('Fastfetch executable changed during capability refresh')
    document = {'owner': OWNER, 'version': VERSION,
                'executable': identity, 'supported': supported}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.runtime-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(document, stream)
            stream.write('\n')
        if not _safe(path):
            raise ValueError('Runtime capability state changed during refresh')
        if path.exists():
            _read(path)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'supported': supported, 'state_file': str(path)}


def refresh_runtime(data_dir, *, state_file=None):
    """Stage both maintenance outputs before publication; restore on failure."""
    import fcntl
    import hashlib
    import tempfile
    from .selection_index import INDEX_NAME, _check_destination, build_index
    from .selection_v3 import INDEX_NAME as V3_NAME, _destination, build_index_v3

    root = Path(data_dir).resolve(strict=True)
    destination = root / INDEX_NAME
    destination_v3 = root / V3_NAME
    _destination(destination_v3)
    state = _path(state_file)
    if not _safe(state):
        raise ValueError('Runtime capability state must not use symlinks')
    if state.exists():
        _read(state)
    _identity()
    _check_destination(destination)
    # Lock directories, not persistent lockfiles, so rejected refreshes leave
    # the data inventory untouched. No rendering path acquires these locks.
    descriptors = []
    try:
        state.parent.mkdir(parents=True, exist_ok=True)
        for directory in sorted({root, state.parent}, key=str):
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            descriptors.append(descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('Another runtime refresh is in progress') from error
        if not _safe(state):
            raise ValueError('Runtime capability state changed during refresh')
        old_state = _read(state) if state.exists() else None
        old_state_bytes = state.read_bytes() if old_state is not None else None
        _check_destination(destination)
        old_index = destination.read_bytes() if destination.exists() else None
        old_v3 = destination_v3.read_bytes() if destination_v3.exists() else None
        catalog = (root / 'catalog.json').read_bytes()
        with tempfile.TemporaryDirectory(prefix='.runtime-index-', dir=root) as index_dir, \
                tempfile.TemporaryDirectory(prefix='.runtime-state-', dir=state.parent) as state_dir:
            stage = Path(index_dir)
            (stage / 'catalog.json').write_bytes(catalog)
            report = build_index(stage)
            report_v3 = build_index_v3(stage)
            capabilities = refresh_capabilities(state_file=Path(state_dir) / 'runtime.json')
            if not capabilities['supported']:
                raise ValueError('Fastfetch needs Kitty animation and ImageMagick support')
            if hashlib.sha256((root / 'catalog.json').read_bytes()).digest() != hashlib.sha256(catalog).digest():
                raise ValueError('Catalog changed during runtime refresh')
            _check_destination(destination)
            if (destination.read_bytes() if destination.exists() else None) != old_index:
                raise ValueError('Index changed during runtime refresh')
            _destination(destination_v3)
            if (destination_v3.read_bytes() if destination_v3.exists() else None) != old_v3:
                raise ValueError('V3 index changed during runtime refresh')
            if not _safe(state):
                raise ValueError('Runtime capability state changed during refresh')
            if (state.read_bytes() if state.exists() else None) != old_state_bytes:
                raise ValueError('Runtime capability state changed during refresh')
            attempted = []
            try:
                for source, target, previous, backup_root in (
                    (stage / INDEX_NAME, destination, old_index, stage),
                    (stage / V3_NAME, destination_v3, old_v3, stage),
                    (Path(state_dir) / 'runtime.json', state, old_state_bytes, Path(state_dir)),
                ):
                    backup = backup_root / ('previous-' + target.name)
                    if previous is not None:
                        backup.write_bytes(previous)
                    attempted.append((target, previous, backup))
                    os.replace(source, target)
            except BaseException:
                for target, previous, backup in reversed(attempted):
                    # A failed replacement may leave the original bytes in place.
                    current = target.read_bytes() if target.exists() else None
                    if current == previous:
                        continue
                    if previous is None:
                        target.unlink(missing_ok=True)
                    else:
                        os.replace(backup, target)
                raise
        report['index'] = str(destination)
        capabilities['state_file'] = str(state)
        report_v3['index'] = str(destination_v3)
        return {'index': report, 'index_v3': report_v3, 'capabilities': capabilities}
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
