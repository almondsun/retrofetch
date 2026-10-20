"""Explicit opt-in installation of a locally built native frontend."""
import argparse
import fcntl
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
import tempfile


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def native_owned(prefix, marker):
    """Recognize an exact managed native/Python pair, never arbitrary binaries."""
    if (not isinstance(marker, dict) or set(marker) !=
            {'managed_by', 'version', 'native_sha256', 'python_sha256'}
            or marker.get('managed_by') != 'retrofetch' or marker.get('version') != 2):
        return False
    from .installer import MANAGED
    launcher, companion = prefix / 'bin/retrofetch', prefix / 'bin/retrofetch-python'
    try:
        if any(p.is_symlink() for p in (launcher, companion)):
            return False
        binary, python = launcher.read_bytes(), companion.read_bytes()
        return (binary.startswith(b'\x7fELF') and _digest(binary) == marker['native_sha256']
                and _digest(python) == marker['python_sha256']
                and MANAGED in python.decode('utf-8').splitlines()[:3])
    except (OSError, UnicodeError):
        return False


def install_native(prefix, frontend):
    """Publish a managed pair with rollback; caller explicitly opts in."""
    from .installer import (_safe_path, _validate_targets, InstallationError,
                            MANAGED, PACKAGE_MARKER)
    prefix = Path(prefix).expanduser().absolute()
    frontend = Path(frontend).expanduser().absolute()
    _safe_path(prefix)
    _safe_path(frontend)
    binary = frontend.read_bytes()
    if not binary.startswith(b'\x7fELF') or not 16 <= len(binary) <= 16 * 1024 * 1024:
        raise InstallationError('native frontend must be a bounded locally built ELF executable')
    # Share the installer directory lock without creating persistent files.
    fd = os.open(prefix, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        launcher, package = _validate_targets(prefix)
        marker = package / PACKAGE_MARKER
        companion = prefix / 'bin/retrofetch-python'
        for path in (launcher, marker, companion):
            _safe_path(path)
        previous = {path: path.read_bytes() if path.exists() else None
                    for path in (launcher, companion, marker)}
        ownership = json.loads(previous[marker])
        if ownership['version'] == 2:
            python = previous[companion]
        else:
            if previous[companion] is not None:
                try:
                    managed = MANAGED in previous[companion].decode('utf-8').splitlines()[:3]
                except UnicodeError:
                    managed = False
                if not managed:
                    raise InstallationError('refusing to overwrite an unmanaged Python companion')
            python = previous[launcher]
        if python is None or MANAGED not in python.decode('utf-8').splitlines()[:3]:
            raise InstallationError('a managed Python installation is required')
        new_marker = json.dumps(dict(managed_by='retrofetch', version=2,
            native_sha256=_digest(binary), python_sha256=_digest(python))).encode() + b'\n'
        modes = {path: stat.S_IMODE(path.stat().st_mode) if data is not None else None
                 for path, data in previous.items()}
        directories, staged, retained = [], [], set()
        try:
            for target, data, mode in ((companion, python, 0o755),
                    (marker, new_marker, 0o644), (launcher, binary, 0o755)):
                # Renames must remain on the target's filesystem, including recovery.
                directory = Path(tempfile.mkdtemp(prefix='.native-install-', dir=target.parent))
                directories.append(directory)
                backup = directory / 'original'
                if previous[target] is not None:
                    backup.write_bytes(previous[target])
                    backup.chmod(modes[target])
                path = directory / 'replacement'
                path.write_bytes(data)
                path.chmod(mode)
                staged.append((path, target, backup))
            for path, data in previous.items():
                _safe_path(path)
                if ((path.read_bytes() if path.exists() else None) != data
                        or (stat.S_IMODE(path.stat().st_mode) if path.exists() else None) != modes[path]):
                    raise InstallationError('installation changed during native staging')
            attempted = []
            try:
                for source, target, backup in staged:
                    # Signals may arrive after rename but before Python sees its return.
                    attempted.append((target, backup))
                    os.replace(source, target)
            except BaseException as cause:
                failures = []
                for target, backup in reversed(attempted):
                    try:
                        current = target.read_bytes() if target.exists() else None
                        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
                        if current == previous[target] and mode == modes[target]:
                            continue
                        if previous[target] is None:
                            target.unlink(missing_ok=True)
                        else:
                            os.replace(backup, target)
                    except BaseException as error:
                        # Do not let one restore failure prevent other restores or
                        # delete the only remaining copy of the original bytes.
                        retained.add(backup.parent)
                        recovery = backup if previous[target] is not None else backup.parent
                        failures.append(f'{target}: {error}; recovery: {recovery}'
                                        + (' (target was originally absent)' if previous[target] is None else ''))
                if failures:
                    raise InstallationError(f'native publication failed: {cause}; rollback incomplete: '
                                            + '; '.join(failures)) from cause
                raise
        finally:
            for directory in directories:
                if directory not in retained:
                    shutil.rmtree(directory)
        return {'launcher': str(launcher), 'python_launcher': str(companion)}
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--frontend', type=Path, required=True,
                        help='trusted ELF produced by scripts/build_native.py; never a collection program')
    args = parser.parse_args(argv)
    try:
        report = install_native(args.prefix, args.frontend)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'retrofetch native install: {error}', file=__import__('sys').stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
