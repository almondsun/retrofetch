# Validate GIF candidates

The offline importer uses `retrofetch.gif` to inspect and decode prospective
animation variants. Rendering never calls this preprocessing module.

- Structural inspection bounds input bytes, canvas pixels, frames, and decoded
  size before ImageMagick is invoked.
- Decoding coalesces optimized frames before comparing visible pixels; invisible
  RGB changes do not establish animation.
- Transparent candidates require transparent corners and at least 95% clear
  perimeter in every composited frame. This intentionally rejects uncertain
  cropped artwork rather than accepting an opaque background with a clear corner.
- Uniform-matte conversion uses boundary-connected flood fill, never global color
  deletion. Ambiguous or near-matte artwork is rejected conservatively.
- Converted GIFs are decoded again to check motion, transparency, frame timing,
  and unchanged retained foreground pixels. This is not a universal aesthetic
  quality guarantee.

ImageMagick runs with resource ceilings and a timeout, using argument arrays and
temporary files rather than interpolated shell commands. Source GIFs are not
modified. Existing clean transparent variants can remain byte-for-byte originals.

Run focused checks with:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_gif.py -v
```
