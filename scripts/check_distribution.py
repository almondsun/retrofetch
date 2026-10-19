"""Reject local/generated assets in code-only wheel and source distributions."""

from pathlib import Path, PurePosixPath
import sys
import tarfile
import zipfile

REQUIRED_MODULES = {
    'retrofetch/cli.py', 'retrofetch/native_install.py',
    'retrofetch/native_profile.py', 'retrofetch/selection_v3.py',
}
REQUIRED_NATIVE = {'native/retrofetch.c', 'scripts/build_native.py'}

FORBIDDEN_SUFFIXES = {'.gif', '.iso', '.sqlite3', '.idx', '.zip', '.pyc'}
FORBIDDEN_PARTS = {
    '__pycache__', '.source-verification', '.git', '.aws', '.codex', '.agents',
    'tmp', '.retrofetch-library-work', 'odd', '.vscode', 'recovery', 'source-archives',
}


def check(path):
    if path.suffix == '.whl':
        with zipfile.ZipFile(path) as archive:
            members = archive.namelist()
            for entry in archive.infolist():
                if not entry.is_dir():
                    with archive.open(entry) as stream:
                        if stream.read(4) == b'\x7fELF' or PurePosixPath(entry.filename).suffix.lower() in {'.so', '.o', '.a'}:
                            raise ValueError(f'{path.name}: native binary in Python-only wheel: {entry.filename}')
    elif path.name.endswith('.tar.gz'):
        with tarfile.open(path, 'r:gz') as archive:
            entries = archive.getmembers()
            if any(not (entry.isfile() or entry.isdir()) for entry in entries):
                raise ValueError(f'{path.name}: unexpected nonregular archive member')
            members = [entry.name for entry in entries]
    else:
        raise ValueError(f'Unexpected distribution: {path.name}')
    for name in members:
        member = PurePosixPath(name)
        if (member.is_absolute() or '..' in member.parts
                or member.suffix.lower() in FORBIDDEN_SUFFIXES
                or FORBIDDEN_PARTS.intersection(member.parts)
                or any(part in {'animations', 'temp', 'data', 'backups'} for part in member.parts)):
            raise ValueError(f'{path.name}: forbidden generated/local member {name}')
    required = REQUIRED_MODULES
    normalized = set(members)
    if path.name.endswith('.tar.gz'):
        roots = {PurePosixPath(name).parts[0] for name in members}
        if len(roots) != 1:
            raise ValueError(f'{path.name}: expected one source root')
        normalized = {str(PurePosixPath(*PurePosixPath(name).parts[1:])) for name in members}
        required = {'src/' + name for name in REQUIRED_MODULES} | REQUIRED_NATIVE
    missing = required - normalized
    if missing:
        raise ValueError(f'{path.name}: missing required code: {", ".join(sorted(missing))}')
    print(f'{path.name}: {len(members)} code-only members verified')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('usage: check_distribution.py DIST...')
    for argument in sys.argv[1:]:
        check(Path(argument))
