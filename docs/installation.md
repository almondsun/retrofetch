# Install Retrofetch locally

Installation prepares the animation library upfront and installs a user-local
launcher. It never edits Fastfetch or shell configuration, installs system
packages, or enables terminal startup automatically.

## Quick path

Install Python 3.11+, Kitty (`kitten`), ImageMagick 7 (`magick`), and a Fastfetch
build with Kitty animation and ImageMagick support using your distribution's
package manager. Then, from this checkout:

```sh
sh install.sh
```

This downloads the collection ZIP from Internet Archive, verifies its advertised
size and SHA-1 when available, safely extracts source images/pages/notices into
the user cache, and prepares assets before publishing the launcher. SHA-1 here
detects transfer corruption; it is not an authenticity or licensing guarantee.
Expect a roughly 1.6 GB download and several GB of temporary/extracted data.
Installation can take substantial time; terminal startup never downloads or
converts assets.

Ensure `~/.local/bin` is already on your `PATH`, or invoke
`~/.local/bin/retrofetch` directly. No privileged package installation occurs.

## Reuse local source and choose directories

```sh
sh install.sh --source 'temp/animation factory essential collection'
sh install.sh --prefix "$HOME/.local" --data-dir "$HOME/.local/share/retrofetch"
```

Defaults are `~/.local` for the executable/package,
`${XDG_DATA_HOME:-~/.local/share}/retrofetch` for prepared assets, and
`${XDG_CACHE_HOME:-~/.cache}/retrofetch` for source caching. `--cache-dir` overrides
the source cache. An explicitly chosen data directory must also be selected by
the runtime's `RETROFETCH_DATA_DIR` environment override.

Completed source caches are rehashed before reuse. Interrupted downloads restart
rather than trusting partial files. Extraction validates every archive member
before writing any assets and rejects traversal, symlinks, duplicate/conflicting
paths, encryption, and excessive resource claims. Executables are not extracted.
Concurrent acquisitions of the same cache are rejected; retry after the other
installation finishes.

Existing unrelated prefix files are preserved. The installer refuses an unknown
`bin/retrofetch` or unmarked package directory rather than replacing it. Managed
package updates are staged; preparation failure publishes no new launcher.
The installer neither deletes `temp/` nor removes any source collection.

## Source rights and optional packaging

Source assets have their own notices and restrictions; Internet Archive hosting
does not grant additional rights. Preserve and review the imported notices.
The project's MIT license covers its code, not third-party animations. Source
archives and prepared animation files must not be committed to this repository.

`pyproject.toml` supports optional Python packaging with no Python runtime
dependencies. Packaging the code alone does not download or prepare assets;
the explicit installer is still the complete installation workflow.
The build backend requires setuptools 77.0.3 or newer for the SPDX license
metadata; see the [Python packaging guide](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/).
