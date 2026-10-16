# Verified development library

The complete local collection was prepared into `data/` and validated by hashes
and GIF metadata. After independent verification of both prepared library copies,
the user-authorized disposable `temp/` source staging was removed.

| Result | Count |
| --- | ---: |
| Candidate families | 27,799 |
| Usable catalog entries | 20,554 |
| Unique normalized GIFs | 20,283 |
| Preserved original GIFs | 20,284 |
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

After normalization, validation returned `{"animations": 20554, "files": 20283}`. The manifest at
`data/manifest.json` records each selection and exclusion with variant diagnostics;
see [selection and quality boundaries](importing.md#selection-and-quality-boundaries).
Exclusions are conservative validation outcomes, not a judgment of artistic quality.

Original catalog SHA-256:
`28aec6f9e8f08529fc25132594740978967f4f303b9dc64e3809b33ffa358969`.

Normalized catalog SHA-256:
`e8a1f590d05bd6fa989c72c7bb9bdaeec1f9cf3a9cc5617bad42dd9fcc6ee7d6`.

Generated assets are ignored by Git and excluded from Python distributions.
The 170 source notices remain under `data/notices/`; Retrofetch's MIT license
does not license those animations. Do not redistribute assets without the
necessary permissions. For future imports, keep staging until verification and
explicit cleanup authorization.

## Display normalization and startup measurements

Full normalization took 67 minutes 41 seconds upfront. All 20,554 entries were
retained without additional exclusions. Every canvas is 160×160 pixels; artwork
is centered using animation-wide bounds and never enlarged. Exact frame delays,
identities, aliases, and every original GIF hash were preserved. The exact original
catalog is retained as `catalog.original.json`.

Thirty matched random selections at 120×40 cells with 10×20-pixel cells produced:

| Process start to first output | Median | p95 |
| --- | ---: | ---: |
| Previous, warm | 894.5 ms | 907.5 ms |
| Normalized/indexed, warm | 84.8 ms | 98.2 ms |
| Normalized/indexed, fresh image cache | 116.4 ms | 133.0 ms |

These controlled graphical probes meet the 150/250 ms targets. They are not
desktop shortcut-to-window measurements, and fresh image cache does not mean
flushed operating-system caches. `NO_COLOR` was omitted only in isolated graphics
probes; no permanent color settings were changed.

## Initial exact-geometry Kitty checks

At 104×51 cells and 9×20 pixels per cell, explicit calibration measured 30
information rows. A 153-pixel canvas occupied 17 columns and 8 rows, with 11 rows
of top padding. Wide, tall, tiny, and off-center samples were visibly bounded
and centered in native left/right layouts. Top positioning and animation-only
mode remained independent; the prompt did not overlap. Scrolling moved graphics
away and clearing removed them. Visual captures used an isolated Kitty window,
not experimental graphics emitted into the development terminal.

The installed launcher was also checked against the actual user Fastfetch config.
Three captures showed distinct animation phases after the helper exited, including
a 1.37-second interval beyond the sample's one-second loop. Default calibration
was saved for that window's fingerprint; rerun `retrofetch --calibrate-layout`
inside a differently sized terminal. Installed module/data hashes matched the
verified repository, and installed full validation passed.

## Completion measurements

A later, separate comparison used 20 interleaved samples per version, the same
seed-selected seven-frame animation, 120×40 cells with 10×20-pixel cells, and a
six-module configuration. OS page caches were warm. These values are not directly
comparable to the earlier random-corpus cohort.

| Controlled warm timing | Baseline median / p95 | Completion median / p95 |
| --- | ---: | ---: |
| First byte / graphics packet | 98.31 / 108.91 ms | 69.77 / 74.70 ms |
| Wrapper before native rendering | 95.19 / 106.60 ms | 67.24 / 71.57 ms |
| Process completion | 119.76 / 133.10 ms | 83.90 / 89.31 ms |

Wrapper median improved 29.36%. Fresh per-image cache, with OS caches still warm,
produced 96.39 / 106.70 ms median/p95 first graphics. With the actual system
Fastfetch configuration, warm first graphics measured 67.39 / 72.86 ms and process
completion 101.82 / 108.14 ms. Modules and colors were not disabled for speed.
Peak child RSS increased from 31,556 to 36,032 KiB (about 4.4 MiB); this is not
aggregate simultaneous memory or GPU allocation.

### Reusable centering and ongoing playback

One explicit fixed-height calibration measured 29 information rows. Actual
9pt (129×62 cells, 7×16 pixels) and 11pt (100×49, 9×20 pixels) windows, followed by
a 100×23 tiled window, all used the computed 10-row top padding instead of native
top padding 2. Startup left calibration bytes unchanged. Representative artwork,
left/right/top positioning, prompt separation, playback, scrolling and clearing
passed captured visual checks. Short viewports naturally scroll a taller block;
Retrofetch does not pin output or continuously reflow it.

Five-second samples of owned Kitty processes, excluding helpers/compositor/GPU:

| Windows | Idle CPU / RSS | Animated CPU / RSS |
| --- | ---: | ---: |
| 1 | 0% / 144.4 MiB | 1.00% / 144.5 MiB |
| 3 | 0% / 383.8 MiB | 2.60% / 391.0 MiB |

CPU percentages are equivalents of one core; RSS is summed across the owned
Kitty processes, not animation-only memory. These short samples do not establish
long-term behavior for every animation. Launch-to-Hyprland-client-visible timing
was 223–331 ms, not physical shortcut-to-first-composited-frame latency.

Final installed checks invoke Retrofetch directly from Bash rather than a Python
test parent, preserving representative Fastfetch terminal detection. The installed
29-row fixed-height reference gave 10 rows of padding at both font sizes, and a
47-column pane correctly selected native auto/top placement. Distinct sprite
frames at 2.37 and 3.74 seconds after the prompt returned demonstrate playback
continuing beyond multiple measured one-second animation periods. The deployed
package, catalog and indexes matched the verified source/library, and installed
full validation passed.
