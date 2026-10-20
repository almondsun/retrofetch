"""Portable, content-bound v3 index; publication is explicitly offline only."""
import hashlib
import json
import os
from pathlib import Path
import random
import stat
import struct
import tempfile

from .catalog import (_entries_from_document, _entry_from_row, _select,
                      _strings, _validate_selected)

INDEX_NAME = 'selection.v3.idx'
HEADER = struct.Struct('<8sII32sQQQ')
RECORD = struct.Struct('<32sIII')
KEY = struct.Struct('<BI')
MAX_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 1_000_000
MAX_STRING = 2 * 1024 * 1024


def _text(value):
    if not isinstance(value, str) or not value or len(value.encode('utf-8')) > MAX_STRING:
        raise ValueError('Invalid bounded index string')
    return value


def _keys(rows):
    for ordinal, row in enumerate(rows):
        seen = set()
        for role, values in ((0, [row[0]]), (1, row[3]), (2, [row[1]])):
            for value in values:
                key = value.casefold()
                pair = (role, key)
                if pair not in seen:
                    seen.add(pair)
                    yield key, role, ordinal


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not HEADER.size + RECORD.size + 32 <= info.st_size <= MAX_BYTES:
            raise ValueError('Invalid bounded index file')
        blob = stream.read(info.st_size + 1)
    if len(blob) != info.st_size or hashlib.sha256(memoryview(blob)[:-32]).digest() != blob[-32:]:
        raise ValueError('Index checksum mismatch')
    magic, version, count, digest, nr, nm, nk = HEADER.unpack_from(blob)
    if magic != b'RTFXIDX3' or version != 3 or not 0 < count <= MAX_ENTRIES:
        raise ValueError('Unknown index header')
    if nr != count * RECORD.size or HEADER.size + nr + nm + nk + 32 != len(blob):
        raise ValueError('Invalid section lengths')
    records = list(RECORD.iter_unpack(memoryview(blob)[HEADER.size:HEADER.size + nr]))
    if any(w < 1 or h < 1 or f < 2 for _, w, h, f in records):
        raise ValueError('Invalid animation dimensions')
    metadata = json.loads(blob[HEADER.size + nr:HEADER.size + nr + nm])
    if not isinstance(metadata, dict) or set(metadata) != {'category_names', 'rows'}:
        raise ValueError('Invalid metadata object')
    categories, rows = metadata['category_names'], metadata['rows']
    _strings(categories, 'category_names')
    if len(set(categories)) != len(categories):
        raise ValueError('Duplicate categories')
    for category in categories:
        _text(category)
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError('Invalid metadata count')
    identities = set()
    for row in rows:
        if not isinstance(row, list) or len(row) != 4:
            raise ValueError('Invalid metadata row')
        identity = _text(row[0]).casefold()
        _text(row[1])
        if identity in identities:
            raise ValueError('Duplicate identity')
        identities.add(identity)
        if not isinstance(row[2], list) or any(type(i) is not int or not 0 <= i < len(categories) for i in row[2]):
            raise ValueError('Invalid category ordinal')
        _strings(row[3], 'aliases')
        for alias in row[3]:
            _text(alias)
    position, end = HEADER.size + nr + nm, len(blob) - 32
    # Keys are checked against all metadata, not merely the selected record.
    for expected in _keys(rows):
        if position + 4 > end:
            raise ValueError('Truncated key')
        length, = struct.unpack_from('<I', blob, position)
        position += 4
        if not 0 < length <= MAX_STRING or position + length + KEY.size > end:
            raise ValueError('Invalid key length')
        key = blob[position:position + length].decode('utf-8')
        position += length
        role, ordinal = KEY.unpack_from(blob, position)
        position += KEY.size
        if (key, role, ordinal) != expected:
            raise ValueError('Key metadata mismatch')
    if position != end:
        raise ValueError('Unexpected trailing keys')
    return digest, records, categories, rows


def _destination(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return
    try:
        _read(path)
    except (OSError, ValueError, TypeError, struct.error) as error:
        raise ValueError('Refusing to replace an unrecognized selection index') from error


def build_index_v3(data_dir):
    """Build deterministic compact metadata and atomically publish validated bytes."""
    root = Path(data_dir).resolve(strict=True)
    destination = root / INDEX_NAME
    _destination(destination)
    catalog = (root / 'catalog.json').read_bytes()
    digest = hashlib.sha256(catalog).digest()
    entries = _entries_from_document(json.loads(catalog), root)
    if not 0 < len(entries) <= MAX_ENTRIES:
        raise ValueError('Selection index requires 1 to 1000000 entries')
    categories = sorted({c for entry in entries for c in entry.categories})
    category_ids = {c: i for i, c in enumerate(categories)}
    rows = [[e.id, e.name, [category_ids[c] for c in e.categories], list(e.aliases)] for e in entries]
    records = b''.join(RECORD.pack(bytes.fromhex(e.sha256), e.width, e.height, e.frames) for e in entries)
    metadata = json.dumps(dict(category_names=categories, rows=rows), ensure_ascii=False,
                          separators=(',', ':')).encode('utf-8')
    keys = bytearray()
    for key, role, ordinal in _keys(rows):
        encoded = key.encode('utf-8')
        if not 0 < len(encoded) <= MAX_STRING:
            raise ValueError('Index key exceeds size limit')
        keys.extend(struct.pack('<I', len(encoded)))
        keys.extend(encoded)
        keys.extend(KEY.pack(role, ordinal))
        if len(keys) + len(metadata) + len(records) + HEADER.size + 32 > MAX_BYTES:
            raise ValueError('Selection index exceeds size limit')
    body = HEADER.pack(b'RTFXIDX3', 3, len(entries), digest, len(records), len(metadata), len(keys)) + records + metadata + keys
    if len(body) + 32 > MAX_BYTES:
        raise ValueError('Selection index exceeds size limit')
    fd, name = tempfile.mkstemp(prefix='.selection-v3-', suffix='.idx', dir=root)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.write(hashlib.sha256(body).digest())
        _read(temporary)
        if hashlib.sha256((root / 'catalog.json').read_bytes()).digest() != digest:
            raise ValueError('Catalog changed during index construction')
        _destination(destination)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return dict(entries=len(entries), catalog_sha256=digest.hex(), index=str(destination))


def select_v3(data_dir, identifier=None, include=(), exclude=(), rng=None):
    """Return None for invalid infrastructure, propagating semantic selection errors."""
    try:
        root = Path(data_dir).resolve(strict=True)
        digest, records, categories, rows = _read(root / INDEX_NAME)
        if hashlib.sha256((root / 'catalog.json').read_bytes()).digest() != digest:
            return None
    except (OSError, ValueError, TypeError, OverflowError, struct.error):
        return None
    def field(ordinal, name):
        row = rows[ordinal]
        if name == 'categories':
            return [categories[i] for i in row[2]]
        return row[{'id': 0, 'name': 1, 'aliases': 3}[name]]
    ordinal = _select(range(len(rows)), identifier, include, exclude,
                      rng if rng is not None else random, field)
    row = rows[ordinal]
    digest, width, height, frames = records[ordinal]
    digest = digest.hex()
    try:
        return _validate_selected(_entry_from_row(dict(id=row[0], name=row[1],
            path='animations/' + digest + '.gif', sha256=digest, width=width,
            height=height, frames=frames, categories=[categories[i] for i in row[2]],
            aliases=row[3]), root))
    except (OSError, ValueError, TypeError, OverflowError):
        return None
