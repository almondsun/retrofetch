"""User-local installation without pip, privilege changes, or shell edits."""

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .acquire import acquire_source
from .rendering import supports_animation

MANAGED = "# Managed by Retrofetch installer v1"
PACKAGE_MARKER = ".retrofetch-install.json"


class InstallationError(RuntimeError):
    """Installation cannot safely proceed."""


def prepare_library(source, data_dir, *, progress=None):
    """Import lazily so renderer-only installations remain importable."""
    from .importer import prepare_library as prepare
    from .normalization import normalize_library
    from .selection_index import build_index
    report = prepare(source, data_dir, progress=progress)
    report['normalization'] = normalize_library(data_dir, progress=progress)
    build_index(data_dir)
    from .runtime_cache import refresh_capabilities
    refresh_capabilities()
    return report


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
            cache_dir: Path | None = None, progress=None) -> dict:
    """Prepare the library first, then atomically publish package and launcher."""
    check_dependencies()
    prefix, data_dir = Path(prefix).expanduser().absolute(), Path(data_dir).expanduser().absolute()
    _validate_targets(prefix)
    prefix.mkdir(parents=True, exist_ok=True)
    lock_path = prefix / ".retrofetch-install.lock"
    if lock_path.is_symlink():
        raise InstallationError("installation lock must not be a symlink")
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallationError("another installation is using this prefix") from error
        return _install(prefix, data_dir, source=source, cache_dir=cache_dir, progress=progress)


def _install(prefix, data_dir, *, source=None, cache_dir=None, progress=None):
    launcher, package = _validate_targets(prefix)
    cache_dir = Path(cache_dir) if cache_dir is not None else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "retrofetch"
    if source is None:
        source = acquire_source(cache_dir, progress=progress)
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_dir():
        raise InstallationError("source must be an extracted collection directory")
    def preparing(done, total, family):
        if progress and (done == 1 or done % 100 == 0 or done == total):
            progress(f"Preparing {done}/{total}: {family}")
    library_report = prepare_library(source, data_dir, progress=preparing if progress else None)
    # Data preparation owns its own transaction. No launcher is published when
    # that transaction fails; existing working installations are preserved.
    launcher.parent.mkdir(parents=True, exist_ok=True)
    package.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".retrofetch-stage-", dir=package.parent) as directory:
        stage = Path(directory)
        new_package = stage / "package"
        source_package = Path(__file__).resolve().parent
        shutil.copytree(source_package, new_package / "retrofetch", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (new_package / PACKAGE_MARKER).write_text(json.dumps({"managed_by": "retrofetch", "version": 1}) + "\n")
        # A Python entrypoint avoids shell quoting hazards in installation paths.
        text = ("#!/usr/bin/env python3\n" + MANAGED + "\n"
                "import sys\n"
                f"sys.path.insert(0, {str(package)!r})\n"
                "from retrofetch.cli import main\n"
                "raise SystemExit(main())\n")
        fd, temporary_name = tempfile.mkstemp(prefix=".retrofetch-", dir=launcher.parent)
        temporary_launcher = Path(temporary_name)
        try:
            with os.fdopen(fd, "w") as output:
                output.write(text)
            temporary_launcher.chmod(0o755)
            _validate_targets(prefix)
            previous = stage / "previous"
            published_package = False
            try:
                if package.exists():
                    package.rename(previous)
                new_package.rename(package)
                published_package = True
                os.replace(temporary_launcher, launcher)
            except BaseException:
                # Restore the previous package before staging cleanup, including
                # when Ctrl-C interrupts publication. Re-raise the original exit.
                if published_package:
                    shutil.rmtree(package)
                if previous.exists():
                    previous.rename(package)
                raise
        finally:
            temporary_launcher.unlink(missing_ok=True)
    if progress:
        progress(f"Installed {launcher}; shell startup remains opt-in")
    return {"prefix": str(prefix), "data_dir": str(data_dir), "launcher": str(launcher), "library": library_report}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare Retrofetch assets and install a user-local launcher.")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "retrofetch")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--source", type=Path, help="reuse a local extracted collection instead of downloading")
    args = parser.parse_args(argv)
    try:
        report = install(args.prefix, args.data_dir, source=args.source, cache_dir=args.cache_dir,
                         progress=lambda message: print(message, file=sys.stderr))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"retrofetch install: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
