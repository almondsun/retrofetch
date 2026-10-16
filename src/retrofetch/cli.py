"""Explicit-file rendering entry point; catalog commands follow in the next slice."""

import argparse
import os
from pathlib import Path
import sys

from .rendering import image_size, plain_fastfetch, render


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
    parser.add_argument("--file", type=Path, help="explicit local GIF (catalog selection is not implemented yet)")
    parser.add_argument("--animation-only", action="store_true", help="display only the animation with kitten icat")
    parser.add_argument("--size", type=size_box, metavar="COLSxROWS", help="bounding box in terminal cells; default: one-third width, half height")
    parser.add_argument("--fastfetch-config", metavar="PATH", help="pass a configuration to Fastfetch without modifying it")
    parser.add_argument("--startup", action="store_true", help="quietly skip unsupported/noninteractive terminals and suppress wrapper diagnostics")
    args = parser.parse_args(argv)
    if args.animation_only and args.fastfetch_config:
        parser.error("--fastfetch-config cannot be used with --animation-only")
    kitty = os.environ.get("TERM") == "xterm-kitty" and not (os.environ.get("TMUX") or os.environ.get("STY"))
    if not sys.stdout.isatty() or not kitty or not sys.platform.startswith("linux"):
        if args.startup:
            return 0
        print("retrofetch: requires an interactive Linux Kitty terminal (without a multiplexer)", file=sys.stderr)
        return 1
    if args.file is None:
        parser.error("catalog selection is not implemented yet; supply --file PATH")
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
