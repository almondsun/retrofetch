"""Bounded read-only ISO9660/Joliet extraction; never mount or execute images."""

import hashlib
from pathlib import Path, PurePosixPath
import shutil

BLOCK = 2048
MAX_ENTRIES = 300_000
MAX_DIRECTORY_BYTES = 32 * 1024**2
MAX_FILE_BYTES = 256 * 1024**2
MAX_TOTAL_BYTES = 8 * 1024**3
SUPPORTED = {'.gif', '.htm', '.html', '.txt'}


class IsoError(ValueError):
    """An ISO violates the supported, bounded source format."""


def safe_parents(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise IsoError('source or destination path has a symlink component')


def _number(data, offset, width):
    little = int.from_bytes(data[offset:offset+width], 'little')
    big = int.from_bytes(data[offset+width:offset+2*width], 'big')
    if little != big:
        raise IsoError('ISO endian fields disagree')
    return little


def inspect_iso(path, *, known_truncation=False):
    """Validate directory records and all extents before extraction.

    known_truncation is reserved for callers that verified the pinned damaged
    image hash. Incomplete regular payloads are reported, never materialized.
    """
    path = Path(path)
    safe_parents(path)
    size = path.stat().st_size
    with path.open('rb') as stream:
        descriptor, joliet, terminated = None, False, False
        for sector in range(16, 80):
            stream.seek(sector*BLOCK)
            data = stream.read(BLOCK)
            if len(data) != BLOCK or data[1:7] != b'CD001\x01':
                raise IsoError('invalid ISO volume descriptor')
            if data[0] == 255:
                terminated = True
                break
            if data[0] == 1 and descriptor is None:
                descriptor = data
            if data[0] == 2 and data[88:91] in (b'%/@', b'%/C', b'%/E'):
                descriptor, joliet = data, True
        if descriptor is None or not terminated:
            raise IsoError('missing ISO volume or terminator')
        volume = _number(descriptor,80,4)*BLOCK
        if _number(descriptor,128,2) != BLOCK or volume < size:
            raise IsoError('unsupported ISO block or volume size')
        entries, omitted, seen, directories, extents = [], [], set(), set(), []
        total = 0

        def parse(data):
            if len(data) < 34 or data[0] != len(data) or data[1] or data[26:28] != b'\0\0':
                raise IsoError('unsupported ISO directory record')
            count = data[32]
            if 33+count > len(data) or data[25] & ~3:
                raise IsoError('invalid or multi-extent ISO record')
            if _number(data,28,2) != 1:
                raise IsoError('unsupported ISO volume sequence')
            return _number(data,2,4)*BLOCK, _number(data,10,4), bool(data[25]&2), data[33:33+count]

        def walk(offset, length, parent, depth):
            nonlocal total
            if depth > 32 or offset in directories or length > MAX_DIRECTORY_BYTES or offset+length > size:
                raise IsoError('cyclic, oversized, or truncated ISO directory')
            directories.add(offset)
            extents.append((offset,offset+length))
            stream.seek(offset)
            contents = stream.read(length)
            position = 0
            while position < length:
                count = contents[position]
                if not count:
                    position = (position//BLOCK+1)*BLOCK
                    continue
                if position%BLOCK+count > BLOCK or position+count > length:
                    raise IsoError('ISO record crosses directory boundary')
                start, amount, directory, raw = parse(contents[position:position+count])
                position += count
                if raw in (b'\0', b'\1'):
                    continue
                try:
                    name = raw.decode('utf-16-be' if joliet else 'ascii')
                except UnicodeError as error:
                    raise IsoError('invalid ISO filename encoding') from error
                if ';' in name:
                    name, version = name.rsplit(';',1)
                    if version != '1':
                        raise IsoError('unsupported ISO file version')
                if not name or name in ('.','..') or any(c in name for c in '/\\:\0') or any(ord(c)<32 for c in name):
                    raise IsoError('unsafe ISO member name')
                relative = parent / name
                key = relative.as_posix().casefold()
                if key in seen or len(seen) >= MAX_ENTRIES:
                    raise IsoError('duplicate ISO path or excessive member count')
                seen.add(key)
                if start < 19*BLOCK or start+amount > volume or amount > MAX_FILE_BYTES:
                    raise IsoError('invalid ISO member extent')
                if directory:
                    walk(start,amount,relative,depth+1)
                else:
                    total += amount
                    if total > MAX_TOTAL_BYTES:
                        raise IsoError('ISO exceeds extraction size limit')
                    item = {'path':relative.as_posix(),'offset':start,'size':amount}
                    if start+amount > size:
                        if not known_truncation:
                            raise IsoError('truncated ISO payload')
                        omitted.append(item)
                    else:
                        entries.append(item)
                    if amount:
                        extents.append((start,start+amount))
        offset,length,_,_ = parse(descriptor[156:156+descriptor[156]])
        walk(offset,length,PurePosixPath(),0)
        extents.sort()
        if any(left[1] > right[0] for left,right in zip(extents,extents[1:])):
            raise IsoError('overlapping ISO payload or directory extents')
        return {'entries':entries,'omitted':omitted,'volume_bytes':volume,'image_bytes':size,'joliet':joliet}


def extract_iso(path, destination, *, known_truncation=False):
    destination = Path(destination)
    safe_parents(destination)
    if destination.exists():
        raise IsoError('extraction destination already exists')
    report = inspect_iso(path, known_truncation=known_truncation)
    destination.mkdir(parents=True)
    hashes = {}
    try:
        with Path(path).open('rb') as source:
            for item in report['entries']:
                relative = PurePosixPath(item['path'])
                if relative.suffix.lower() not in SUPPORTED:
                    continue
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True,exist_ok=True)
                source.seek(item['offset'])
                digest, remaining = hashlib.sha256(), item['size']
                with target.open('xb') as output:
                    while remaining:
                        block = source.read(min(1024*1024,remaining))
                        if not block:
                            raise IsoError('ISO changed or truncated during extraction')
                        output.write(block)
                        digest.update(block)
                        remaining -= len(block)
                hashes[item['path']] = digest.hexdigest()
    except Exception:
        shutil.rmtree(destination)
        raise
    return {'files':hashes,'omitted':report['omitted']}
