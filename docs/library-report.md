# Verified development library

The complete local collection was prepared into `data/` and validated by hashes
and GIF metadata. After independent verification of both prepared library copies,
the user-authorized disposable `temp/` source staging was removed.

| Result | Count |
| --- | ---: |
| Candidate families | 27,799 |
| Usable catalog entries | 20,554 |
| Unique referenced GIFs | 20,284 |
| Excluded families | 7,245 |
| Converted selections | 155 |
| Fallback selections | 6,835 |
| Preserved source notices | 170 |

Preparation took about 31 minutes on the development machine; this is an observed
run, not an installation-time guarantee. The output occupied approximately
373 MB including catalog, manifest, checkpoint, and notices. One hundred
distributed source-file samples had unchanged hashes after preparation.

## Verify or use it

```sh
PYTHONPATH=src python3 -m retrofetch --validate --data-dir data
PYTHONPATH=src python3 -m retrofetch --search computer --data-dir data
PYTHONPATH=src python3 -m retrofetch --data-dir data  # inside Kitty
```

Validation returned `{"animations": 20554, "files": 20284}`. The manifest at
`data/manifest.json` records each selection and exclusion with variant diagnostics;
see [selection and quality boundaries](importing.md#selection-and-quality-boundaries).
Exclusions are conservative validation outcomes, not a judgment of artistic quality.

Catalog SHA-256:
`28aec6f9e8f08529fc25132594740978967f4f303b9dc64e3809b33ffa358969`.

Generated assets are ignored by Git and excluded from Python distributions.
The 170 source notices remain under `data/notices/`; Retrofetch's MIT license
does not license those animations. Do not redistribute assets without the
necessary permissions. For future imports, keep staging until verification and
explicit cleanup authorization.
