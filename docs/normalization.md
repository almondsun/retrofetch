# Normalize a prepared library upfront

Normalization places each animation on a **160 × 160 transparent canvas** without
upscaling small artwork. It runs only when explicitly requested, never during
terminal startup. The CLI wraps `normalize_library(data_dir, progress=None,
limit=None)`; progress receives `(completed, total, animation_id)`.

## Geometry and preservation

1. Decode and coalesce the original frames under existing GIF resource limits.
2. Find the union of visible alpha bounds across **every** composited frame.
3. Apply that same crop to every frame, preserving relative movement.
4. Downscale only when the union exceeds 160 pixels on either axis, using a
   point filter to preserve the retro palette; center on a 160-pixel canvas.
5. Re-decode and verify canvas, frame count, exact delays, visible motion, and
   the existing conservative clear-background policy.

Point resampling can eliminate very small movements. Such derivatives are
excluded rather than accepted as static images. A one-pixel transparent border
is reserved around the artwork (maximum foreground bounds 158×158), so large
solid artwork does not falsely fail exterior-transparency checks merely because
it reaches the downscale limit. The checks do not guarantee universal aesthetic quality.

## Publication and recovery

- Original content-addressed GIFs are never overwritten or deleted.
- `catalog.original.json` preserves the **first** input catalog byte-for-byte.
- `normalization.json` records ownership, version, profile, first-original and
  current catalog hashes, input hash, and per-entry original/derived hashes,
  union bounds, or explicit exclusion reasons.
- Existing unknown backups, malformed manifests, altered backup hashes, and
  symlink paths cause a controlled refusal. No unknown backup is overwritten.
- IDs, names, categories, and aliases remain unchanged for accepted entries.
- All candidate blobs and the complete candidate catalog validate before any
  catalog publication. Blobs become available before atomic catalog replacement.
- Backup creation and manifest/catalog publication are separate filesystem
  operations, not a multi-file transaction. Interrupted publication may require
  inspecting the preserved backup and manifest before retrying; it never deletes
  original assets or publishes a catalog referencing missing derived files.

To roll back deliberately, first verify the backup hash against the manifest,
then replace `catalog.json` with the backup and rebuild any derived selection
index. Original GIFs remain available. No automatic rollback or cleanup runs.

A repeat with the same profile and current catalog hash verifies referenced
output hashes and metadata and reuses validated derivatives. A changed source
catalog is processed as fresh input while retaining the first backup. Duplicate
content is transformed only once per run. A nonpositive limit is rejected;
a positive limit produces a partial report without publishing blobs, backups,
or the live normalization manifest/catalog. An all-invalid run raises a
controlled error and leaves the previous catalog intact.

Normalization takes its own nonblocking writer lock. Do not run import or other
library writers concurrently; a detected input-catalog change refuses publication.

## Verification

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_normalization.py' -v
```

Tests use real temporary GIF fixtures for wide, tall, tiny, off-center, and moving
artwork, exact timing, originals/rollback, static exclusion, duplicate reuse,
idempotence, changed catalogs, corrupt derived files/backups, and symlink refusal.
