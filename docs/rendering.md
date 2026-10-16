# Rendering slice

This slice renders an explicit local GIF. Catalog selection, importing,
installation, and startup snippets are not implemented yet.

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

`--size COLSxROWS` is a bounding box, clamped to the screen. Default bounds are
one-third of terminal width and half its height. Pixel geometry comes from the
terminal; when unavailable, sizing assumes 8x16-pixel cells. Aspect ratio is
preserved. Animation-only mode does not enlarge small source images.

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
belongs to the forthcoming importer. Do not treat arbitrary files as curated.

## Checks

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Automated tests capture/mock commands and do not prove visual behavior. Before
accepting this rendering slice, check in actual Kitty: looping after the command
returns, correct prompt position, left/right/top Fastfetch layouts, no unexpected
screen or scrollback clearing, scrolling, and disappearance on clear. These
visual checks are pending; do not report them as passed.

Independent scratch-PTY probes confirmed both installed native helpers upload
animation packets and return, without emitting screen/scrollback-clear sequences.
Those protocol checks do not replace the visual checklist above.
