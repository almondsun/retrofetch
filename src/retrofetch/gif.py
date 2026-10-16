"""Bounded GIF structure inspection and prospective-selection validation."""

from dataclasses import dataclass
from pathlib import Path
import hashlib
import subprocess
import tempfile


MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 4_000_000
MAX_DECODE = 128 * 1024 * 1024
MAX_FRAMES = 1000


class GifError(ValueError):
    """An unsafe, malformed, or unsupported animation."""


@dataclass(frozen=True)
class GifInfo:
    width: int
    height: int
    frames: int
    delays: tuple[int, ...]
    first_transparent: bool


@dataclass
class Decoded:
    info: GifInfo
    pixels: list[bytes]
    animated: bool
    transparent: bool


def inspect_gif(path: Path) -> GifInfo:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise GifError('unsafe GIF file or excessive size')
    data = path.read_bytes()
    pos = 0

    def take(n):
        nonlocal pos
        if pos + n > len(data):
            raise GifError('truncated GIF')
        part = data[pos:pos + n]
        pos += n
        return part

    def subblocks():
        while True:
            n = take(1)[0]
            if not n:
                break
            take(n)
    if take(6) not in (b'GIF87a', b'GIF89a'):
        raise GifError('invalid GIF signature')
    screen = take(7)
    width = int.from_bytes(screen[:2], 'little')
    height = int.from_bytes(screen[2:4], 'little')
    if not width or not height or width * height > MAX_PIXELS:
        raise GifError('invalid or excessive GIF canvas')
    if screen[4] & 128:
        take(3 * 2 ** ((screen[4] & 7) + 1))
    delays = []
    delay = 0
    transparent = False
    first = False
    while True:
        block = take(1)[0]
        if block == 0x3B:
            break
        if block == 0x21:
            label = take(1)[0]
            if label == 0xF9:
                if take(1) != b'\x04':
                    raise GifError('invalid graphics control')
                control = take(4)
                if take(1) != b'\x00':
                    raise GifError('invalid control terminator')
                delay = int.from_bytes(control[1:3], 'little')
                transparent = bool(control[0] & 1)
            else:
                subblocks()
        elif block == 0x2C:
            descriptor = take(9)
            x = int.from_bytes(descriptor[:2], 'little')
            y = int.from_bytes(descriptor[2:4], 'little')
            w = int.from_bytes(descriptor[4:6], 'little')
            h = int.from_bytes(descriptor[6:8], 'little')
            if not w or not h or x + w > width or (y + h > height):
                raise GifError('invalid frame bounds')
            if descriptor[8] & 128:
                take(3 * 2 ** ((descriptor[8] & 7) + 1))
            if not 2 <= take(1)[0] <= 8:
                raise GifError('invalid LZW code size')
            subblocks()
            delays.append(delay)
            if len(delays) == 1:
                first = transparent
            if len(delays) > MAX_FRAMES:
                raise GifError('too many GIF frames')
            delay = 0
            transparent = False
        else:
            raise GifError('unknown GIF block')
    if not delays:
        raise GifError('GIF has no frames')
    return GifInfo(width, height, len(delays), tuple(delays), first)


def _run(arguments, *, stdout=None):
    try:
        result = subprocess.run(
            [
                'magick',
                '-limit',
                'memory',
                '128MiB',
                '-limit',
                'map',
                '256MiB',
                '-limit',
                'disk',
                '512MiB',
                '-limit',
                'thread',
                '1',
                *arguments,
            ],
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GifError('ImageMagick unavailable or timed out') from exc
    if result.returncode:
        raise GifError('ImageMagick rejected GIF')
    return result


def _exterior_clear(pixels, width, height):
    # A transparent delta patch is insufficient: inspect the composited canvas.
    border = set(range(width)) | set(range((height - 1) * width, height * width))
    border.update((y * width for y in range(height)))
    border.update((y * width + width - 1 for y in range(height)))
    corners = {0, width - 1, (height - 1) * width, width * height - 1}
    clear = sum(pixels[i * 4 + 3] == 0 for i in border)
    return (
        all(pixels[i * 4 + 3] == 0 for i in corners)
        and clear / len(border) >= 0.95
    )


def decode_gif(path: Path, info: GifInfo | None = None) -> Decoded:
    info = info or inspect_gif(path)
    expected = info.width * info.height * 4 * info.frames
    if expected > MAX_DECODE:
        raise GifError('decoded animation exceeds memory budget')
    # File output avoids an unbounded stdout allocation from malformed inputs.
    with tempfile.TemporaryDirectory(prefix='retrofetch-decode-') as directory:
        raw = Path(directory) / 'frames.rgba'
        _run([str(path.resolve()), '-coalesce', '-alpha', 'on', '-depth', '8', 'rgba:' + str(raw)])
        if raw.stat().st_size != expected:
            raise GifError('decoded frame dimensions disagree')
        data = raw.read_bytes()
    stride = info.width * info.height * 4
    frames = [data[i:i + stride] for i in range(0, len(data), stride)]

    # Ignore RGB data under fully transparent pixels when comparing visible frames.
    def visible(frame):
        normalized = bytearray(frame)
        for i in range(0, len(frame), 4):
            if not frame[i + 3]:
                normalized[i:i + 3] = b'\x00\x00\x00'
        return hashlib.sha256(normalized).digest()
    animated = len({visible(frame) for frame in frames}) > 1
    transparent = all((_exterior_clear(frame, info.width, info.height) for frame in frames))
    return Decoded(info, frames, animated, transparent)


def remove_uniform_matte(decoded: Decoded, output: Path) -> Decoded:
    """Exact boundary flood fill only; reject ambiguous/antialiased matte edges."""
    w, h = (decoded.info.width, decoded.info.height)
    converted = []
    matte = None
    for frame in decoded.pixels:
        border = set(range(w)) | set(range((h - 1) * w, h * w))
        border.update((y * w for y in range(h)))
        border.update((y * w + w - 1 for y in range(h)))
        colors = {frame[i * 4:i * 4 + 4] for i in border}
        if len(colors) != 1:
            raise GifError('ambiguous nonuniform matte boundary')
        color = next(iter(colors))
        if color[3] != 255 or (matte is not None and color != matte):
            raise GifError('changing matte')
        matte = color
        seen = set(border)
        queue = list(border)
        while queue:
            i = queue.pop()
            x = i % w
            y = i // w
            for j in (
                i - 1 if x else -1,
                i + 1 if x < w - 1 else -1,
                i - w if y else -1,
                i + w if y < h - 1 else -1,
            ):
                if j >= 0 and j not in seen and (frame[j * 4:j * 4 + 4] == color):
                    seen.add(j)
                    queue.append(j)
        foreground = w * h - len(seen)
        if foreground < 1:
            raise GifError('matte removal leaves no artwork')
        # Nearby matte colors commonly indicate antialiasing/halos: fail closed.
        for i in range(w * h):
            pixel = frame[i * 4:i * 4 + 4]
            if i not in seen and max((abs(pixel[c] - color[c]) for c in range(3))) < 48:
                raise GifError('ambiguous near-matte foreground')
            if i not in seen and pixel == color:
                raise GifError('enclosed matte-colored artwork is ambiguous')
        result = bytearray(frame)
        for i in seen:
            result[i * 4:i * 4 + 4] = b'\x00\x00\x00\x00'
        converted.append(bytes(result))
    with tempfile.TemporaryDirectory(prefix='retrofetch-convert-') as directory:
        args = []
        for i, pixels in enumerate(converted):
            raw = Path(directory) / f'{i}.rgba'
            raw.write_bytes(pixels)
            args.extend([
                '-delay',
                str(decoded.info.delays[i]),
                '-dispose',
                'Background',
                '-size',
                f'{w}x{h}',
                '-depth',
                '8',
                'rgba:' + str(raw),
            ])
        _run([*args, '-loop', '0', str(output)])
    checked = decode_gif(output)
    if checked.info.delays != decoded.info.delays or not checked.animated or (not checked.transparent):
        raise GifError('converted timing or animation validation failed')
    # GIF palette quantization must not damage retained artwork.
    for expected, actual in zip(converted, checked.pixels):
        for i in range(0, len(expected), 4):
            if expected[i + 3] != actual[i + 3] or (expected[i + 3] and expected[i:i + 4] != actual[i:i + 4]):
                raise GifError('conversion changed foreground pixels')
    return checked
