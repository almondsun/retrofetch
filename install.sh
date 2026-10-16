#!/bin/sh
# Install explicitly; never edit shell startup files.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m retrofetch.installer "$@"
