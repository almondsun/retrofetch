"""Catalog discovery, offline preparation, and native terminal rendering."""

import argparse
import os
from pathlib import Path
import sys

class Catalog:
    """Defer catalog imports while preserving the CLI's patchable loading seam."""

    @staticmethod
    def load(*args, **kwargs):
        from .catalog import Catalog as implementation
        return implementation.load(*args, **kwargs)


def image_size(*args, **kwargs):
    from .rendering import image_size as implementation
    return implementation(*args, **kwargs)


def render(*args, **kwargs):
    from .rendering import render as implementation
    return implementation(*args, **kwargs)


def plain_fastfetch(*args, **kwargs):
    from .rendering import plain_fastfetch as implementation
    return implementation(*args, **kwargs)


def data_directory(*args, **kwargs):
    from .config import data_directory as implementation
    return implementation(*args, **kwargs)


def load_settings(*args, **kwargs):
    from .config import load_settings as implementation
    return implementation(*args, **kwargs)


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
    parser.add_argument("--size", type=size_box, metavar="COLSxROWS", help="maximum box in terminal cells; default: 20x10, without enlarging source pixels")
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
    modes.add_argument("--normalize", action="store_true", help="normalize prepared animations upfront and rebuild their selection index")
    modes.add_argument("--refresh-native", action="store_true", help="prepare an optional native profile for the current terminal after explicit runtime refresh")
    modes.add_argument("--refresh-runtime", action="store_true", help="rebuild the selection index and refresh Fastfetch capability metadata without converting assets")
    parser.add_argument("--layout-mode", choices=("fixed-height", "width-sensitive"), help="calibration contract: fixed-height reuses rows across tiling/font changes; default: width-sensitive")
    modes.add_argument("--calibrate-layout", action="store_true", help="measure Fastfetch layout once for centered native logos; rerun after layout changes")
    args = parser.parse_args(argv)
    if args.animation_only and args.fastfetch_config:
        parser.error("--fastfetch-config cannot be used with --animation-only")
    discovery = args.list or args.search is not None or args.categories
    maintenance = args.prepare is not None or args.validate or args.normalize or args.refresh_runtime or args.refresh_native
    if args.layout_mode and not args.calibrate_layout:
        parser.error("--layout-mode requires --calibrate-layout")
    if args.file and args.animation:
        parser.error("choose either an animation name or --file")
    if (discovery or maintenance) and (args.animation or args.file or args.animation_only
                                       or args.fastfetch_config or args.size or args.startup):
        parser.error("discovery/preparation options cannot be combined with rendering options")
    if maintenance and (args.include or args.exclude or args.config):
        parser.error("preparation/validation does not accept runtime settings or category filters")
    if args.file and (args.include or args.exclude):
        parser.error("--file cannot be combined with category filters")
    if args.calibrate_layout and (args.animation or args.file or args.animation_only or args.startup
                                  or args.include or args.exclude):
        parser.error("calibration cannot be combined with animation selection or startup")
    directory = args.data_dir.expanduser() if args.data_dir is not None else data_directory()
    if maintenance:
        import json
        try:
            if args.refresh_native:
                from .native_profile import build_profile
                print(json.dumps(build_profile(directory), sort_keys=True))
                return 0
            if args.refresh_runtime:
                from .runtime_cache import refresh_runtime
                report = refresh_runtime(directory)
                print(json.dumps(report, sort_keys=True))
                return 0 if report["capabilities"]["supported"] else 1
            if args.prepare or args.normalize:
                def progress(done, total, family):
                    if done == 1 or done % 100 == 0 or done == total:
                        print(f"Preparing {done}/{total}: {family}", file=sys.stderr)
                from .normalization import normalize_library
                from .selection_index import build_index
                if args.prepare:
                    from .importer import prepare_library
                    report = prepare_library(args.prepare, directory, progress=progress)
                    report['normalization'] = normalize_library(directory, progress=progress)
                else:
                    report = normalize_library(directory, progress=progress)
                build_index(directory)
                from .selection_v3 import build_index_v3
                build_index_v3(directory)
                from .runtime_cache import refresh_capabilities
                refresh_capabilities()
                print(json.dumps({key: value for key, value in report.items()
                                  if key not in ("selections", "exclusions")}, sort_keys=True))
            else:
                from .importer import validate_library
                print(json.dumps(validate_library(directory), sort_keys=True))
            return 0
        except (OSError, ValueError, RuntimeError) as error:
            print(f"retrofetch: {error}", file=sys.stderr)
            return 1
    kitty = os.environ.get("TERM") == "xterm-kitty" and not (os.environ.get("TMUX") or os.environ.get("STY"))
    if args.calibrate_layout:
        import json
        import subprocess
        from .calibration import calibrate_layout
        if not sys.stdout.isatty() or not kitty or not sys.platform.startswith('linux'):
            print('retrofetch: calibration requires an interactive Linux Kitty terminal', file=sys.stderr)
            return 1
        try:
            settings = load_settings(args.config)
            options = {"layout_mode": args.layout_mode} if args.layout_mode else {}
            report = calibrate_layout(args.fastfetch_config or settings.fastfetch_config,
                                      args.size if args.size is not None else settings.size, **options)
            print(json.dumps(report, sort_keys=True))
            return 0
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            print(f"retrofetch: {error}", file=sys.stderr)
            return 1
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
            selected = None
            if not discovery:
                from .selection_index import select_indexed
                selected = select_indexed(directory, args.animation, include, exclude)
            catalog = (Catalog.load(directory)
                       if selected is None else None)
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
            args.file = (selected if selected is not None else catalog.select(args.animation, include, exclude)).path
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
               fastfetch_config=args.fastfetch_config, quiet=args.startup)
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
