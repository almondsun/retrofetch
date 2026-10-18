# Prepare and verify the animation library

Preparation is offline and upfront. `prepare_library(source, destination)` accepts
the extracted collection root or its parent; use `data/` for development and the
XDG data directory for installations. Source files are never edited or deleted.
The CLI integration wraps this Python API separately.

At equal native resolution, candidates declaring first-frame transparency are
tried first, then fully decoded and validated. This avoids needless conversion
of an opaque color variant when a clean original is already available.

## Output and verification

- `catalog.json` indexes named families and category aliases; duplicate bytes share
  `animations/<sha256>.gif`. Paths are relative and content-addressed.
- `manifest.json` records source identity, selected variants, conversions,
  fallbacks, and per-family exclusions with variant diagnostics.
- `notices/` preserves source readme, use, license, copyright, and legal documents
  with their relative source paths. These assets are not covered by Retrofetch's
  MIT license merely because they are downloaded alongside it.
- `.import-checkpoint.json` caches successful selections against source path,
  size, and modification time. Selected blobs are hashed again before reuse.
  Malformed checkpoint roots, nested records, and entries are discarded and rebuilt.
  Schema version 2 invalidates selections made before the stricter transparency
  checks. Checkpoints flush every 100 families and at completion; interruption can redo
  the last batch. Do not edit source files while preparation is running.

Call `validate_library(destination)` after preparation. It verifies referenced
file hashes, safe paths, and GIF canvas/frame metadata. Keep `temp/` until full
verification succeeds and cleanup is explicitly approved.

A positive `limit` is for debugging only: its report has `partial: true`, and it
never publishes a catalog or manifest. Existing library catalogs remain intact.
Publication installs content-addressed blobs before atomically replacing each
JSON file; catalogs never reference files that have not been installed. Manifest
and catalog replacements are individually atomic, not a transactional pair.
An import with no usable animations raises a controlled error and writes
`import-failure.json` with exclusion reasons, never an empty catalog. Previously
published catalogs, manifests, and animation files remain intact; this also
applies when a formerly valid source becomes corrupt. A first installation with
no usable animations fails instead of publishing an unusable library.

README files and unrelated destination contents remain untouched. Run only one
importer per destination; concurrent preparation is unsupported.

## Selection and quality boundaries

Variant-page HTML mappings take precedence; anchored variant suffixes reconcile
orphan GIFs. Known navigation-image directories are excluded, not all unlisted
files. Every candidate gets bounded GIF block inspection before ranking by native
canvas area. Only prospective selections are decoded with ImageMagick resource
limits and a 60-second timeout; decoded animations are limited to 128 MiB.

Visible differences are tested on coalesced frames, ignoring RGB under invisible
pixels. Transparency must reach the composited canvas exterior in every frame;
transparent optimization patches on an opaque canvas do not qualify. Each
composited frame must have four fully transparent corners and at least 95% of
its perimeter fully transparent. This deliberately rejects cropped artwork or
foreground touching the border rather than treating a transparent corner as
proof of a removed background.

Opaque candidates use exact, boundary-connected uniform-matte flood fill only.
Changing or nonuniform borders, enclosed matte-colored foreground, and near-matte
colors are rejected as ambiguous. Conversion is re-decoded to verify timing,
transparency, visible motion, and exact retained foreground pixels. It never
upscales. If conversion fails, a smaller validated transparent source is copied
byte-for-byte; otherwise the family is excluded with diagnostic reasons.
Converted GIFs retain frame delays and use infinite looping. Unconverted source
files remain byte-for-byte originals; terminal-side playback requests looping.

These conservative checks are not a universal aesthetic guarantee. They do not
attempt paired-background registration or fuzzy color removal; antialiased and
complex-background artwork is deliberately rejected rather than silently damaged.
Full preparation can take hours for the entire collection; runtime never performs
conversion or acquisition.

## Tests

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_gif.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_importer.py' -v
```

The deterministic fixtures require ImageMagick and are created only in temporary
directories. No collection images are generated or modified by these tests.

## Expanded local collections

Use `tmp/animation-factory-dataset/files/` as the combined source root. Do not
prepare its parent: archived ISO images and recovery artifacts are not candidates.
The importer recognizes `essential-collection`, `anime-factory`, and
`250k-animations`, records their Internet Archive URLs in `manifest.json`, and
retains each selected family's source collection, family, and variant paths.
Notices remain separated by collection. Availability does not grant redistribution
rights; generated assets must not be committed.

Existing Essential IDs and categories retain their historical `d1`–`d4` form.
When rebuilding against a legacy baseline, newly usable Essential families use
`essential-collection/` IDs too. Other collections always use qualified IDs. Families in different collections share candidates only
when an identical, decoded visibly animated GIF links them; matching names alone do
not merge families, and corrupt/shared still images cannot establish identity.
Orphan high-resolution and alternate transparent suffixes are grouped alongside
the existing size/background variants. Identical candidate bytes reuse inspection,
decode, conversion, and rejection results within a preparation run.

For an expanded rebuild, call `prepare_library(source, fresh_stage,
legacy_catalog=current_library / 'catalog.json')`, then `normalize_library`,
`build_index`, and `validate_library`. The optional baseline preserves canonical
IDs, categories, aliases, and previously unambiguous selectors; missing legacy
IDs or irreconcilable canonical-ID selector collisions abort publication. Existing single-collection and CLI usage remain supported.
Checkpoints include the grouping-policy identity so older grouping results are
not silently reused. Debug limits still do not publish a catalog or manifest.

Use a fresh staging library, not the current normalized destination: normalization
preserves its first original catalog. Fully verify the stage before activating it;
keep the previous development and installed libraries as rollback snapshots.
