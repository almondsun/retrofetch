"""Finite native rendering calls; the terminal owns ongoing playback."""

import fcntl
from pathlib import Path
import shlex
import struct
import subprocess
import termios


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


def render(path, *, animation_only=False, size=None, fastfetch_config=None):
    """Render an explicit GIF; raise on failure so the CLI owns fallback."""
    path = Path(path).expanduser().resolve(strict=True)
    width, height = image_size(path)
    columns, rows, cell_width, cell_height = terminal_geometry()
    box_width, box_height = size or (max(1, columns // 3), max(1, rows // 2))
    box_width, box_height = min(box_width, columns - 1), min(box_height, rows - 2)
    display_width = max(1, min(box_width, int(box_height * cell_height * width / height / cell_width)))
    if animation_only:
        command = [
            "kitten", "icat", "--engine=builtin", "--stdin=no", "--align=left",
            "--transfer-mode=stream", "--passthrough=none", "--loop=-1",
            f"--use-window-size={box_width},{box_height},"
            f"{int(box_width * cell_width)},{int(box_height * cell_height)}",
            str(path),
        ]
    else:
        if not supports_animation():
            raise RuntimeError("Fastfetch needs Kitty animation and ImageMagick support")
        # Fastfetch expands logo sources using wordexp on Linux. Quote for that
        # parser as well as passing an argv list to avoid source substitution.
        command = [
            "fastfetch", "--logo-type", "kitty", "--logo", shlex.quote(str(path)),
            "--logo-animation-frame", "0", "--logo-width", str(display_width),
        ]
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
