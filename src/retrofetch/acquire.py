"""Bounded HTTPS acquisition and transactional extraction of source assets."""

import fcntl
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener
import zipfile

ITEM = "animationfactoryessentialcollection"
ARCHIVE_NAME = "animation factory essential collection.zip"
METADATA_URL = f"https://archive.org/metadata/{ITEM}"
DOWNLOAD_URL = f"https://archive.org/download/{ITEM}/{quote(ARCHIVE_NAME)}"
MAX_ARCHIVE_BYTES = 4 * 1024**3
MAX_FILE_BYTES = 256 * 1024**2
MAX_TOTAL_BYTES = 8 * 1024**3
MAX_MEMBERS = 300_000
MARKER = ".retrofetch-source.json"


class AcquisitionError(RuntimeError):
    """Source could not be safely downloaded, verified, or extracted."""


def _allowed_url(url):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and not parsed.username and not parsed.password
            and (parsed.hostname == "archive.org" or (parsed.hostname or "").endswith(".archive.org")))


class _ArchiveRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if not _allowed_url(new_url):
            raise AcquisitionError("source redirect leaves HTTPS Internet Archive")
        return super().redirect_request(request, response, code, message, headers, new_url)


def urlopen(url, *, timeout):
    """Enforce redirect boundaries before following the redirected request."""
    return build_opener(_ArchiveRedirect()).open(url, timeout=timeout)


def _hash(path, algorithm="sha256"):
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular(path):
    return not path.is_symlink() and path.is_file()


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


def _open(url):
    response = urlopen(url, timeout=60)
    if not _allowed_url(response.geturl()):
        response.close()
        raise AcquisitionError("source redirected outside HTTPS Internet Archive")
    return response


def _metadata():
    with _open(METADATA_URL) as response:
        data = response.read(2 * 1024**2 + 1)
    if len(data) > 2 * 1024**2:
        raise AcquisitionError("source metadata exceeds the size limit")
    for entry in json.loads(data).get("files", []):
        if entry.get("name") == ARCHIVE_NAME:
            size = int(entry["size"])
            sha1 = entry.get("sha1")
            if size <= 0 or size > MAX_ARCHIVE_BYTES:
                raise AcquisitionError("source archive size is invalid or exceeds the limit")
            if sha1 is not None and not re.fullmatch(r"[0-9a-fA-F]{40}", sha1):
                raise AcquisitionError("source metadata has an invalid checksum")
            return size, sha1.lower() if sha1 else None
    raise AcquisitionError("the expected source ZIP is absent from Internet Archive metadata")


def _verified_source(source):
    marker = source / MARKER
    if not source.is_dir() or source.is_symlink() or not _regular(marker):
        return False
    try:
        record = json.loads(marker.read_text())
        if record.get("version") != 1 or not record.get("files"):
            return False
        for name, digest in record["files"].items():
            relative = _member_path(name)
            path = source.joinpath(*relative.parts)
            if (any(parent.is_symlink() for parent in path.parents if parent != source.parent)
                    or not _regular(path) or _hash(path) != digest):
                return False
        return True
    except (OSError, ValueError, TypeError, AttributeError, AcquisitionError):
        return False


def acquire_source(cache_dir: Path, *, progress=None) -> Path:
    """Reuse verified extracted sources, or download and safely publish a ZIP.

    Interrupted transfers restart rather than trusting an unverified partial.
    SHA-1 from official HTTPS metadata checks corruption, not licensing or
    cryptographic provenance. Completed sources are rehashed before reuse.
    """
    cache_dir = Path(cache_dir).expanduser().absolute()
    if cache_dir.is_symlink():
        raise AcquisitionError("source cache must not be a symlink")
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / ".acquire.lock"
    if lock_path.is_symlink():
        raise AcquisitionError("source cache lock must not be a symlink")
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AcquisitionError("another source acquisition is running") from error
        source = cache_dir / "source"
        if _verified_source(source):
            if progress:
                progress("Reusing verified source collection")
            return source
        if source.is_symlink() or (source.exists() and not _regular(source / MARKER)):
            raise AcquisitionError("refusing to replace an unrecognized source directory")
        archive_path = cache_dir / "collection.zip"
        partial = cache_dir / "collection.zip.part"
        if archive_path.is_symlink() or partial.is_symlink():
            raise AcquisitionError("source archive paths must not be symlinks")
        staging = None
        try:
            size, sha1 = _metadata()
            valid_archive = (_regular(archive_path) and archive_path.stat().st_size == size
                             and sha1 is not None and _hash(archive_path, "sha1") == sha1)
            if not valid_archive:
                if progress:
                    progress(f"Downloading source collection ({size} bytes)")
                digest, received = hashlib.sha1(), 0
                with _open(DOWNLOAD_URL) as response, partial.open("wb") as output:
                    for block in iter(lambda: response.read(1024 * 1024), b""):
                        received += len(block)
                        if received > size or received > MAX_ARCHIVE_BYTES:
                            raise AcquisitionError("download exceeds its declared size")
                        output.write(block)
                        digest.update(block)
                if received != size or (sha1 and digest.hexdigest() != sha1):
                    raise AcquisitionError("source download size or checksum verification failed")
                if not sha1:
                    with zipfile.ZipFile(partial) as archive:
                        _validated_entries(archive)
                        if archive.testzip() is not None:
                            raise AcquisitionError("source ZIP checksum verification failed")
                os.replace(partial, archive_path)
            staging = Path(tempfile.mkdtemp(prefix=".source-stage-", dir=cache_dir))
            extracted = staging / "source"
            if progress:
                progress("Validating and extracting source collection")
            hashes = extract_zip(archive_path, extracted)
            parents = {path.parent for path in extracted.rglob("d1") if path.is_dir()}
            parents.add(extracted)
            if not any(all((parent / f"d{i}").is_dir() for i in range(1, 5)) for parent in parents):
                raise AcquisitionError("source ZIP does not contain all four collection discs")
            (extracted / MARKER).write_text(json.dumps({
                "version": 1, "url": DOWNLOAD_URL, "sha1": sha1,
                "archive_sha256": _hash(archive_path), "files": hashes,
            }, sort_keys=True) + "\n")
            previous = staging / "previous"
            if source.exists():
                source.rename(previous)
            try:
                extracted.rename(source)
            except OSError:
                if previous.exists():
                    previous.rename(source)
                raise
            return source
        except (OSError, ValueError, KeyError, TypeError, AttributeError, HTTPException, zipfile.BadZipFile) as error:
            raise AcquisitionError(f"source acquisition failed: {error}") from error
        finally:
            partial.unlink(missing_ok=True)
            if staging is not None:
                shutil.rmtree(staging)
