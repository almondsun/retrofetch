"""Content-bound read-only accelerators for catalog selection."""

import hashlib
import json
import os
from pathlib import Path
import random
import stat
import struct

from .catalog import _entries_from_document, _entry_from_row, _validate_selected, _validate_row, _select

INDEX_NAME = 'selection.v2.idx'
LEGACY_INDEX_NAME = 'selection.sqlite3'
APPLICATION_ID = 0x52544658
VERSION = 2
MAGIC = b'RTFXIDX2'
HEADER = struct.Struct('<8sII32sQ')
MAX_INDEX_BYTES = 128 * 1024 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 1_000_000


def _read_binary(path):
    """Validate a complete snapshot, without following the index symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode)
                or not HEADER.size + 16 + 32 <= info.st_size <= MAX_INDEX_BYTES):
            raise ValueError('Selection index is not a bounded regular file')
        snapshot = stream.read(info.st_size + 1)
    if len(snapshot) != info.st_size:
        raise ValueError('Selection index changed size while being read')
    if hashlib.sha256(memoryview(snapshot)[:-32]).digest() != snapshot[-32:]:
        raise ValueError('Selection index checksum mismatch')
    magic, version, count, digest, payload_length = HEADER.unpack_from(snapshot)
    if magic != MAGIC or version != VERSION or not 0 < count <= MAX_ENTRIES:
        raise ValueError('Unrecognized selection index header')
    start = HEADER.size + 8 * (count + 1)
    if start + payload_length + 32 != len(snapshot):
        raise ValueError('Selection index length mismatch')
    offsets = struct.unpack_from('<' + 'Q' * (count + 1), snapshot, HEADER.size)
    if (offsets[0] != 0 or offsets[-1] != payload_length
            or any(not 0 < end - begin <= MAX_RECORD_BYTES
                   for begin, end in zip(offsets, offsets[1:]))):
        raise ValueError('Invalid selection index offsets')
    return snapshot, start, offsets, digest


def _check_destination(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return
    try:
        _read_binary(path)
    except (OSError, ValueError, struct.error) as error:
        raise ValueError('Refusing to replace an unrecognized selection index') from error


def build_index(data_dir: Path) -> dict:
    """Atomically publish a deterministic v2 index; leave legacy SQLite intact."""
    from dataclasses import asdict
    import tempfile

    root = Path(data_dir).resolve(strict=True)
    destination = root / INDEX_NAME
    _check_destination(destination)
    snapshot = (root / 'catalog.json').read_bytes()
    digest = hashlib.sha256(snapshot).digest()
    entries = _entries_from_document(json.loads(snapshot), root)
    if not 0 < len(entries) <= MAX_ENTRIES:
        raise ValueError('Selection index requires 1 to 1000000 entries')
    records, offsets = [], [0]
    for entry in entries:
        row = asdict(entry)
        row['path'] = 'animations/' + entry.sha256 + '.gif'
        record = json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if not 0 < len(record) <= MAX_RECORD_BYTES:
            raise ValueError('Selection index record exceeds its size limit')
        records.append(record)
        offsets.append(offsets[-1] + len(record))
        if HEADER.size + 8 * (len(entries) + 1) + offsets[-1] + 32 > MAX_INDEX_BYTES:
            raise ValueError('Selection index exceeds its size limit')
    body = (HEADER.pack(MAGIC, VERSION, len(entries), digest, offsets[-1])
            + struct.pack('<' + 'Q' * len(offsets), *offsets) + b''.join(records))
    fd, name = tempfile.mkstemp(prefix='.selection-index-', suffix='.idx', dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.write(hashlib.sha256(body).digest())
        if hashlib.sha256((root / 'catalog.json').read_bytes()).digest() != digest:
            raise ValueError('Catalog changed while its selection index was being built')
        _check_destination(destination)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {'entries': len(entries), 'catalog_sha256': digest.hex(), 'index': str(destination)}


def _legacy_selection(root, rng):
    """Compatibility only: importing SQLite must not penalize the v2 path."""
    path = root / LEGACY_INDEX_NAME
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INDEX_BYTES:
        return None
    from contextlib import closing
    import sqlite3
    import time

    def connect():
        connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=0.1)
        try:
            connection.execute('PRAGMA trusted_schema = OFF')
            connection.execute('PRAGMA query_only = ON')
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_RECORD_BYTES)
            deadline = time.monotonic() + 0.1
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            return connection
        except BaseException:
            connection.close()
            raise

    try:
        with closing(connect()) as connection:
            if connection.execute('PRAGMA application_id').fetchone() != (APPLICATION_ID,):
                return None
            digest = hashlib.sha256((root / 'catalog.json').read_bytes()).hexdigest()
            metadata = connection.execute('SELECT version, catalog_sha256, entry_count FROM metadata LIMIT 2').fetchall()
            if len(metadata) != 1:
                return None
            version, expected, count = metadata[0]
            if (type(version) is not int or version != 1 or expected != digest
                    or type(count) is not int or not 0 < count <= MAX_ENTRIES):
                return None
            bounds = connection.execute('SELECT COUNT(*), MIN(ordinal), MAX(ordinal) FROM animations').fetchone()
            if bounds != (count, 0, count - 1):
                return None
            selected = connection.execute('SELECT payload FROM animations WHERE ordinal = ?',
                                          (rng.randrange(count),)).fetchone()
            if selected is None or not isinstance(selected[0], str):
                return None
            return _validate_selected(_entry_from_row(json.loads(selected[0]), root))
    except sqlite3.Error:
        return None


def select_indexed(data_dir: Path, identifier=None, include=(), exclude=(), rng=None):
    """Select from a verified index, or return None for canonical fallback.

    Matching errors are semantic results and propagate; invalid index metadata
    and selected asset paths instead require canonical validation. Unfiltered
    random selection retains its single-record fast path and legacy support.
    """
    unfiltered = identifier is None and not include and not exclude
    generator = rng if rng is not None else random
    if not unfiltered:
        from .selection_v3 import select_v3
        selected = select_v3(data_dir, identifier, include, exclude, rng)
        if selected is not None:
            return selected
    try:
        root = Path(data_dir).resolve(strict=True)
        path = root / INDEX_NAME
        try:
            path.lstat()
        except FileNotFoundError:
            return _legacy_selection(root, generator) if unfiltered else None
        snapshot, start, offsets, expected = _read_binary(path)
        if hashlib.sha256((root / 'catalog.json').read_bytes()).digest() != expected:
            return None
        if unfiltered:
            ordinal = generator.randrange(len(offsets) - 1)
            begin, end = offsets[ordinal:ordinal + 2]
            record = json.loads(snapshot[start + begin:start + end])
            return _validate_selected(_entry_from_row(record, root))
        # Existing records are adjacent JSON objects. Join once and decode one
        # array rather than paying a Python JSON decoder call per record.
        payload = memoryview(snapshot)[start:start + offsets[-1]]
        records = json.loads(b'[' + b','.join(payload[begin:end]
                            for begin, end in zip(offsets, offsets[1:])) + b']')
        identities = set()
        for row in records:
            _validate_row(row)
            identity = row['id'].casefold()
            if identity in identities:
                return None
            identities.add(identity)
    except (OSError, ValueError, TypeError, OverflowError, struct.error):
        return None
    # Keep semantic failures outside the infrastructure/fallback exception scope.
    selected = _select(records, identifier, include, exclude, generator, dict.__getitem__)
    try:
        return _validate_selected(_entry_from_row(selected, root))
    except (OSError, ValueError, TypeError, OverflowError):
        return None


def select_random(data_dir: Path, rng=None):
    """Compatibility wrapper for unfiltered random selection."""
    return select_indexed(data_dir, rng=rng)
