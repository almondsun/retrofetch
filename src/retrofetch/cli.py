"""Catalog discovery, offline preparation, and native terminal rendering."""

import argparse
import json
import os
from pathlib import Path
import sys

from .rendering import image_size, plain_fastfetch, render
from .catalog import Catalog
from .config import data_directory, load_settings


def size_box(value):
    try:
        width, height = map(int, value.lower().split("x"))
        if width > 0 and height > 0:
            return width, height
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("size must be positive COLSxROWS, for example 20x10")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Display a retro GIF in Kitty, alongside Fastfetch by default.")
    parser.add_argument("animation", nargs="?", help="animation ID, alias, or unambiguous name; default: random")
    parser.add_argument("--file", type=Path, help="render an explicit local GIF instead of selecting from the catalog")
    parser.add_argument("--animation-only", action="store_true", help="display only the animation with kitten icat")
    parser.add_argument("--size", type=size_box, metavar="COLSxROWS", help="bounding box in terminal cells; default: one-third width, half height")
    parser.add_argument("--fastfetch-config", metavar="PATH", help="pass a configuration to Fastfetch without modifying it")
    parser.add_argument("--startup", action="store_true", help="quietly skip unsupported/noninteractive terminals and suppress wrapper diagnostics")
    parser.add_argument("--data-dir", type=Path, help="prepared library directory; default: RETROFETCH_DATA_DIR or XDG data home")
    parser.add_argument("--config", type=Path, help="Retrofetch TOML settings; default: XDG config home/retrofetch/config.toml")
    parser.add_argument("--include", action="append", metavar="CATEGORY", help="include category subtree/glob (repeatable; replaces configured includes)")
    parser.add_argument("--exclude", action="append", metavar="CATEGORY", help="exclude category subtree/glob (repeatable; replaces configured excludes)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--list", action="store_true", help="list eligible animation IDs and categories")
    modes.add_argument("--search", metavar="QUERY", help="search IDs, names, aliases, and categories")
    modes.add_argument("--categories", action="store_true", help="list eligible category paths")
    modes.add_argument("--prepare", type=Path, metavar="SOURCE", help="prepare the full library upfront from an extracted collection")
    modes.add_argument("--validate", action="store_true", help="verify prepared catalog file hashes and GIF metadata")
    args = parser.parse_args(argv)
    if args.animation_only and args.fastfetch_config:
        parser.error("--fastfetch-config cannot be used with --animation-only")
    discovery = args.list or args.search is not None or args.categories
    maintenance = args.prepare is not None or args.validate
    if args.file and args.animation:
        parser.error("choose either an animation name or --file")
    if (discovery or maintenance) and (args.animation or args.file or args.animation_only
                                       or args.fastfetch_config or args.size or args.startup):
        parser.error("discovery/preparation options cannot be combined with rendering options")
    if maintenance and (args.include or args.exclude or args.config):
        parser.error("preparation/validation does not accept runtime settings or category filters")
    if args.file and (args.include or args.exclude):
        parser.error("--file cannot be combined with category filters")
    directory = args.data_dir.expanduser() if args.data_dir is not None else data_directory()
    if maintenance:
        from .importer import prepare_library, validate_library
        try:
            if args.prepare:
                def progress(done, total, family):
                    if done == 1 or done % 100 == 0 or done == total:
                        print(f"Preparing {done}/{total}: {family}", file=sys.stderr)
                report = prepare_library(args.prepare, directory, progress=progress)
                print(json.dumps({key: value for key, value in report.items()
                                  if key not in ("selections", "exclusions")}, sort_keys=True))
            else:
                print(json.dumps(validate_library(directory), sort_keys=True))
            return 0
        except (OSError, ValueError, RuntimeError) as error:
            print(f"retrofetch: {error}", file=sys.stderr)
            return 1
    kitty = os.environ.get("TERM") == "xterm-kitty" and not (os.environ.get("TMUX") or os.environ.get("STY"))
    if not discovery and (not sys.stdout.isatty() or not kitty or not sys.platform.startswith("linux")):
        if args.startup:
            return 0
        print("retrofetch: requires an interactive Linux Kitty terminal (without a multiplexer)", file=sys.stderr)
        return 1
    try:
        settings = load_settings(args.config)
        include = settings.include if args.include is None else args.include
        exclude = settings.exclude if args.exclude is None else args.exclude
        if discovery or args.file is None:
            catalog = Catalog.load(directory)
            if discovery:
                entries = (catalog.search(args.search, include, exclude) if args.search is not None
                           else catalog.filtered(include, exclude))
                if args.categories:
                    for category in sorted({c for entry in entries for c in entry.categories}):
                        print(category)
                else:
                    for entry in entries:
                        print(f"{entry.id}\t{entry.name}\t{','.join(entry.categories)}")
                return 0
            args.file = catalog.select(args.animation, include, exclude).path
        args.size = args.size if args.size is not None else settings.size
        args.fastfetch_config = args.fastfetch_config or settings.fastfetch_config
        if args.animation_only:
            args.fastfetch_config = None
    except (OSError, ValueError) as error:
        if not args.startup:
            print(f"retrofetch: {error}", file=sys.stderr)
        return 1
    # Reject invalid input before invoking any external command.
    try:
        image_size(args.file.expanduser())
    except (OSError, ValueError) as error:
        if not args.startup:
            print(f"retrofetch: {error}", file=sys.stderr)
        return 1
    try:
        render(args.file, animation_only=args.animation_only, size=args.size,
               fastfetch_config=args.fastfetch_config)
        return 0
    except (OSError, RuntimeError) as error:
        if not args.startup:
            suffix = "" if args.animation_only else "; falling back to Fastfetch"
            print(f"retrofetch: {error}{suffix}", file=sys.stderr)
        if args.animation_only:
            return 2 if isinstance(error, RuntimeError) else 1
        try:
            return plain_fastfetch(args.fastfetch_config)
        except OSError:
            if not args.startup:
                print("retrofetch: fastfetch executable is unavailable", file=sys.stderr)
            return 1
