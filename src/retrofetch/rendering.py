"""Finite native rendering calls; the terminal owns ongoing playback."""

import fcntl
import math
from pathlib import Path
import shlex
import struct
import subprocess
import sys
import termios


DEFAULT_BOX = (20, 10)


def display_geometry(width, height, geometry, size=None):
    """Fit native pixels to a maximum box; terminal cells are not square."""
    columns, rows, cell_width, cell_height = geometry
    box_width, box_height = size or DEFAULT_BOX
    box_width = max(1, min(box_width, columns - 1))
    box_height = max(1, min(box_height, rows - 2))
    scale = min(1.0, box_width * cell_width / width,
                box_height * cell_height / height)
    # Width zero tells Fastfetch to retain native pixels for sub-cell images.
    display_width = int(width * scale / cell_width)
    pixel_width = display_width * cell_width if display_width else width * scale
    display_height = max(1, math.ceil(pixel_width * height / width / cell_height))
    return box_width, box_height, display_width, display_height


def terminal_geometry():
    """Return columns, rows, cell pixel width and cell pixel height."""
    try:
        rows, columns, width, height = struct.unpack(
            "HHHH", fcntl.ioctl(1, termios.TIOCGWINSZ, bytes(8))
        )
    except OSError:
        rows, columns, width, height = 24, 80, 0, 0
    columns, rows = max(columns, 2), max(rows, 3)
    return columns, rows, width / columns or 8, height / rows or 16


def image_size(path):
    """Read canvas geometry, not animation/transparency eligibility."""
    with path.open("rb") as source:
        header = source.read(10)
    if len(header) != 10 or header[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("expected a GIF file")
    width, height = struct.unpack("<HH", header[6:])
    if not width or not height:
        raise ValueError("GIF has an empty canvas")
    return width, height


def supports_animation():
    """Check the installed build, not a guessed minimum version."""
    try:
        help_result = subprocess.run(
            ["fastfetch", "--help", "logo-animation-frame"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5,
        )
        features = subprocess.run(
            ["fastfetch", "--list-features"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return (help_result.returncode == features.returncode == 0
            and "0" in help_result.stdout
            and any(feature in features.stdout.lower()
                    for feature in ("imagemagick6", "imagemagick7")))


def render(path, *, animation_only=False, size=None, fastfetch_config=None, quiet=False):
    """Render an explicit GIF; raise on failure so the CLI owns fallback."""
    path = Path(path).expanduser().resolve(strict=True)
    width, height = image_size(path)
    geometry = terminal_geometry()
    columns, rows, cell_width, cell_height = geometry
    box_width, box_height, display_width, display_height = display_geometry(width, height, geometry, size)
    if animation_only:
        command = [
            "kitten", "icat", "--engine=builtin", "--stdin=no", "--align=left",
            "--transfer-mode=stream", "--passthrough=none", "--loop=-1",
            f"--use-window-size={box_width},{box_height},"
            f"{int(box_width * cell_width)},{int(box_height * cell_height)}",
            str(path),
        ]
    else:
        pixel_width = display_width * cell_width if display_width else min(width, box_height * cell_height * width / height)
        if min(pixel_width, pixel_width * height / width) < 1:
            raise RuntimeError("GIF aspect ratio cannot fit native pixel bounds; normalize the artwork upfront")
        from .runtime_cache import cached_capabilities
        supported = cached_capabilities()
        if supported is None:
            supported = supports_animation()
        if not supported:
            raise RuntimeError("Fastfetch needs Kitty animation and ImageMagick support")
        # Fastfetch expands logo sources using wordexp on Linux. Quote for that
        # parser as well as passing an argv list to avoid source substitution.
        command = [
            "fastfetch", "--logo-type", "kitty", "--logo", shlex.quote(str(path)),
            "--logo-animation-frame", "0", "--logo-width", str(display_width),
            "--logo-height", str(box_height if not display_width and height > box_height * cell_height else 0),
        ]
        if (width, height) == (160, 160):
            from .calibration import layout_padding
            padding = layout_padding(fastfetch_config, size, geometry, display_height)
            if padding is not None:
                command.extend(['--logo-padding-top', str(padding)])
            elif not quiet:
                print('retrofetch: layout missing, stale, or top-positioned; native placement retained (run --calibrate-layout for side-by-side centering)', file=sys.stderr)
        if fastfetch_config is not None:
            command.extend(["--config", fastfetch_config])
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL)
    except FileNotFoundError as error:
        raise FileNotFoundError(f"{command[0]} executable is unavailable") from error
    if result.returncode:
        raise RuntimeError(f"animation rendering failed (exit {result.returncode})")


def plain_fastfetch(config=None):
    command = ["fastfetch"]
    if config is not None:
        command.extend(["--config", config])
    return subprocess.run(command, stdin=subprocess.DEVNULL).returncode
