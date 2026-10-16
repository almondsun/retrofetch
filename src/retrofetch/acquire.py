"""Bounded HTTPS acquisition and transactional extraction of source assets."""

import hashlib
from pathlib import Path, PurePosixPath
import shutil
import stat
import zipfile

MAX_FILE_BYTES = 256 * 1024**2
MAX_TOTAL_BYTES = 8 * 1024**3
MAX_MEMBERS = 300_000
MARKER = ".retrofetch-source.json"


class AcquisitionError(RuntimeError):
    """Source could not be safely downloaded, verified, or extracted."""


def _member_path(name):
    if (not name or "\\" in name or ":" in name or name.startswith("/")
            or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))):
        raise AcquisitionError("archive contains an unsafe member path")
    return PurePosixPath(name.rstrip("/"))


def _validated_entries(archive):
    entries = archive.infolist()
    if len(entries) > MAX_MEMBERS:
        raise AcquisitionError("archive has too many members")
    seen, files, total = set(), set(), 0
    validated = []
    for entry in entries:
        path = _member_path(entry.filename)
        key = path.as_posix().casefold()
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if (entry.orig_filename != entry.filename or key in seen
                or key == MARKER or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                or entry.flag_bits & 1):
            raise AcquisitionError("archive contains duplicate, special, or encrypted entries")
        seen.add(key)
        if not entry.is_dir():
            files.add(key)
        if entry.file_size > MAX_FILE_BYTES or entry.file_size < 0:
            raise AcquisitionError("archive member exceeds the size limit")
        total += entry.file_size
        if total > MAX_TOTAL_BYTES:
            raise AcquisitionError("archive exceeds the extraction size limit")
        validated.append((entry, path))
    for _, path in validated:
        if any(parent.as_posix().casefold() in files for parent in path.parents if str(parent) != "."):
            raise AcquisitionError("archive has conflicting file and directory paths")
    return validated


def extract_zip(archive_path, destination):
    """Prevalidate every entry before creating destination; return file hashes.

    Destination must not exist. Only image/HTML/text sources are materialized;
    executables and other support programs are never extracted or executed.
    """
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise AcquisitionError("extraction destination already exists")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            validated = _validated_entries(archive)
            destination.mkdir(parents=True)
            hashes = {}
            try:
                for entry, relative in validated:
                    if entry.is_dir() or relative.suffix.lower() not in (".gif", ".htm", ".html", ".txt"):
                        continue
                    target = destination.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    digest, written = hashlib.sha256(), 0
                    with archive.open(entry) as source, target.open("xb") as output:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            written += len(block)
                            if written > entry.file_size:
                                raise AcquisitionError("archive member exceeds its declared size")
                            digest.update(block)
                            output.write(block)
                    if written != entry.file_size:
                        raise AcquisitionError("archive member is truncated")
                    hashes[relative.as_posix()] = digest.hexdigest()
                if not hashes:
                    raise AcquisitionError("archive contains no supported source files")
                return hashes
            except Exception:
                shutil.rmtree(destination)
                raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        if isinstance(error, AcquisitionError):
            raise
        raise AcquisitionError(f"source extraction failed: {error}") from error


