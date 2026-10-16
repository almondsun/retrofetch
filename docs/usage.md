# Use Retrofetch

After [installation](installation.md), run `retrofetch` inside Kitty to display a
random prepared retro animation beside your existing Fastfetch output. Without
installation, use `PYTHONPATH=src python3 -m retrofetch` from the checkout.

```sh
retrofetch                              # random animation plus Fastfetch
retrofetch --animation-only             # animation without system information
retrofetch --list                       # list IDs, display names, categories
retrofetch --search computer            # case-insensitive substring search
retrofetch --categories                 # list available category paths
retrofetch d1/animals/cats/cat           # select a listed canonical ID
retrofetch --include animals --exclude '*/spiders'
retrofetch --size 20x10
retrofetch --calibrate-layout           # once, inside Kitty after preparation
retrofetch --file /path/to/custom.gif    # bypass catalog selection
```

Discovery works without an interactive terminal. Rendering is Linux/Kitty-only;
multiplexed sessions are not supported. IDs take precedence over aliases, then
unambiguous display names. Exclusions win over includes. Category filters apply
to random, named, and discovery operations; explicit files bypass those filters
and therefore cannot be combined with CLI category options.

## Settings and precedence

See [catalog settings](catalog.md) for the optional TOML file. `--config PATH`
selects Retrofetch settings, whereas `--fastfetch-config PATH` selects the
existing Fastfetch configuration without rewriting it. CLI size/config options
override settings; repeated CLI include/exclude options replace the corresponding
configured lists, not append to them. `--data-dir PATH` overrides the data
directory; otherwise `RETROFETCH_DATA_DIR` and XDG defaults apply.

Fastfetch retains its modules, formatting, position, padding, and color policy.
Replacing its logo may change implicit logo-derived colors; Retrofetch does not
manage those colors or unset `NO_COLOR`.

## Prepare and check development data

Use the existing extracted source collection without downloading again:

```sh
PYTHONPATH=src python3 -m retrofetch --prepare temp --data-dir data
PYTHONPATH=src python3 -m retrofetch --validate --data-dir data
PYTHONPATH=src python3 -m retrofetch --list --data-dir data
PYTHONPATH=src python3 -m retrofetch --normalize --data-dir data  # upgrade prepared assets
PYTHONPATH=src python3 -m retrofetch --data-dir data  # render in Kitty
```

Preparation is an upfront operation, never part of terminal startup. It reports
progress and records provenance, selections, fallbacks, and exclusions. An
all-invalid import fails without replacing an existing usable catalog. See
[import rules](importing.md) and [source rights](installation.md#source-rights-and-optional-packaging).

Public preparation/installation also normalize artwork and build the read-only
random-selection index. Existing libraries use `--normalize` without original
source material; originals are retained. See [normalization](normalization.md)
and [calibrated centering](rendering.md#stable-artwork-and-calibrated-centering).
The default maximum is 20×10 cells without upscaling. Missing/stale indexes fall
back to the canonical catalog, never a runtime rebuild.

`--startup` is for explicitly enabled shell hooks: unsupported/noninteractive
contexts skip silently; incompatible explicit options still fail. See the
[opt-in examples](../examples/startup.bash) and installation guide. No command
automatically edits shell or Fastfetch configuration.
