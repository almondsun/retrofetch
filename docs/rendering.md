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
