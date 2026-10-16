"""Validated, read-only access to a prepared animation catalog."""

from dataclasses import dataclass
from fnmatch import fnmatchcase
import json
from pathlib import Path
import random
import re


@dataclass(frozen=True)
class Entry:
    id: str
    name: str
    path: Path
    width: int
    height: int
    frames: int
    sha256: str
    categories: tuple[str, ...]
    aliases: tuple[str, ...]


def _strings(value, label):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f'{label} must be a list of nonempty strings')
    return tuple(value)


def _category_matches(category, pattern):
    category, pattern = category.casefold(), pattern.casefold()
    return category == pattern or category.startswith(pattern + '/') or fnmatchcase(category, pattern)


def _entry_from_row(row, root):
    """Validate metadata without touching any animation filesystem paths."""
    if not isinstance(row, dict):
        raise ValueError('Catalog animation must be an object')
    for key in ('id', 'name', 'path', 'sha256'):
        if not isinstance(row.get(key), str) or not row[key]:
            raise ValueError(f'Animation {key} must be a nonempty string')
    for key in ('width', 'height', 'frames'):
        if type(row.get(key)) is not int or row[key] < (2 if key == 'frames' else 1):
            raise ValueError(f'Animation {key} must be a positive integer (frames >= 2)')
    if not re.fullmatch(r'[0-9a-f]{64}', row['sha256']):
        raise ValueError('Animation sha256 must be 64 lowercase hexadecimal characters')
    if row['path'] != 'animations/' + row['sha256'] + '.gif':
        raise ValueError('Animation path must be animations/<sha256>.gif')
    return Entry(row['id'], row['name'], root / row['path'], row['width'], row['height'],
                 row['frames'], row['sha256'], _strings(row.get('categories'), 'categories'),
                 _strings(row.get('aliases'), 'aliases'))


def _entries_from_document(document, root):
    if (not isinstance(document, dict) or type(document.get('version')) is not int
            or document['version'] != 1 or not isinstance(document.get('animations'), list)):
        raise ValueError('Catalog must contain version 1 and an animations list')
    entries, identities = [], set()
    for row in document['animations']:
        entry = _entry_from_row(row, root)
        identity = entry.id.casefold()
        if identity in identities:
            raise ValueError(f'Duplicate animation ID: {entry.id}')
        identities.add(identity)
        entries.append(entry)
    return entries


def _validate_selected(entry):
    # Canonical lexical paths were checked when the record was loaded. Require
    # that resolution changes no component, rejecting both leaf and directory
    # symlinks, including ones added after loading the metadata.
    try:
        if entry.path.resolve(strict=True) != entry.path or not entry.path.is_file():
            raise ValueError(f'Animation file is missing or changed: {entry.id}')
    except OSError as error:
        raise ValueError(f'Animation file is missing or changed: {entry.id}') from error
    return entry


class Catalog:
    def __init__(self, entries):
        self.entries = list(entries)

    @classmethod
    def load(cls, data_dir: Path):
        root = Path(data_dir).resolve()
        try:
            document = json.loads((root / 'catalog.json').read_text(encoding='utf-8'))
        except (OSError, ValueError) as error:
            raise ValueError(f'Cannot read catalog: {error}') from error
        return cls(_entries_from_document(document, root))

    def filtered(self, include=(), exclude=()):
        return [entry for entry in self.entries
                if (not include or any(_category_matches(c, p) for c in entry.categories for p in include))
                and not any(_category_matches(c, p) for c in entry.categories for p in exclude)]

    def search(self, query, include=(), exclude=()):
        needle = query.casefold()
        return [entry for entry in self.filtered(include, exclude)
                if any(needle in text.casefold()
                       for text in (entry.id, entry.name, *entry.aliases, *entry.categories))]

    def select(self, identifier=None, include=(), exclude=(), rng=None):
        eligible = self.filtered(include, exclude)
        if identifier is None:
            if not eligible:
                raise ValueError('No animations match the category filters')
            selected = (rng if rng is not None else random).choice(eligible)
        else:
            needle = identifier.casefold()
            matches = [entry for entry in eligible if entry.id.casefold() == needle]
            if not matches:
                matches = [entry for entry in eligible
                           if any(needle == value.casefold() for value in entry.aliases)]
            if not matches:
                matches = [entry for entry in eligible if entry.name.casefold() == needle]
            if len(matches) != 1:
                reason = 'Ambiguous animation' if matches else 'No matching animation'
                raise ValueError(f'{reason}: {identifier}')
            selected = matches[0]
        return _validate_selected(selected)

    def categories(self):
        return sorted({category for entry in self.entries for category in entry.categories})
