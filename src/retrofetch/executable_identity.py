"""Content identity with invocation-local descriptor and pathname race guards."""

import hashlib
import os
from pathlib import Path
import stat


def _stamp(value):
    return (value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def executable_identity(executable, *, legacy=False):
    """Hash a stable regular executable; never persist incidental metadata."""
    if executable is None:
        raise ValueError('Fastfetch executable is unavailable')
    logical = Path(executable).absolute()
    path = logical.resolve(strict=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= 128 * 1024 * 1024:
            raise ValueError('Fastfetch executable is not a regular file')
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = os.fstat(stream.fileno())
        if (_stamp(before) != _stamp(after)
                or _stamp(path.stat()) != _stamp(before)
                or logical.resolve(strict=True) != path):
            raise ValueError('Fastfetch executable changed during identity validation')
    if legacy:
        return [str(path), before.st_dev, before.st_ino, before.st_size,
                before.st_mtime_ns, before.st_ctime_ns]
    return [str(path), before.st_size, digest]
