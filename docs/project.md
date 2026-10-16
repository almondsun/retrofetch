# Retrofetch project brief

Retrofetch displays retro animations as terminal content, normally alongside the
user's existing Fastfetch output. Inspired by the neighboring pokeshell project,
it adds an indexed animation library rather than replacing Fastfetch.

**Status:** rendering, catalog/configuration, offline preparation, acquisition,
and installation are implemented and tested. Actual Kitty visual checks passed;
the complete development library is prepared and hash-validated. Delivery uses
the user-approved feature-branch chain integrating into `feature/retrofetch`.
Start with [usage](usage.md),
[installation](installation.md), or [the library report](library-report.md).

## Confirmed behavior

| Area | Requirement |
| --- | --- |
| Platform | Linux distributions generally; Kitty exclusively, not merely for v1. |
| Default | Random retro animation plus Fastfetch system information. |
| Other modes | Animation-only, listing, searching, and selecting an animation by name. |
| Selection | Entire usable library by default; optional category include/exclude filters. |
| Fastfetch | Preserve the existing configuration, modules, information, and formatting; replace the logo. |
| Colors | Entirely out of scope: no color settings, pinning, or automatic preservation. Changes to implicit logo-derived colors are acceptable. |
| Sizing | Maximum 20×10 cells by default, no upscaling; stable 160×160 prepared canvas with configurable bounding box. |
| Centering | Explicit calibration supplies native padding beside Fastfetch; no startup measurement or module reconstruction. |
| Playback | Loop without waiting for playback to finish before returning the prompt. Finite decoding/upload time is still necessary. |
| Lifecycle | Ordinary terminal output: scrolls away and disappears on clear, not pinned. |
| Startup | Explicitly opt-in for Bash, Zsh, and Fish; installation does not modify shell startup files automatically. |
| Render failure | Combined mode falls back to ordinary Fastfetch; manual invocation gives a concise diagnostic. |

## Asset selection rules

"Curated" means choosing variants for every distinct usable animation, not
selecting a small aesthetic subset of the collection.

1. Group source variants using the collection's HTML mappings and reconcile
   unlisted files. Preserve category/name aliases when identical assets overlap.
2. Prefer the highest-resolution usable transparent animated GIF. Never upscale
   a source merely to call it a higher-resolution asset.
3. If a larger opaque variant exists, attempt background removal first. If that
   conversion damages the artwork or cannot be validated confidently, fall back
   to the existing smaller transparent GIF.
4. Exclude a family when no acceptable transparent animation remains. Also
   exclude corrupt GIFs and GIFs that are really still images, including files
   with multiple frames but no visible animation.
5. Prepare the complete library upfront during installation. Runtime must not
   download, extract, or remove backgrounds on first display.
6. Record source paths, selected variants, conversion/fallback outcomes, and
   exclusions in an import manifest. Preserve source notices.

Validate decoded, composited frames rather than relying on suffixes or GIF
transparency flags. Opaque GIFs can use transparent delta frames for optimization;
variants can also differ in crop, canvas, and timing.

Automated quality checks are conservative heuristics, not a guarantee of aesthetic
quality. Ambiguous conversions must not be silently accepted. Flag them for
optional later inspection; do not require manual approval of every imported GIF.

## Implementation direction

- Python 3.11+ CLI, with standard-library configuration and catalog infrastructure.
- Combined mode delegates rendering and layout to Fastfetch:

  ```sh
  fastfetch --logo-type kitty --logo /path/to/animation.gif --logo-animation-frame 0
  ```

- Animation-only mode delegates to `kitten icat`. No custom raw-logo adapter and
  no persistent playback daemon are planned.
- Linux Fastfetch builds need ImageMagick support for the native Kitty backend.
  ImageMagick is also used during upfront asset preparation.
- Keep configuration and data separate: planned user configuration under XDG
  config home and installed assets under XDG data home. Development imports live
  under `data/`, never `temp/`.
- Do not rewrite Fastfetch configuration or reconstruct its module output.
- Auto-fitting happens at invocation time; continuous resize reflow is not planned.
- Startup integration should skip noninteractive/non-Kitty contexts silently.
  Manual unsupported-terminal and animation-only failures should be explicit.

## Source acquisition and rights

Installation should automatically obtain the dataset from the
[Internet Archive collection](https://archive.org/details/animationfactoryessentialcollection).
The approximately 1.6 GB ZIP contains all four discs' extracted contents and
avoids ISO mounting/extraction. Existing local source material can be reused for
development without another download.

The source collection contains its own `readme.txt` and `use.html` restrictions.
Archive availability is not evidence of a permissive license, and downloading at
installation time does not resolve usage or redistribution rights. Preserve and
disclose those notices; do not commit the collection or claim it is MIT-licensed.
Resolve applicable permissions before public asset distribution.

## Verification and remaining risks

- First validate native Fastfetch animation in actual Kitty: finite upload then
  ongoing playback, left/right/top placement, sizing, prompt position, scrolling,
  clearing, and preservation of existing scrollback.
- Capability-check dependencies instead of assuming all distro packages match
  locally inspected Fastfetch 2.69.0 and Kitty 0.49.2. Supported minimum versions
  must follow tested capabilities; these observations are not minimum versions.
- Test import grouping, real transparency, static detection, conversion fallback,
  deduplication aliases, corrupt input, and interrupted installation.
- Test CLI discovery/selection, filters, overrides, fallbacks, and shell guards.
- Background-removal quality thresholds need representative fixtures and
  calibration; no universal aesthetic-damage detector is assumed.
- The full suite runs with `PYTHONPATH=src python3 -m unittest discover -s
  tests -v`. Isolated wheel/source builds and installed-console checks passed.
  Bash startup guards and isolated signed-package Zsh/Fish executable checks passed.
  Real anonymous Internet Archive download, extraction, and hash-verified cache
  reuse passed; deterministic fixtures cover rejected archives and failure paths.
- Use the approved feature-branch chain for implementation work-unit commits;
  do not open PRs, push, or merge without authorization.

## References

- [Working instructions](../AGENTS.md)
- [Development data directory](../data/README.md)
- [Fastfetch logo and animation options](https://github.com/fastfetch-cli/fastfetch/wiki/Logo-options)
- [Kitty graphics animation protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/#animation)
