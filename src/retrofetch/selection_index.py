"""Optional, content-bound read-only accelerator for unfiltered random selection."""

from contextlib import closing
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3
import tempfile
import time

from .catalog import _entries_from_document, _entry_from_row, _validate_selected

INDEX_NAME = 'selection.sqlite3'
APPLICATION_ID = 0x52544658
VERSION = 1
MAX_INDEX_BYTES = 128 * 1024 * 1024


def _connect(path):
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=0.1)
    try:
        connection.execute('PRAGMA trusted_schema = OFF')
        connection.execute('PRAGMA query_only = ON')
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 2 * 1024 * 1024)
        deadline = time.monotonic() + 0.1
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        return connection
    except BaseException:
        connection.close()
        raise


def _managed(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INDEX_BYTES:
        return False
    try:
        with closing(_connect(path)) as connection:
            return connection.execute('PRAGMA application_id').fetchone() == (APPLICATION_ID,)
    except (sqlite3.Error, OSError, ValueError):
        return False


def build_index(data_dir: Path) -> dict:
    """Build explicitly after preparation; never invoked by selection itself.

    The canonical JSON stays authoritative. Replace only an absent destination
    or a database bearing our application ID, using one atomic rename.
    """
    root = Path(data_dir).resolve(strict=True)
    destination = root / INDEX_NAME
    if destination.is_symlink() or (destination.exists() and not _managed(destination)):
        raise ValueError('Refusing to replace an unrecognized selection index')
    snapshot = (root / 'catalog.json').read_bytes()
    digest = hashlib.sha256(snapshot).hexdigest()
    entries = _entries_from_document(json.loads(snapshot), root)
    if not entries:
        raise ValueError('Cannot index an empty catalog')
    fd, name = tempfile.mkstemp(prefix='.selection-index-', suffix='.sqlite3', dir=root)
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.execute(f'PRAGMA application_id = {APPLICATION_ID}')
            connection.execute('CREATE TABLE metadata (version INTEGER, catalog_sha256 TEXT, entry_count INTEGER)')
            connection.execute('INSERT INTO metadata VALUES (?, ?, ?)', (VERSION, digest, len(entries)))
            connection.execute('CREATE TABLE animations (ordinal INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            for ordinal, entry in enumerate(entries):
                row = asdict(entry)
                row['path'] = 'animations/' + entry.sha256 + '.gif'
                connection.execute('INSERT INTO animations VALUES (?, ?)', (ordinal, json.dumps(row)))
            connection.commit()
        if hashlib.sha256((root / 'catalog.json').read_bytes()).hexdigest() != digest:
            raise ValueError('Catalog changed while its selection index was being built')
        if destination.is_symlink() or (destination.exists() and not _managed(destination)):
            raise ValueError('Selection index destination changed during preparation')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {'entries': len(entries), 'catalog_sha256': digest, 'index': str(destination)}


def select_random(data_dir: Path, rng=None):
    """Return a checked entry or None to request the canonical-catalog fallback.

    Hashing the JSON avoids parsing all entries while binding the index to exact
    current content. Do not rebuild, update, or create SQLite journals here.
    """
    try:
        root = Path(data_dir).resolve(strict=True)
        path = root / INDEX_NAME
        if not _managed(path):
            return None
        digest = hashlib.sha256((root / 'catalog.json').read_bytes()).hexdigest()
        with closing(_connect(path)) as connection:
            metadata = connection.execute('SELECT version, catalog_sha256, entry_count FROM metadata LIMIT 2').fetchall()
            if len(metadata) != 1:
                return None
            version, expected, count = metadata[0]
            if (type(version) is not int or version != VERSION or expected != digest
                    or type(count) is not int or not 0 < count <= 1_000_000):
                return None
            bounds = connection.execute('SELECT COUNT(*), MIN(ordinal), MAX(ordinal) FROM animations').fetchone()
            if bounds != (count, 0, count - 1):
                return None
            ordinal = (rng if rng is not None else random).randrange(count)
            selected = connection.execute('SELECT payload FROM animations WHERE ordinal = ?', (ordinal,)).fetchone()
            if selected is None or not isinstance(selected[0], str):
                return None
            entry = _entry_from_row(json.loads(selected[0]), root)
            return _validate_selected(entry)
    except (OSError, ValueError, TypeError, sqlite3.Error, OverflowError):
        return None
