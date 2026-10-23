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

The installer displays the download and third-party-rights notice before network
access. Type `yes` to accept, or use `sh install.sh --accept-source-notices` for
an explicitly accepted noninteractive installation. Without acceptance,
noninteractive installation fails before downloading.

The default downloads **all three collections**: Essential Collection (ZIP),
250K Animations (ISO), and the twelve Anime Factory disc images. Expect roughly
**13.71 GB of downloads**, at least **65 GiB free** when cache and destination
share a filesystem, and hours of upfront preparation. On separate filesystems,
the cache needs 45 GiB and the library destination 20 GiB. These conservative
budgets reserve room for extraction, staging, and retained rollback data;
existing rollback directories consume additional disk space.
Archives are verified against source metadata. Transfer hashes detect corruption,
not authenticity or permission to use the artwork. Completed downloads can be
reused on retry; source notices and explicit known-damage exclusions are retained.
Terminal startup never downloads or converts assets.

Preparation also normalizes artwork onto a common 160×160 transparent canvas
and builds a selection index. Existing libraries can run `retrofetch --normalize`
instead of downloading/importing again; originals and the first catalog remain
available. Normalization can take substantial upfront time, never startup time.

Ensure `~/.local/bin` is already on your `PATH`, or invoke
`~/.local/bin/retrofetch` directly. No privileged package installation occurs.

## Reuse local source and choose directories

```sh
sh install.sh --source 'tmp/animation-factory-dataset/files'
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
upgrades prepare and validate a fresh library before publishing any active
output. Previous IDs/selectors are preserved, as are prior normalized GIFs for
in-flight readers. Library and package directories use Linux atomic exchange;
launcher and capability metadata publication participate in reverse-order
rollback if a later publication fails. Old libraries, packages, launcher, and
capability metadata remain in `.retrofetch-rollback-*` directories beside their
targets after success; the installer reports those paths and never removes
those rollback copies automatically. Concurrent installations targeting the
same prefix or library are rejected. Symlinked target ancestors and unmanaged
libraries are rejected. A local `--source` avoids downloading, the acceptance
prompt, and the download-space check.
The installer neither deletes `temp/` nor removes any source collection.

## Upgrading an existing installation

The installer is a **full preparation workflow**, not a code-only updater.
It prepares and validates a fresh library before publishing an upgrade, even
when a prepared library already exists. Reusing the download cache or passing
`--source` can avoid downloads; neither skips import, normalization or indexing.
Allow the preparation time and staging/rollback space described above.

There is no installer option that upgrades code while retaining the prepared
library untouched. Do not work around this by copying package files into the
managed prefix or installing a wheel over its private package directory; those
operations do not coordinate the managed launchers and ownership metadata.
If the installed runtime already matches the desired release, no reinstall or
refresh is needed solely because its release was published.

For a native-managed installation, retain the working installation and rollback
files. The [native setup](rendering.md#optional-v3native-acceleration) describes
explicit frontend activation over an existing managed Python installation; it
is not a code-only package updater. Runtime/profile refresh commands prepare
indexes and compatibility state, not application code. Do not remove ownership
guards or overwrite a native launcher to force an upgrade past a refusal.

## Optional terminal startup

After installation, explicitly copy/source the appropriate guarded example from
`examples/startup.bash`, `examples/startup.zsh`, or `examples/startup.fish` in your
interactive shell configuration. Replace an existing explicit Fastfetch startup
invocation yourself if necessary to avoid duplicate output. Installation makes
none of these changes for you.

Run `retrofetch --calibrate-layout --layout-mode fixed-height` in Kitty for a
viewport-independent information block. This explicit contract reuses the row
count across tiling/font changes; recalibrate after module-height changes. Use
the default width-sensitive mode for wrapping or width-dependent output. See
[calibration details](rendering.md#stable-artwork-and-calibrated-centering).

The examples require interactive Kitty, no tmux/screen, and an available
`retrofetch` command. Unsupported startup contexts are skipped quietly.

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


## Optional native acceleration

The default installation remains Python-only. The source distribution also
contains a C frontend and explicit build helper; using it requires a C11 compiler,
OpenSSL development headers and libcrypto. Wheels do not contain a native ELF.
Follow the [native setup guide](rendering.md#optional-v3native-acceleration) after
installing the managed Python package. Its explicit activation command sets
`PYTHONPATH` to the chosen prefix's private package directory; ordinary
`python3 -m retrofetch.native_install` cannot assume that directory is importable.

Run `retrofetch --refresh-runtime` to prepare v2/v3 indexes and capabilities,
then `retrofetch --refresh-native` in the target Kitty terminal. Neither command
reimports or converts animation assets. Neither command rewrites or migrates
`layout.json`; native refresh can reuse compatible saved calibration. Layout
migration uses separate explicit maintenance, not either refresh command.
Native profiles bind the environment,
settings, dependencies and compatible layout contracts. Current v3 profiles use
content-bound executable identities and calculate placement from current terminal
geometry; legacy v2/v1 profiles remain readable. Runtime capabilities use state
v2 and calibration uses layout state v3. Separate explicit maintenance can use
`retrofetch.calibration.migrate_layout_state()` for proven-compatible version-2
calibration; version-1 or stale state requires recalibration in the previous
layout mode. Startup never migrates state. Stale or unsupported profiles
preserve Python playback rather than silently changing configuration or layout.

Native activation preserves the Python companion and validates managed ownership.
Publication and recovery use destination-local staging for separate filesystems.
If rollback cannot complete, keep the reported recovery directories and resolve
the original failure before retrying; they contain prior bytes and permissions.
An ordinary Python reinstall restores the Python entrypoint; explicitly enable
the native frontend again afterward if desired. No startup hook is enabled.
