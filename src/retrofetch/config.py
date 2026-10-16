"""Read-only TOML settings and XDG locations."""

from dataclasses import dataclass
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class Settings:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    size: tuple[int, int] | None = None
    fastfetch_config: str | None = None


def _environment(environ):
    return os.environ if environ is None else environ


def _expand(path, environ):
    # Expand only the current user's home, never variables or shell expressions.
    if path == '~' or path.startswith('~/'):
        return str(Path(environ.get('HOME') or Path.home()) / path[2:])
    return path


def data_directory(environ=None):
    env = _environment(environ)
    if env.get('RETROFETCH_DATA_DIR'):
        return Path(_expand(env['RETROFETCH_DATA_DIR'], env))
    base = env.get('XDG_DATA_HOME') or str(Path(env.get('HOME') or Path.home()) / '.local/share')
    return Path(_expand(base, env)) / 'retrofetch'


def load_settings(path: Path | None = None, environ=None):
    env = _environment(environ)
    explicit = path is not None
    if path is None:
        base = env.get('XDG_CONFIG_HOME') or str(Path(env.get('HOME') or Path.home()) / '.config')
        path = Path(_expand(base, env)) / 'retrofetch/config.toml'
    else:
        path = Path(_expand(str(path), env))
    try:
        with path.open('rb') as source:
            import tomllib
            document = tomllib.load(source)
    except FileNotFoundError as error:
        if not explicit:
            return Settings()
        raise ValueError(f'Configuration does not exist: {path}') from error
    except (OSError, ValueError) as error:
        raise ValueError(f'Cannot read configuration: {error}') from error
    unknown = document.keys() - {'include', 'exclude', 'size', 'fastfetch_config'}
    if unknown:
        raise ValueError('Unknown configuration keys: ' + ', '.join(sorted(unknown)))
    values = {}
    for key in ('include', 'exclude'):
        value = document.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise ValueError(f'{key} must be a list of nonempty category strings')
        values[key] = tuple(value)
    if 'size' in document:
        size = document['size']
        if not isinstance(size, str) or not re.fullmatch(r'[1-9][0-9]*x[1-9][0-9]*', size):
            raise ValueError('size must be a positive COLSxROWS string')
        values['size'] = tuple(map(int, size.split('x')))
    if 'fastfetch_config' in document:
        value = document['fastfetch_config']
        if not isinstance(value, str) or not value:
            raise ValueError('fastfetch_config must be a nonempty path string')
        values['fastfetch_config'] = _expand(value, env)
    return Settings(**values)
