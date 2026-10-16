"""Offline, resumable collection preparation. Never mutates the source collection."""

from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import re
import shutil
import tempfile
from urllib.parse import unquote, urlsplit
from .gif import GifError, inspect_gif, decode_gif, remove_uniform_matte
from .catalog import Catalog
SUFFIX = re.compile('_(?:lb|lc|lw|mc|sc|tc|blk|clr|wht|b|c|w)$', re.I)
NOTICE = re.compile('^(?:readme|use|licen[cs]e|copyright|legal)(?:[_. -].*)?$', re.I)


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _safe(root, relative):
    value = PurePosixPath(relative)
    if value.is_absolute() or '..' in value.parts or '\\' in relative:
        raise ValueError('unsafe library path')
    path = root.joinpath(*value.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('library path escapes root')
    if any((p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root))):
        raise ValueError('symlinks are not allowed in the library')
    return path


def _write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')


class _Links(HTMLParser):

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ('src', 'href') and value:
                self.links.append(value)


def _families(root):
    files = []
    pages = []
    notices = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted((d for d in dirs if not (Path(directory) / d).is_symlink()))
        for name in sorted(names):
            p = Path(directory) / name
            if NOTICE.match(name) and (not p.is_symlink()):
                notices.append(p)
            if p.suffix.lower() == '.gif':
                files.append(p)
            if name.lower().startswith('vp_') and p.suffix.lower() in (
                '.htm',
                '.html',
            ) and (not p.is_symlink()):
                pages.append(p)
    known = {p.relative_to(root).as_posix(): p for p in files}
    assigned = {}
    groups = {}
    for page in sorted(pages):
        if page.stat().st_size > 2 * 1024 * 1024:
            continue
        parser = _Links()
        parser.feed(page.read_text(encoding='latin-1'))
        # Variant pages link to animation files in their own category directory.
        # Navigation images live elsewhere and are not animation-family members.
        local = []
        for link in parser.links:
            parsed = urlsplit(link)
            if parsed.scheme or parsed.netloc:
                continue
            candidate = page.parent / unquote(parsed.path)
            if candidate.parent != page.parent or candidate.suffix.lower() != '.gif':
                continue
            relative = candidate.relative_to(root).as_posix()
            if relative in known:
                local.append(relative)
        if not local:
            continue
        family = (page.parent / page.stem[3:]).relative_to(root).as_posix()
        for relative in sorted(set(local)):
            if relative not in assigned:
                assigned[relative] = family
                groups.setdefault(family, []).append(known[relative])
    for relative, p in sorted(known.items()):
        if relative in assigned:
            continue
        parts = PurePosixPath(relative).parts
        if any(
            part.lower() in ('images', '_images', 'navigation', 'buttons')
            for part in parts[:-1]
        ):
            continue
        family = p.with_name(SUFFIX.sub('', p.stem)).relative_to(root).as_posix()
        groups.setdefault(family, []).append(p)
    return (groups, notices)


def _entry(family, digest, info):
    parts = PurePosixPath(family).parts
    category = parts[1:-1] if parts and re.fullmatch('d[1-4]', parts[0], re.I) else parts[:-1]
    return {
        'id': family,
        'name': parts[-1],
        'path': f'animations/{digest}.gif',
        'width': info.width,
        'height': info.height,
        'frames': info.frames,
        'sha256': digest,
        'categories': ['/'.join(category)] if category else [],
        'aliases': [],
    }


def _publish(destination, staging, catalog, report):
    # Content-addressed files become durable before catalog visibility changes.
    # Keep old content: readers of the old catalog always retain valid references.
    for folder in ('animations', 'notices'):
        target = _safe(destination, folder)
        target.mkdir(exist_ok=True)
        for source in (staging / folder).rglob('*'):
            if not source.is_file():
                continue
            relative = source.relative_to(staging).as_posix()
            final = _safe(destination, relative)
            final.parent.mkdir(parents=True, exist_ok=True)
            if not final.exists() or _hash(final) != _hash(source):
                os.replace(source, final)
    _write_json(staging / 'manifest.json', report)
    _write_json(staging / 'catalog.json', catalog)
    os.replace(staging / 'manifest.json', _safe(destination, 'manifest.json'))
    os.replace(staging / 'catalog.json', _safe(destination, 'catalog.json'))


def _valid_checkpoint(cache, source):
    if not isinstance(cache, dict):
        return False
    if (cache.get('version') != 2 or cache.get('source') != str(source)
            or not isinstance(cache.get('families'), dict)):
        return False
    for family, previous in cache['families'].items():
        if not isinstance(family, str) or not isinstance(previous, dict):
            return False
        if (not isinstance(previous.get('key'), str)
                or not isinstance(previous.get('details'), list)
                or type(previous.get('converted')) is not bool
                or type(previous.get('attempted')) is not bool):
            return False
        for detail in previous['details']:
            if not isinstance(detail, dict) or not isinstance(detail.get('path'), str):
                return False
            if 'reason' in detail and not isinstance(detail['reason'], str):
                return False
        entry = previous.get('entry')
        if entry is None:
            continue
        if not isinstance(entry, dict):
            return False
        if any(not isinstance(entry.get(key), str) or not entry[key]
               for key in ('id', 'name', 'path', 'sha256')):
            return False
        if entry['id'] != family or not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            return False
        if entry['path'] != f"animations/{entry['sha256']}.gif":
            return False
        if any(type(entry.get(key)) is not int or entry[key] < minimum
               for key, minimum in (('width', 1), ('height', 1), ('frames', 2))):
            return False
        for key in ('categories', 'aliases'):
            if (not isinstance(entry.get(key), list)
                    or any(not isinstance(value, str) or not value for value in entry[key])):
                return False
    return True


def prepare_library(source: Path, destination: Path, *, progress=None, limit=None) -> dict:
    source = Path(source).resolve()
    destination = Path(destination).absolute()
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError('limit must be positive')
    if not source.is_dir():
        raise ValueError('source directory does not exist')
    nested = source / 'animation factory essential collection'
    if nested.is_dir() and (not nested.is_symlink()):
        source = nested.resolve()
    if destination.resolve().is_relative_to(source) or source.is_relative_to(destination.resolve()):
        raise ValueError('source and destination must be disjoint')
    if destination.is_symlink():
        raise ValueError('destination may not be a symlink')
    destination.mkdir(parents=True, exist_ok=True)
    groups, notices = _families(source)
    cache_path = _safe(destination, '.import-checkpoint.json')
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    if not _valid_checkpoint(cache, source):
        cache = {'version': 2, 'source': str(source), 'families': {}}
    report = {
        'version': 1,
        'source': str(source),
        'provenance': (
            'Animation Factory Essential Collection; source notices retained; '
            'no redistribution license inferred'
        ),
        'partial': limit is not None,
        'families': len(groups),
        'successes': 0,
        'conversions': 0,
        'fallbacks': 0,
        'exclusions': [],
        'selections': [],
    }
    entries = []
    with tempfile.TemporaryDirectory(prefix='.import-', dir=destination) as directory:
        staging = Path(directory)
        (staging / 'animations').mkdir()
        (staging / 'notices').mkdir()
        for index, (family, variants) in enumerate(sorted(groups.items())):
            if limit is not None and index >= limit:
                break
            fingerprints = [
                (
                    p.relative_to(source).as_posix(),
                    p.lstat().st_size,
                    p.lstat().st_mtime_ns,
                )
                for p in variants
            ]
            key = hashlib.sha256(json.dumps(fingerprints).encode()).hexdigest()
            previous = cache['families'].get(family, {})
            selected = None
            details = []
            attempted = False
            converted = False
            if previous.get('key') == key and previous.get('entry'):
                old = previous['entry']
                asset = _safe(destination, old['path'])
                if asset.is_file() and _hash(asset) == old['sha256']:
                    selected = old
                    details = previous['details']
                    converted = previous['converted']
                    attempted = previous['attempted']
            if selected is None:
                ranked = []
                for p in variants:
                    try:
                        info = inspect_gif(p)
                        ranked.append((info.width * info.height, p, info))
                    except (GifError, OSError) as exc:
                        details.append({
                            'path': p.relative_to(source).as_posix(),
                            'reason': str(exc),
                        })
                # Prefer an original declaring transparency at equal native
                # resolution; decoding still validates that claim. This avoids
                # converting the black variant merely because '_lb' sorts first.
                for _, p, info in sorted(
                    ranked, key=lambda x: (-x[0], not x[2].first_transparent, str(x[1]))
                ):
                    try:
                        decoded = decode_gif(p, info)
                        if not decoded.animated:
                            raise GifError('visually static')
                        candidate = p
                        if not decoded.transparent:
                            attempted = True
                            candidate = staging / 'converted.gif'
                            decoded = remove_uniform_matte(decoded, candidate)
                            converted = True
                        digest = _hash(candidate)
                        shutil.copyfile(candidate, staging / 'animations' / f'{digest}.gif')
                        selected = _entry(family, digest, decoded.info)
                        details.append({
                            'path': p.relative_to(source).as_posix(),
                            'selected': True,
                            'converted': converted,
                        })
                        break
                    except (GifError, OSError) as exc:
                        details.append({
                            'path': p.relative_to(source).as_posix(),
                            'reason': str(exc),
                        })
                cache['families'][family] = {
                    'key': key,
                    'entry': selected,
                    'details': details,
                    'converted': converted,
                    'attempted': attempted,
                }
            if selected:
                entries.append(selected)
                report['successes'] += 1
                report['conversions'] += int(converted)
                report['fallbacks'] += int(attempted and (not converted))
                report['selections'].append({
                    'id': family,
                    'sha256': selected['sha256'],
                    'variants': details,
                })
            else:
                report['exclusions'].append({'id': family, 'variants': details})
            # Persist selected blobs with checkpoint for interruption-safe reuse.
            for asset in (staging / 'animations').iterdir():
                final = _safe(destination, 'animations/' + asset.name)
                final.parent.mkdir(exist_ok=True)
                if not final.exists() or _hash(final) != asset.stem:
                    shutil.copyfile(asset, final)
                asset.unlink()
            if (index + 1) % 100 == 0:
                checkpoint = staging / 'checkpoint.json'
                _write_json(checkpoint, cache)
                os.replace(checkpoint, cache_path)
            if progress:
                progress(index + 1, len(groups), family)
        checkpoint = staging / 'checkpoint.json'
        _write_json(checkpoint, cache)
        os.replace(checkpoint, cache_path)
        # Alias relationships survive byte deduplication, including category names.
        by_hash = {}
        for entry in entries:
            by_hash.setdefault(entry['sha256'], []).append(entry['id'])
        for entry in entries:
            entry['aliases'] = [name for name in by_hash[entry['sha256']] if name != entry['id']]
        for notice in notices:
            relative = notice.relative_to(source).as_posix()
            target = staging / 'notices' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(notice, target)
        if not entries:
            failure = staging / 'import-failure.json'
            _write_json(failure, report)
            os.replace(failure, _safe(destination, 'import-failure.json'))
            raise ValueError('No usable animations; see import-failure.json for exclusions')
        if limit is None:
            _publish(destination, staging, {'version': 1, 'animations': entries}, report)
    return report


def validate_library(destination: Path) -> dict:
    destination = Path(destination)
    _safe(destination, 'catalog.json')
    catalog = Catalog.load(destination)
    if not catalog.entries:
        raise ValueError('No usable animations in catalog')
    blobs = set()
    for entry in catalog.entries:
        path = _safe(destination, f'animations/{entry.sha256}.gif')
        try:
            if _hash(path) != entry.sha256:
                raise ValueError('asset hash mismatch')
            info = inspect_gif(path)
        except (OSError, GifError) as exc:
            raise ValueError(str(exc)) from exc
        if (info.width, info.height, info.frames) != (entry.width, entry.height, entry.frames):
            raise ValueError('asset metadata mismatch')
        blobs.add(entry.sha256)
    return {'animations': len(catalog.entries), 'files': len(blobs)}
