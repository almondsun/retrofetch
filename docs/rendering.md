# Rendering slice

The rendering boundary accepts either a catalog-selected animation or an explicit
local GIF. See [usage](usage.md) for catalog, importing, installation and settings.

## Run from the checkout

In a Linux Kitty terminal, with Fastfetch and kitten installed:

```sh
PYTHONPATH=src python3 -m retrofetch --file /path/to/animation.gif
PYTHONPATH=src python3 -m retrofetch --file /path/to/animation.gif --size 20x10
PYTHONPATH=src python3 -m retrofetch --file /path/to/animation.gif --animation-only
PYTHONPATH=src python3 -m retrofetch --help
```

`--fastfetch-config PATH` selects an existing Fastfetch configuration without
rewriting it. Normal invocation loads Fastfetch's usual configuration. Color
handling is entirely Fastfetch's responsibility.

`--size COLSxROWS` is a maximum box, clamped to the screen. Default bounds are
**20 columns by 10 rows**, without enlarging small source pixels. Pixel geometry comes from the
terminal; when unavailable, sizing assumes 8x16-pixel cells. Aspect ratio is
preserved. Extremely thin explicit GIFs that would shrink below one pixel fail
explicitly rather than silently displaying Fastfetch's builtin logo.

## Stable artwork and calibrated centering

`retrofetch --normalize --data-dir PATH` prepares a common 160×160 transparent
canvas from animation-wide foreground bounds; [originals remain available](normalization.md).
Rendering never crops or converts assets during startup.

Run `retrofetch --calibrate-layout` **inside Kitty** after normalization.
`--fastfetch-config`, `--config`, and `--size` retain their normal precedence.
Calibration executes modules once in a private PTY at the information-area width,
without modifying Fastfetch configuration. Its reference lives in
`${XDG_STATE_HOME:-~/.local/state}/retrofetch/layout.json`.

Choose the layout contract explicitly:

| Mode | Compatibility | Use when |
| --- | --- | --- |
| `--layout-mode width-sensitive` (default) | Exact effective information width in columns | Wrapping or responsive output can change height. Calibrate each intended width. |
| `--layout-mode fixed-height` | Reuses measured rows across tiling, font, viewport and size changes | You assert that the information block keeps its height across those changes. |

For example, `retrofetch --calibrate-layout --layout-mode fixed-height` explicitly
opts into a stable-height contract. Disabled line wrapping helps keep logical
line counts stable, but **does not prove** that custom or conditional modules are
height-independent. Dynamic-height modules or changed device counts still require
explicit recalibration. No mode promises exact future height for arbitrary dynamic
commands, and Retrofetch never disables modules or rewrites their configuration.

For normalized canvases beside the information, top padding is
`max(0, (reference_rows - current_rendered_canvas_rows) // 2)`. Current image fitting
is recomputed for the current terminal, including cell pixels and available rows.
Existing horizontal padding and native left/right/top placement remain Fastfetch's.
Auto placement is reevaluated at the current width; top receives no side offset.

Version-2 state retains up to 64 recent profiles. Width-sensitive profiles match
terminal columns minus the current logo columns and horizontal padding; top uses
the full terminal width. Different size/font combinations leaving the same
information width can reuse the measurement. The newest compatible profile wins;
recalibrating its contract/width replaces that profile, not unrelated widths.
Oldest profiles are evicted when the limit is reached.

References bind configuration/search-file content and precedence, HOME/XDG search
environment, resolved Fastfetch binary identity, TERM/color environment, NO_COLOR
presence, and NO_CONFIG policy. Config content also binds native wrap/pipe settings.
Missing, stale or unknown-width state retains native placement; manual rendering
gives a diagnostic and startup stays quiet. Startup never measures modules,
creates profiles, or writes state. Current viewport rows are not a reference-height
invalidator: a height-sensitive custom command is outside the fixed-height contract
and requires recalibration even if its configuration bytes are unchanged.

Known owned version-1 state is not reused as either new contract. The first explicit
recalibration preserves its exact bytes in `layout.json.v1.bak` before publishing
version 2; an unrelated backup or unrecognized state causes a refusal. State reads
and writes are bounded; explicit calibration takes a nonblocking writer lock,
preventing concurrent profile loss without adding startup locking. Profile
publication is atomic and refuses symlink state
paths, including symlink parents. Neither migration nor cleanup happens at startup.

Calibration supports JSON/JSONC files. Extended JSON5 and named presets are
rejected explicitly for calibration, not guessed; native rendering still accepts
them without offsets. Explicit files receive bounded sizing; only normalized
160×160 canvases receive the calibrated layout.

`--startup` skips noninteractive/non-Kitty/multiplexed contexts silently and
suppresses Retrofetch diagnostics. It does not install a startup hook. Explicit
invalid options remain errors. Combined-mode rendering failures fall back to
ordinary Fastfetch; animation-only failures return an error.

The Linux Fastfetch build must support ImageMagick and `--logo-animation-frame`.
No downloads, asset conversion, or configuration writes occur during rendering.
Fastfetch may suppress graphics when `NO_COLOR` is set, even while returning
success. Retrofetch does not unset it or force colors/graphics against the user's
Fastfetch/environment policy.
An explicit GIF's header is checked, but full animation/transparency validation
belongs to the importer. Do not treat arbitrary explicit files as curated.

## Checks

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Automated tests capture/mock commands and do not prove visual behavior. Before
accepting rendering changes, check in actual Kitty: looping after the command
returns, correct prompt position, left/right/top Fastfetch layouts, no unexpected
screen or scrollback clearing, scrolling, and disappearance on clear. These
visual checks passed for the initial slice in isolated Kitty 0.49.2 with
Fastfetch 2.69.0: left/right/top and animation-only, visible animation after
helper exit, prompt below usable content, image scrolling away and clearing.
Repeat these checks when changing the rendering boundary.

Independent scratch-PTY probes confirmed both installed native helpers upload
animation packets and return, without emitting screen/scrollback-clear sequences.
Those protocol checks do not replace the visual checklist above.

## Selection responsiveness

Playback uses the existing v2 selection index for random, named, and
category-filtered choices. Named and filtered lookups decode metadata together
and construct only the selected animation entry. Discovery commands still use
the canonical catalog. Missing or invalid indexes fall back to that catalog;
startup never builds an index. Existing libraries need no format migration.

Every indexed invocation still verifies the full index checksum and catalog
content binding. These checks do not verify GIF payload hashes; use `--validate`
for full prepared-library validation. Rendering, animation timing, and library
coverage are unchanged. CLI dependencies load only when their command needs them.

Measure fresh processes against the same library, fixed animation and filters;
report median and p95 separately for random, named, filtered and explicit-file
controls. Warm filesystem measurements are not cold-cache results, and captured
protocol timing is not visible-first-frame latency. Capture all graphics during
benchmarks rather than sending protocol probes to an interactive terminal.

A local Python 3.15.0 comparison on the 36,642-entry library used 30 fresh
processes per version/mode, interleaved with warm filesystem caches. Rendering
was stubbed after selection and input validation; these are not end-to-end
animation or visible-first-frame timings. Median/p95 milliseconds:

| Mode | Baseline | Optimized |
| --- | --- | --- |
| CLI import | 65.3 / 71.4 | 40.5 / 47.4 |
| Random | 128.6 / 136.7 | 128.8 / 136.5 |
| Named | 422.1 / 433.2 | 334.8 / 345.1 |
| Filtered random | 436.0 / 453.3 | 348.7 / 361.4 |
| Filtered named | 433.3 / 443.6 | 348.4 / 359.6 |
| Explicit file | 46.8 / 57.7 | 41.8 / 51.8 |

Named and filtered launches improved about 20%, below the planned 30% target.
These results demonstrate a partial improvement, not completion of that target.

## Optional v3/native acceleration

Preparation and explicit `--refresh-runtime` now publish a compact v3 index
alongside v2. Named and filtered Python playback tries v3 first; older libraries
retain v2/canonical fallback. Both complete index and catalog contents are still
hashed on every indexed invocation. No index is generated during startup.

The optional native frontend can avoid starting Python for supported playback.
It requires a C11 compiler and OpenSSL development headers/libcrypto at build
time; libcrypto is also required at runtime. The ordinary Python installation
remains supported and is the fallback for unsupported options or stale state.
The C source is included in source distributions; wheels remain Python-only.

Build into an explicit scratch directory, never into the source collections:

```sh
python3 scripts/build_native.py --output-dir /path/to/local-build
```

After reviewing the local build, explicitly install it over an existing managed
Python installation (this is NOT done automatically):

```sh
prefix="$HOME/.local"
PYTHONPATH="$prefix/lib/retrofetch" python3 -m retrofetch.native_install \
  --prefix "$prefix" --frontend /path/to/local-build/retrofetch
```

The managed package is private to the selected prefix, so the explicit
`PYTHONPATH` above is required unless Retrofetch is separately installed into
that Python interpreter. Use Python 3.11 or newer.

This retains `bin/retrofetch-python` and records hashes for both launchers.
Installation is serialized. Each replacement and recovery copy is staged beside
its own destination, including when `bin` and `lib` use separate filesystems.
Failed publication restores successful replacements and their original modes;
if a restoration also fails, the diagnostic identifies retained recovery files
and the original failure. Do not remove those files until recovery is complete. An ordinary
subsequent Python installation restores the Python entrypoint; enable the native
frontend again explicitly if desired. Do not use an ELF from a source collection
or an untrusted download.

In the actual target Kitty terminal, explicit maintenance prepares native state:

```sh
retrofetch --refresh-runtime
retrofetch --refresh-native
```

The profile binds prepared category eligibility, relevant environment, exact
terminal geometry, capability/calibration inputs, and dependency content/identity.
Changed settings, resized geometry, unsupported options or stale state cause
silent delegation to the Python entrypoint before output. Combined native mode
currently supports default sizing and prepared 160x160 canvases; ad-hoc filters,
custom sizing/configuration and non-ASCII query folding remain Python paths.
No user configuration is rewritten. Do not run maintenance against active assets
without explicitly choosing that installation update.

The optional extra render cache remains **disabled**: Fastfetch 2.70.0 exposes
read/write-on-miss, disabled, and regenerate modes, but no documented read-only
cache-use mode. The lossless/read-only prerequisite failed, so no extra cache was
created and the 1 GiB budget was not consumed. Existing user-owned Fastfetch cache
policy is not changed by this feature.

Native development checks use isolated fixtures and captured PTYs:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
CC=clang RETROFETCH_TEST_SANITIZE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -p test_native.py -v
```

Sanitizers, malformed-input probes and differential command tests complement,
but do not replace, independent safety review and the real Kitty visual checks.

### Native comparison (local development measurement)

Thirty fresh processes per cell, real 36,642-entry catalog, warm filesystem
caches, and captured/stubbed rendering produced these median/p95 milliseconds.
The original baseline is commit `1f2245a`; these are **not** measured GIF upload
or visible-first-frame times.

| Path | Original | Current Python | Native |
| --- | --- | --- | --- |
| Prepared named/filter | 448.5 / 486.9 | 370.3 / 444.1 | 80.9 / 85.9 |
| Unfiltered random | 87.9 / 100.3 | 87.9 / 98.4 | 77.0 / 85.0 |
| Explicit file | 47.6 / 64.9 | 43.6 / 53.9 | 38.7 / 46.5 |
| Ad-hoc filter fallback | 442.3 / 553.6 | 359.7 / 433.6 | 356.0 / 420.8 |
| Unicode fallback | 449.3 / 572.2 | 365.6 / 415.5 | 363.6 / 391.3 |

The approximately 82% prepared named/filter gain exceeds the 30% target. Native
fallback paths still start Python and do not promise native-path latency. Random
and explicit-file controls showed no greater-than-5% regression. The v3 index
was 15,529,841 bytes; the canonical catalog was 21,050,259 bytes. Integrity
verification remains linear in those contents.

Prepared profiles also verify current Fastfetch PATH resolution: creating a new
executable in an earlier existing PATH directory invalidates the native path,
even when the PATH string itself has not changed. Sanitizer and regression checks
cover this case. Actual isolated Kitty 0.49.2 / Fastfetch 2.70.0 checks passed for native combined
left/right/top layouts, animation-only playback, motion after frontend exit,
interactive prompt placement, scrolling, clearing, and intentional custom-size
Python fallback. Native execution was independently confirmed without a Python
fallback. Repeat affected visual checks when changing the rendering boundary.
