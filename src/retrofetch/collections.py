"""Collection identity and conservative cross-source animation reconciliation."""

from collections import defaultdict
import hashlib

from .gif import GifError, inspect_gif, decode_gif

COLLECTIONS = {
    'essential-collection': ('Essential Collection (2001)',
                             'https://archive.org/details/animationfactoryessentialcollection'),
    'anime-factory': ('Anime Factory', 'https://archive.org/details/anime-factory-1'),
    '250k-animations': ('250K_ANIMATIONS',
                        'https://archive.org/details/3DAnimatedClipart2AnimationFactory2008'),
}


def provenance(root):
    keys = [key for key in COLLECTIONS if (root / key).is_dir()
            and not (root / key).is_symlink()]
    if not keys:
        keys = ['essential-collection']  # Historical single-collection API.
    return [{'id': key, 'title': COLLECTIONS[key][0], 'url': COLLECTIONS[key][1]}
            for key in keys]


def _digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def _collection(family):
    return family.split('/', 1)[0]


def _identity(family):
    prefix = 'essential-collection/'
    return family[len(prefix):] if family.startswith(prefix) else family


def organize_collections(root, groups, *, hashes=None, validations=None):
    """Join only cross-source families anchored by identical source GIF bytes.

    Every Essential identity survives, even when a shared variant links several
    legacy families. New collections use qualified IDs, avoiding filename merges.
    """
    if not any((root / key).is_dir() for key in COLLECTIONS):
        return groups, {}
    parents = {family: family for family in groups}

    def find(family):
        while parents[family] != family:
            parents[family] = parents[parents[family]]
            family = parents[family]
        return family

    owners = defaultdict(list)
    animated_headers = {}
    hashes = {} if hashes is None else hashes
    validations = {} if validations is None else validations
    for family, paths in sorted(groups.items()):
        for path in paths:
            if path.is_symlink() or not path.is_file():
                continue
            digest = hashes[path] = _digest(path)
            for other in owners[digest]:
                if _collection(other) != _collection(family) and find(family) != find(other):
                    if digest not in animated_headers:
                        try:
                            info = inspect_gif(path)
                            if info.frames > 1:
                                decoded = decode_gif(path, info)
                                validations[digest] = (info, decoded.animated, decoded.transparent)
                                animated_headers[digest] = decoded.animated
                            else:
                                animated_headers[digest] = False
                        except (GifError, OSError):
                            animated_headers[digest] = False
                    if animated_headers[digest]:
                        parents[find(family)] = find(other)
            if family not in owners[digest]:
                owners[digest].append(family)
    components = defaultdict(list)
    for family in sorted(groups):
        components[find(family)].append(family)
    result, origins = {}, {}
    priority = {key: index for index, key in enumerate(COLLECTIONS)}
    for members in components.values():
        members.sort(key=lambda family: (priority.get(_collection(family), 99), family))
        legacy = [family for family in members if family.startswith('essential-collection/')]
        identities = legacy or members[:1]
        variants = sorted({path for family in members for path in groups[family]})
        references = [
            {'collection': _collection(family), 'family': family,
             'url': COLLECTIONS.get(_collection(family), ('', ''))[1],
             'paths': [p.relative_to(root).as_posix() for p in groups[family]]}
            for family in members
        ]
        for family in identities:
            identity = _identity(family)
            if identity in result:
                raise ValueError(f'Conflicting collection identity: {identity}')
            result[identity] = variants
            origins[identity] = references
    return result, origins


def preserve_selectors(entries, legacy):
    """Pin prior unambiguous lookups without changing catalog lookup precedence."""
    current = {entry['id'].casefold(): entry for entry in entries}
    ids, aliases, names = {}, defaultdict(list), defaultdict(list)
    for entry in legacy:
        key = entry['id'].casefold()
        if key not in current:
            raise ValueError(f'Missing legacy animation: {entry["id"]}')
        ids[key] = key
        current[key]['categories'] = sorted(set(current[key]['categories'] + entry['categories']))
        for alias in entry['aliases']:
            aliases[alias.casefold()].append(key)
        names[entry['name'].casefold()].append(key)
    tokens = set(ids) | set(aliases) | set(names)
    bindings = {}
    for token in tokens:
        candidates = [ids[token]] if token in ids else aliases.get(token) or names.get(token)
        if len(set(candidates)) == 1:
            bindings[token] = candidates[0]
    for token, identity in bindings.items():
        if token in current and token != identity:
            raise ValueError(f'Conflicting legacy selector: {token}')
    for entry in entries:
        identity = entry['id'].casefold()
        entry['aliases'] = sorted({alias for alias in entry['aliases']
                                   if bindings.get(alias.casefold(), identity) == identity})
    for token, identity in bindings.items():
        if token != identity and token not in {alias.casefold() for alias in current[identity]['aliases']}:
            current[identity]['aliases'].append(token)
    for entry in entries:
        entry['aliases'].sort()
