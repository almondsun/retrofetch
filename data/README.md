# Prepared development data

The expanded local Retrofetch library contains **36,642 usable catalog entries**
backed by **34,604 unique normalized GIFs**. Sources are the Essential Collection,
Anime Factory, and 250K_ANIMATIONS. Original collection notices and provenance
remain in `notices/` and `manifest.json`; Retrofetch's MIT license does not license
third-party animation assets.

## Use and verify

```sh
PYTHONPATH=src python3 -m retrofetch --validate --data-dir data
PYTHONPATH=src python3 -m retrofetch --search computer --data-dir data
```

All displayed canvases are 160×160, centered without upscaling. Native selected
GIFs and their original catalog are retained separately. Existing Essential IDs,
category filters, aliases, and previously unambiguous selectors remain supported.
Additional source families use collection-qualified identities and aliases.

The input dataset is preserved under `tmp/animation-factory-dataset/` and is never
changed by preparation. `source-integrity.json` documents its partial GIF and 12
known absent payloads; none are represented as complete usable originals.

Generated library files and source datasets are ignored by Git and excluded from
Python distributions. Rollback snapshots remain in the local ignored library
workspace and alongside the installed library. Preserve them until cleanup is
explicitly authorized. Extra legacy blobs retained for in-flight readers are
listed in `compatibility-retained-assets.json`, outside current catalog counts.

See [the import workflow](../docs/importing.md) and
[the library report](../docs/library-report.md) for selection and verification.
