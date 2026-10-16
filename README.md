# Retrofetch

Retrofetch is a Linux utility that displays random retro animations alongside
Fastfetch system information in Kitty. Inspired by the aesthetics of the 1990s
and early 2000s internet, it combines classic animations with your existing system
display. New-terminal startup is available only when you explicitly enable it.

## Install

Requirements: Linux, Python 3.11+, Kitty's `kitten`, ImageMagick 7 (`magick`), and
Fastfetch with Kitty animation and ImageMagick support. Install these through
your distribution's package manager first.

```sh
git clone https://github.com/almondsun/retrofetch.git
cd retrofetch
sh install.sh
```

The installer downloads approximately 1.7 GB from Internet Archive and prepares
the library upfront. Expect several GB of free space and substantial preparation
time; the development import took about 31 minutes. Nothing downloads or converts
during terminal startup. Code installs into `~/.local`, assets into XDG data home.
Ensure `~/.local/bin` is on your `PATH`.

Already have the extracted collection? Use `sh install.sh --source /path/to/source`.
See [installation and recovery](docs/installation.md) for directory overrides,
safety checks, and opt-in Bash/Zsh/Fish startup examples.

## Use inside Kitty

```sh
retrofetch                         # random animation plus Fastfetch
retrofetch --animation-only        # animation without system information
retrofetch --search computer       # find names, IDs, and categories
retrofetch --list                  # list selectable IDs
retrofetch --categories
retrofetch --include animals --exclude '*/spiders'
retrofetch --size 20x10
retrofetch --file /path/to/animation.gif
```

Pass an ID from `--list` as the positional argument to select it explicitly.
Discovery commands work without an interactive terminal. See the
[usage guide](docs/usage.md) and [optional TOML settings](docs/catalog.md).

Retrofetch replaces only Fastfetch's logo: it does not rewrite Fastfetch
configuration, reconstruct modules, or manage colors. The terminal owns playback;
the prompt returns, animations scroll away normally, and clearing removes them.
There is no background playback daemon. Rendering supports Kitty only, without
tmux/screen passthrough.

## Library and source rights

The verified development library contains **20,554 eligible animation entries**
sharing **20,284 unique GIFs**. Larger opaque variants are tried conservatively;
unsuitable conversions fall back to usable transparent variants. Corrupt,
ambiguous, and visually static results are excluded. See
[preparation rules](docs/importing.md) and [the verification report](docs/library-report.md).

**MIT covers Retrofetch's code, not the animation collection.** Source notices
are preserved with prepared data. Internet Archive availability is not permission
to redistribute assets; review the collection's terms before using them.
Git and Python release packages contain no animation collection or source archive.

## Develop and verify

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m retrofetch --validate --data-dir data
PYTHONPATH=src python3 -m retrofetch --data-dir data  # after preparing development data
```

The suite uses the standard library and ImageMagick fixtures. Actual Kitty visual
checks are documented separately in [rendering verification](docs/rendering.md).
Contributor boundaries and architecture are in [AGENTS.md](AGENTS.md) and the
[project brief](docs/project.md).
