"""Upfront, reversible normalization of prepared animation libraries."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .catalog import Catalog
from .gif import GifError, _run, decode_gif, inspect_gif

CANVAS = 160
PROFILE = {'version': 1, 'canvas_width': CANVAS, 'canvas_height': CANVAS,
           'crop': 'animation-union', 'resize': 'downscale-only-point', 'border': 1}
OWNER = 'retrofetch-normalizer'
MANIFEST = 'normalization.json'
BACKUP = 'catalog.original.json'


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _safe(root, relative):
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('unsafe normalization path')
    if not path.resolve().is_relative_to(root):
        raise ValueError('normalization path escapes library')
    if any(item.is_symlink() for item in (path, *path.parents)
           if item.is_relative_to(root)):
        raise ValueError('normalization paths must not be symlinks')
    return path


def _write(path, document):
    path.write_text(json.dumps(document, indent=2, ensure_ascii=True) + '\n')


def _bounds(decoded):
    width = decoded.info.width
    left, top = width, decoded.info.height
    right = bottom = -1
    for frame in decoded.pixels:
        for index in range(width * decoded.info.height):
            if frame[index * 4 + 3]:
                x, y = index % width, index // width
                left, top = min(left, x), min(top, y)
                right, bottom = max(right, x), max(bottom, y)
    if right < left:
        raise GifError('animation has no visible artwork')
    return left, top, right - left + 1, bottom - top + 1


def _normalize(source, output):
    decoded = decode_gif(source)
    if not decoded.animated or not decoded.transparent:
        raise GifError('original must visibly animate on a transparent background')
    left, top, width, height = _bounds(decoded)
    with tempfile.TemporaryDirectory(prefix='retrofetch-normalization-') as directory:
        arguments = []
        for index, frame in enumerate(decoded.pixels):
            raw = Path(directory) / f'{index}.rgba'
            raw.write_bytes(frame)
            arguments.extend([
                '-delay', str(decoded.info.delays[index]), '-dispose', 'Background',
                '-size', f'{decoded.info.width}x{decoded.info.height}',
                '-depth', '8', 'rgba:' + str(raw),
            ])
        arguments.extend([
            '-crop', f'{width}x{height}+{left}+{top}', '+repage',
            '-filter', 'point', '-resize', f'{CANVAS - 2}x{CANVAS - 2}>',
            '-background', 'none', '-gravity', 'center',
            '-extent', f'{CANVAS}x{CANVAS}', '-loop', '0', str(output),
        ])
        _run(arguments)
    normalized = decode_gif(output)
    if (normalized.info.width != CANVAS or normalized.info.height != CANVAS
            or normalized.info.frames != decoded.info.frames
            or normalized.info.delays != decoded.info.delays):
        raise GifError('normalization changed frame count, timing, or canvas')
    if not normalized.animated or not normalized.transparent:
        raise GifError('normalization lost visible motion or clear background')
    return normalized.info, [left, top, width, height]


def _ownership(root):
    manifest_path = _safe(root, MANIFEST)
    backup = _safe(root, BACKUP)
    if not manifest_path.exists():
        if backup.exists():
            raise ValueError('refusing unowned original catalog backup')
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError('invalid normalization manifest') from error
    if (not isinstance(manifest, dict) or manifest.get('owner') != OWNER
            or manifest.get('version') != 1 or not backup.is_file()
            or manifest.get('original_catalog_sha256') != _hash(backup)
            or not isinstance(manifest.get('records'), list)):
        raise ValueError('unrecognized normalization manifest or original backup')
    return manifest


def _validate_candidate(root, rows):
    # Catalog schema validation and exhaustive referenced-blob checks precede use.
    catalog = Catalog.load(root)
    checked = set()
    for row in rows:
        if row['sha256'] in checked:
            continue
        path = _safe(root, row['path'])
        if _hash(path) != row['sha256']:
            raise ValueError('normalized asset hash mismatch')
        info = inspect_gif(path)
        if (info.width, info.height, info.frames) != (
                CANVAS, CANVAS, row['frames']):
            raise ValueError('normalized asset metadata mismatch')
        checked.add(row['sha256'])
    return len(catalog.entries), len(checked)


def normalize_library(data_dir, *, progress=None, limit=None):
    """Publish a validated 160px library, preserving its first catalog and blobs."""
    requested = Path(data_dir).absolute()
    if requested.is_symlink():
        raise ValueError('normalization library must not be a symlink')
    root = requested.resolve(strict=True)
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError('normalization limit must be a positive integer')
    lock_path = _safe(root, '.normalization.lock')
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('another normalization is running') from error
        return _prepare(root, progress=progress, limit=limit)


def _prepare(root, *, progress, limit):
    catalog_path = _safe(root, 'catalog.json')
    Catalog.load(root)
    original_bytes = catalog_path.read_bytes()
    input_hash = hashlib.sha256(original_bytes).hexdigest()
    document = json.loads(original_bytes)
    previous = _ownership(root)
    report = {'profile': PROFILE, 'partial': limit is not None, 'reused': False,
              'successes': 0, 'unique_files': 0, 'exclusions': []}
    if (limit is None and previous and previous.get('profile') == PROFILE
            and previous.get('current_catalog_sha256') == input_hash):
        count, unique = _validate_candidate(root, document['animations'])
        report.update(successes=count, unique_files=unique, reused=True)
        return report
    results = {}
    rows = []
    records = []
    with tempfile.TemporaryDirectory(prefix='.normalization-', dir=root) as directory:
        stage = Path(directory)
        (stage / 'animations').mkdir()
        for index, row in enumerate(document['animations']):
            if limit is not None and index >= limit:
                break
            source_hash = row['sha256']
            if source_hash not in results:
                try:
                    source = _safe(root, row['path'])
                    if _hash(source) != source_hash:
                        raise GifError('original asset hash mismatch')
                    output = stage / 'normalized.gif'
                    info, bounds = _normalize(source, output)
                    digest = _hash(output)
                    target = stage / 'animations' / f'{digest}.gif'
                    if not target.exists():
                        shutil.copyfile(output, target)
                    results[source_hash] = {'sha256': digest, 'frames': info.frames,
                                            'bounds': bounds}
                except (OSError, ValueError, GifError) as error:
                    results[source_hash] = {'reason': str(error)}
            transformed = results[source_hash]
            record = {'id': row['id'], 'original_sha256': source_hash, **transformed}
            records.append(record)
            if 'reason' in transformed:
                report['exclusions'].append(record)
            else:
                normalized_row = dict(row)
                normalized_row.update(
                    path=f"animations/{transformed['sha256']}.gif",
                    sha256=transformed['sha256'], width=CANVAS, height=CANVAS,
                    frames=transformed['frames'],
                )
                rows.append(normalized_row)
            if progress:
                progress(index + 1, len(document['animations']), row['id'])
        report['successes'] = len(rows)
        report['unique_files'] = len({row['sha256'] for row in rows})
        if not rows:
            raise ValueError('No usable normalized animations; original catalog preserved: '
                             + '; '.join(record['reason'] for record in records[:3]))
        candidate = {'version': 1, 'animations': rows}
        _write(stage / 'catalog.json', candidate)
        _validate_candidate(stage, rows)
        if limit is not None:
            return report
        # Reject concurrent source-catalog changes before making anything visible.
        if catalog_path.read_bytes() != original_bytes:
            raise ValueError('catalog changed during normalization; publication refused')
        for asset in (stage / 'animations').iterdir():
            destination = _safe(root, 'animations/' + asset.name)
            if destination.exists():
                if _hash(destination) != asset.stem:
                    raise ValueError('existing content-addressed asset is corrupt')
            else:
                os.replace(asset, destination)
        backup = _safe(root, BACKUP)
        if previous is None:
            # Exclusive creation refuses an unrelated backup appearing mid-run.
            with backup.open('xb') as stream:
                stream.write(original_bytes)
        manifest = {
            'owner': OWNER, 'version': 1, 'profile': PROFILE,
            'original_catalog_sha256': _hash(backup),
            'input_catalog_sha256': input_hash,
            'current_catalog_sha256': _hash(stage / 'catalog.json'),
            'records': records,
        }
        _write(stage / MANIFEST, manifest)
        os.replace(stage / MANIFEST, _safe(root, MANIFEST))
        os.replace(stage / 'catalog.json', catalog_path)
    return report
