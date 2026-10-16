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

For normalized canvases beside the information, top padding is
`max(0, (reference_rows - rendered_canvas_rows) // 2)`. Existing horizontal
padding and left/right/top placement remain Fastfetch's. Top placement receives
no vertical offset; auto retains native minimum information width.

Terminal geometry, size, config/search-file content, config environment, or
Fastfetch binary changes invalidate the reference. Missing/stale state retains
native placement; manual rendering gives a diagnostic and startup stays quiet.
Recalibrate after module output height changes, including dynamic command output.
Startup never measures modules twice or silently recalibrates.

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
