#!/usr/bin/env python3
"""Build the optional frontend into an explicitly selected output directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--python-launcher', type=Path)
    parser.add_argument('--sanitize', action='store_true')
    args = parser.parse_args()
    command = [os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
               '-Wformat=2', '-Wconversion', '-fstack-protector-strong', '-D_FORTIFY_SOURCE=2']
    if args.sanitize:
        command += ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    else:
        command += ['-O2', '-Wl,-z,relro,-z,now']
    if args.python_launcher:
        if not args.python_launcher.is_absolute():
            parser.error('--python-launcher must be absolute')
        command.append('-DRETROFETCH_PYTHON_LAUNCHER=' + json.dumps(str(args.python_launcher)))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    command += [str(Path(__file__).resolve().parents[1] / 'native/retrofetch.c'),
                '-lcrypto', '-lm', '-o', str(args.output_dir / 'retrofetch')]
    subprocess.run(command, check=True)


if __name__ == '__main__':
    main()
