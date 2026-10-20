#!/usr/bin/env python3
"""Smoke-test trusted release artifacts offline in a disposable installation.

Failed or timed-out child commands print their captured diagnostics to stderr.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import venv

from check_distribution import check


def run(arguments, *, cwd, env):
    try:
        return subprocess.run([str(arg) for arg in arguments], cwd=cwd, env=env,
                              check=True, capture_output=True, text=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        # TimeoutExpired can retain bytes even when text=True was requested.
        for output in (error.stdout, error.stderr):
            if output:
                sys.stderr.write(output.decode('utf-8', errors='replace')
                                 if isinstance(output, bytes) else output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sdist', type=Path)
    parser.add_argument('wheel', type=Path)
    args = parser.parse_args()
    sdist, wheel = args.sdist.resolve(), args.wheel.resolve()
    check(sdist)
    check(wheel)
    with tempfile.TemporaryDirectory(prefix='retrofetch-distribution-') as directory:
        root = Path(directory)
        env = dict(os.environ, HOME=str(root), XDG_CONFIG_HOME=str(root / 'config'),
                   XDG_STATE_HOME=str(root / 'state'), PYTHONDONTWRITEBYTECODE='1',
                   PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        environment = root / 'venv'
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / 'bin/python'
        run([python, '-m', 'pip', 'install', '--no-deps', wheel], cwd=root, env=env)
        # No compiler or external executable is reachable during Python-only startup.
        python_env = dict(env, PATH=str(environment / 'bin'), CC='compiler-unavailable')
        help_text = run([environment / 'bin/retrofetch', '--help'], cwd=root, env=python_env).stdout
        assert 'usage:' in help_text, help_text
        source = root / 'source'
        source.mkdir()
        with tarfile.open(sdist, 'r:gz') as archive:
            # check() already rejected links, traversal and nonregular entries.
            archive.extractall(source, filter='data')
        source = next(source.iterdir())
        prefix = root / 'prefix'
        package = prefix / 'lib/retrofetch'
        package.mkdir(parents=True)
        shutil.copytree(source / 'src/retrofetch', package / 'retrofetch')
        (package / '.retrofetch-install.json').write_text(json.dumps({'managed_by': 'retrofetch', 'version': 1}))
        launcher = prefix / 'bin/retrofetch'
        launcher.parent.mkdir()
        launcher.write_text('#!' + str(python) + '\n# Managed by Retrofetch installer v1\n'
                            'import sys\nsys.path.insert(0, ' + repr(str(package)) + ')\n'
                            'from retrofetch.cli import main\nraise SystemExit(main())\n')
        launcher.chmod(0o755)
        assert run([launcher, '--help'], cwd=root, env=python_env).stdout == help_text
        run([python, source / 'scripts/build_native.py', '--output-dir', root / 'native',
             '--python-launcher', prefix / 'bin/retrofetch-python'], cwd=root, env=env)
        # Activation uses the managed private import path, not the checkout.
        activation_env = dict(env, PYTHONPATH=str(package))
        run([python, '-m', 'retrofetch.native_install', '--prefix', prefix,
             '--frontend', root / 'native/retrofetch'], cwd=root, env=activation_env)
        native_help = run([launcher, '--help'], cwd=root, env=python_env).stdout
        assert native_help.replace('retrofetch-python', 'retrofetch').split() == help_text.split()
        # A real two-frame transparent GIF, local fixture only; no converter/network.
        data = root / 'data'
        (data / 'animations').mkdir(parents=True)
        gif = base64.b64decode('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAICRAEAIfkEAQAAAAAsAAAAAAEAAQAAAgJMAQA7')
        digest = hashlib.sha256(gif).hexdigest()
        (data / 'animations' / (digest + '.gif')).write_bytes(gif)
        (data / 'catalog.json').write_text(json.dumps({'version': 1, 'animations': [{
            'id': 'fixture', 'name': 'Fixture', 'path': 'animations/' + digest + '.gif',
            'width': 1, 'height': 1, 'frames': 2, 'sha256': digest,
            'categories': [], 'aliases': []}]}))
        run([python, '-c', 'from pathlib import Path; from retrofetch.selection_v3 import build_index_v3; '
             'build_index_v3(Path(' + repr(str(data)) + '))'], cwd=root, env=activation_env)
        # Non-TTY dispatch exercises the installed Python fallback without graphics.
        listed = run([launcher, '--data-dir', data, '--list'], cwd=root, env=python_env)
        assert 'Fixture' in listed.stdout, listed.stdout
        assert (prefix / 'bin/retrofetch-python').is_file()
    print('Offline wheel, extracted-sdist native build, and managed activation smoke passed')


if __name__ == '__main__':
    main()
