"""User-local installation without pip, privilege changes, or shell edits."""

import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from contextlib import ExitStack

from .acquire import acquire_source
from .rendering import supports_animation

MANAGED = "# Managed by Retrofetch installer v1"
PACKAGE_MARKER = ".retrofetch-install.json"


class InstallationError(RuntimeError):
    """Installation cannot safely proceed."""


DOWNLOAD_NOTICE = """Retrofetch downloads all three Animation Factory collections from Internet Archive.
Expect approximately 13.71 GB of downloads, at least 65 GiB free staging space,
and hours of preparation. Imported source notices must be reviewed and retained.
Third-party animations are NOT covered by Retrofetch's MIT code license.
Internet Archive hosting does not grant additional usage or redistribution rights."""


def acquire_collections(cache_dir, *, progress=None):
    from .acquire import acquire_collections as acquire
    return acquire(cache_dir, progress=progress)


def prepare_library(source, data_dir, *, progress=None, legacy_catalog=None):
    """Prepare and verify without publishing global capability metadata."""
    from .importer import prepare_library as prepare, validate_library
    from .normalization import normalize_library
    from .selection_index import build_index
    report = prepare(source, data_dir, progress=progress, legacy_catalog=legacy_catalog)
    report['normalization'] = normalize_library(data_dir, progress=progress)
    build_index(data_dir)
    report['validation'] = validate_library(data_dir)
    return report


def _safe_path(path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise InstallationError("installation paths must not contain symlinks")


def _existing_parent(path):
    while not path.exists():
        path = path.parent
    return path


def _check_space(cache_dir, data_dir):
    # Cache extraction and candidate + rollback share a budget when co-located.
    budgets = {}
    for path, amount in ((cache_dir, 45 * 1024**3), (data_dir, 20 * 1024**3)):
        parent = _existing_parent(path)
        device = parent.stat().st_dev
        prior = budgets.get(device, (parent, 0))
        budgets[device] = (parent, prior[1] + amount)
    for parent, needed in budgets.values():
        if shutil.disk_usage(parent).free < needed:
            raise InstallationError(f"insufficient free space on {parent}: need {needed // 1024**3} GiB")


def _exchange(left, right):
    """Linux atomic exchange keeps existing library readers on a valid root."""
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.renameat2(-100, os.fsencode(left), -100, os.fsencode(right), 2)
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _runtime_path():
    from .runtime_cache import _path
    return _path(None)


def _stage_capabilities(path):
    from .runtime_cache import refresh_capabilities
    report = refresh_capabilities(state_file=path)
    if not report['supported']:
        raise InstallationError("Fastfetch needs Kitty animation and ImageMagick support")



def check_dependencies():
    if not sys.platform.startswith("linux") or sys.version_info < (3, 11):
        raise InstallationError("Retrofetch requires Linux and Python 3.11 or newer")
    missing = [tool for tool in ("python3", "fastfetch", "kitten", "magick") if shutil.which(tool) is None]
    if missing:
        raise InstallationError("missing required tools: " + ", ".join(missing))
    if not supports_animation():
        raise InstallationError("Fastfetch needs Kitty animation and ImageMagick support")


def _validate_targets(prefix):
    launcher, package = prefix / "bin/retrofetch", prefix / "lib/retrofetch"
    for target in (prefix, prefix / "bin", prefix / "lib", launcher, package):
        if target.is_symlink():
            raise InstallationError("installation targets must not be symlinks")
    if launcher.exists() and (not launcher.is_file() or MANAGED not in launcher.read_text(errors="replace").splitlines()[:3]):
        raise InstallationError("refusing to overwrite an unmanaged retrofetch launcher")
    marker = package / PACKAGE_MARKER
    if package.exists() and (not package.is_dir() or marker.is_symlink() or not marker.is_file()):
        raise InstallationError("refusing to replace an unmanaged package directory")
    if marker.is_file():
        try:
            if json.loads(marker.read_text()) != {"managed_by": "retrofetch", "version": 1}:
                raise ValueError("unknown ownership marker")
        except (OSError, ValueError, TypeError) as error:
            raise InstallationError("unrecognized package ownership marker") from error
    return launcher, package


def install(prefix: Path, data_dir: Path, *, source: Path | None = None,
            cache_dir: Path | None = None, progress=None,
            accept_source_notices=False) -> dict:
    """Stage every output, then publish with reverse-order rollback."""
    check_dependencies()
    prefix, data_dir = Path(prefix).expanduser().absolute(), Path(data_dir).expanduser().absolute()
    cache_dir = (Path(cache_dir).expanduser().absolute() if cache_dir is not None else
                 Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "retrofetch")
    state = _runtime_path()
    for path in (prefix, data_dir, cache_dir, state):
        _safe_path(path)
    _validate_targets(prefix)
    if source is None and not accept_source_notices:
        raise InstallationError("source notices must be accepted with --accept-source-notices before downloading")
    if data_dir == prefix or data_dir.is_relative_to(prefix / "lib") or prefix.is_relative_to(data_dir):
        raise InstallationError("library and installation package paths must be disjoint")
    if data_dir.exists() and not data_dir.is_dir():
        raise InstallationError("library destination must be a directory")
    if data_dir.exists() and any(data_dir.iterdir()) and not (data_dir / "catalog.json").is_file():
        raise InstallationError("refusing to replace an unmanaged library directory")
    if source is None:
        _check_space(cache_dir, data_dir)
    # Prefix and library-parent locks serialize even different prefixes targeting
    # the same library. Symlink-safe directory descriptors avoid lockfile races.
    with ExitStack() as stack:
        directories = {prefix, data_dir.parent, state.parent}
        if data_dir.exists():
            directories.add(data_dir)
        for directory in sorted(directories, key=str):
            directory.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise InstallationError("another installation is using this destination") from error
        # Retain the original prefix lock for compatibility with older installers.
        lock_path = prefix / ".retrofetch-install.lock"
        _safe_path(lock_path)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        stack.callback(os.close, descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallationError("another installation is using this prefix") from error
        return _install(prefix, data_dir, source=source, cache_dir=cache_dir,
                        progress=progress, state=state)


def _install(prefix, data_dir, *, source, cache_dir, progress, state):
    launcher, package = _validate_targets(prefix)
    if source is None:
        source = acquire_collections(cache_dir, progress=progress)
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_dir():
        raise InstallationError("source must be an extracted collection directory")
    if source.is_relative_to(data_dir) or data_dir.is_relative_to(source):
        raise InstallationError("source and library must be disjoint")
    launcher.parent.mkdir(parents=True, exist_ok=True)
    package.parent.mkdir(parents=True, exist_ok=True)
    def preparing(done, total, family):
        if progress and (done == 1 or done % 100 == 0 or done == total):
            progress(f"Preparing {done}/{total}: {family}")
    old_catalog_bytes = (data_dir / "catalog.json").read_bytes() if (data_dir / "catalog.json").is_file() else None
    old_state_bytes = state.read_bytes() if state.exists() else None
    if old_catalog_bytes is not None:
        _safe_path(data_dir / "catalog.json")
        from .catalog import Catalog
        Catalog.load(data_dir)
    with ExitStack() as stack:
        stages = []
        for parent in (data_dir.parent, package.parent, launcher.parent, state.parent):
            directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix=".retrofetch-stage-", dir=parent)))
            stages.append(directory)
        new_data, new_package, new_launcher, new_state = (stages[0] / "library", stages[1] / "package",
                                                          stages[2] / "launcher", stages[3] / "runtime.json")
        new_data.mkdir()
        old_catalog = data_dir / "catalog.json"
        library_report = prepare_library(source, new_data, progress=preparing if progress else None,
                                         legacy_catalog=old_catalog if old_catalog.is_file() else None)
        # Keep prior normalized files for processes holding a previous catalog.
        if old_catalog.is_file():
            from .catalog import Catalog
            for entry in Catalog.load(data_dir).entries:
                relative = entry.path.relative_to(data_dir)
                _safe_path(entry.path)
                old = data_dir / relative
                target = new_data / relative
                if hashlib.sha256(old.read_bytes()).hexdigest() != entry.sha256:
                    raise InstallationError("previous library asset changed during upgrade")
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(old, target)
        source_package = Path(__file__).resolve().parent
        shutil.copytree(source_package, new_package / "retrofetch", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (new_package / PACKAGE_MARKER).write_text(json.dumps({"managed_by": "retrofetch", "version": 1}) + "\n")
        new_launcher.write_text("#!/usr/bin/env python3\n" + MANAGED + "\nimport sys\n"
                                f"sys.path.insert(0, {str(package)!r})\n"
                                "from retrofetch.cli import main\nraise SystemExit(main())\n")
        new_launcher.chmod(0o755)
        # Only probe after complete asset verification, into private staging.
        if state.exists():
            from .runtime_cache import _read
            _read(state)
        _stage_capabilities(new_state)
        _validate_targets(prefix)
        if ((data_dir / "catalog.json").read_bytes() if old_catalog.is_file() else None) != old_catalog_bytes:
            raise InstallationError("library catalog changed during preparation")
        if (state.read_bytes() if state.exists() else None) != old_state_bytes:
            raise InstallationError("runtime capability state changed during preparation")
        operations = []
        retained = []
        try:
            for candidate, target in ((new_data, data_dir), (new_package, package),
                                      (new_launcher, launcher), (new_state, state)):
                _safe_path(target)
                backup_root = Path(tempfile.mkdtemp(prefix=".retrofetch-rollback-", dir=target.parent))
                backup = backup_root / target.name
                existed = target.exists()
                operations.append((target, backup, existed))
                if existed and target.is_dir():
                    _exchange(candidate, target)
                    try:
                        candidate.rename(backup)
                    except BaseException:
                        _exchange(candidate, target)
                        raise
                else:
                    if existed:
                        # Retain the old inode without removing the active file.
                        os.link(target, backup, follow_symlinks=False)
                    if candidate == new_launcher:
                        os.replace(candidate, target)
                    else:
                        candidate.rename(target)
                retained.append(str(backup_root))
        except BaseException:
            for target, backup, existed in reversed(operations):
                if backup.is_file():
                    os.replace(backup, target)
                elif backup.exists() or not existed:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink(missing_ok=True)
                    if backup.exists():
                        backup.rename(target)
            raise
    if progress:
        progress(f"Installed {launcher}; shell startup remains opt-in")
    return {"prefix": str(prefix), "data_dir": str(data_dir), "launcher": str(launcher),
            "library": library_report, "rollback_directories": retained}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare Retrofetch assets and install a user-local launcher.")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "retrofetch")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--source", type=Path, help="reuse a local extracted collection instead of downloading")
    parser.add_argument("--accept-source-notices", action="store_true",
                        help="accept source notices, third-party rights restrictions, and the 13.71 GB download")
    args = parser.parse_args(argv)
    if args.source is None:
        print(DOWNLOAD_NOTICE, file=sys.stderr)
        if not args.accept_source_notices and sys.stdin.isatty():
            args.accept_source_notices = input("Accept these source notices and download all three collections? [yes/no] ").strip().lower() == "yes"
    try:
        report = install(args.prefix, args.data_dir, source=args.source, cache_dir=args.cache_dir,
                         progress=lambda message: print(message, file=sys.stderr),
                         accept_source_notices=args.accept_source_notices)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"retrofetch install: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
