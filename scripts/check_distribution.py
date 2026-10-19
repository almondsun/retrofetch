"""Reject local/generated assets in code-only wheel and source distributions."""

from pathlib import Path, PurePosixPath
import sys
import tarfile
import zipfile

FORBIDDEN_SUFFIXES = {'.gif', '.iso', '.sqlite3', '.idx', '.zip', '.pyc'}
FORBIDDEN_PARTS = {
    '__pycache__', '.source-verification', '.git', '.aws', '.codex', '.agents',
    'tmp', '.retrofetch-library-work', 'odd', '.vscode', 'recovery', 'source-archives',
}


def check(path):
    if path.suffix == '.whl':
        with zipfile.ZipFile(path) as archive:
            members = archive.namelist()
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
    print(f'{path.name}: {len(members)} code-only members verified')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('usage: check_distribution.py DIST...')
    for argument in sys.argv[1:]:
        check(Path(argument))
