"""Generate managed launchers without resolving Python through startup PATH."""
import json
import os
from pathlib import Path
import subprocess
import sys

MANAGED = "# Managed by Retrofetch installer v1"


def launcher_text(package, *, interpreter=None):
    """Validate an absolute interpreter entry and retain its stable symlink path.

    Selection is explicit at installation time; runtime PATH never selects Python.
    The entry itself is retained so system package upgrades may retarget a symlink.
    """
    entry = os.fspath(sys.executable if interpreter is None else interpreter)
    if (not entry or not os.path.isabs(entry) or any(c.isspace() for c in entry)
            or '\x00' in entry or len(os.fsencode(entry)) > 240):
        raise ValueError('Python interpreter must be an absolute shebang-safe executable path')
    try:
        resolved = Path(entry).resolve(strict=True)
        if not resolved.is_file() or not os.access(entry, os.X_OK):
            raise ValueError('Python interpreter must be executable')
        result = subprocess.run([entry, '-I', '-c',
            'import json,sys; print(json.dumps(list(sys.version_info[:2])))'],
            capture_output=True, timeout=5, check=False)
        version = json.loads(result.stdout) if len(result.stdout) <= 128 else None
        if (result.returncode != 0 or not isinstance(version, list) or len(version) != 2
                or any(type(part) is not int for part in version) or tuple(version) < (3, 11)):
            raise ValueError('Python interpreter must provide Python 3.11 or newer')
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise ValueError('cannot validate Python 3.11 or newer interpreter') from error
    return (f'#!{entry}\n{MANAGED}\nimport sys\n'
            f'sys.path.insert(0, {str(package)!r})\n'
            'from retrofetch.cli import main\nraise SystemExit(main())\n')
