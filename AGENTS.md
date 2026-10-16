# Working on Retrofetch

## Start here

- Read [the project brief](docs/project.md) before changing behavior. It records
  confirmed requirements, the current implementation direction, and open risks.
- Rendering, catalog discovery, preparation, and installation are implemented.
  Use `PYTHONPATH=src python3 -m retrofetch --data-dir data` in Kitty, or see
  [usage](docs/usage.md) and [installation](docs/installation.md).
- Inspect `git status --short` before editing and preserve unrelated user changes.
- Keep this file focused on working rules. Update the project brief when a user
  changes a requirement rather than maintaining conflicting copies here.

## Product boundaries

- Target Linux and Kitty only. Use Fastfetch's native Kitty animation backend
  for combined mode, and `kitten icat` for animation-only mode.
- Keep Fastfetch in control of modules, formatting, and layout. Never rewrite the
  user's Fastfetch configuration or manage its colors. Changes to implicit
  logo-derived colors are accepted.
- Never enable shell startup automatically. Bash, Zsh, and Fish integration is
  explicitly opt-in.
- Do not introduce a background playback daemon or pinned terminal widget.

## Assets and safety

- `temp/` is disposable source staging, NOT the permanent asset library. Do not
  delete it until import verification succeeds and cleanup is explicitly approved.
- Prepared development assets belong in `data/`; see its [README](data/README.md).
  Keep source archives and generated assets out of Git.
- Preserve source notices and provenance. The repository's MIT license does not
  establish a license for third-party animation assets.
- Never execute programs contained in the source collection. Treat downloads,
  archive members, filenames, and image metadata as untrusted input.
- GIF extension, filename suffix, frame count, and transparency flags alone do
  not prove that a file visibly animates on a transparent background.
- Preserve usable smaller transparent variants when a larger conversion fails.
  Reject damaged, ambiguous, corrupt, and visually static results.
- Never emit experimental graphics sequences to the user's terminal. Capture
  protocol probes; arrange actual Kitty visual checks explicitly.

## Implementation conventions

- Default artifacts, identifiers, comments, documentation, and CLI copy to English.
- The stack is Python 3.11+ with standard-library runtime infrastructure;
  ImageMagick handles upfront asset preparation. Avoid dependencies without a
  concrete need and documented tradeoff.
- Invoke subprocesses with argument arrays, not interpolated shell commands.
  Keep network access and image conversion out of terminal startup.
- Add deterministic regression tests before behavior changes when a runnable
  expected outcome exists. Keep tests and user-facing documentation with the
  behavior they describe.
- Run the standard-library suite with `PYTHONPATH=src python3 -m unittest discover
  -s tests -v`. See `docs/rendering.md` for runtime and visual checks.
- Document visual Kitty checks separately from automated protocol/unit tests.

## Delivery

- Keep changes in coherent, reviewable work units. The user approved a
  feature-branch chain integrating into `feature/retrofetch`. Pushes, PRs, merges,
  releases, and remote credentials require explicit user authorization.
- Follow the authorized work-unit commit workflow on a feature branch, never
  implement directly on `main`.
- Use Conventional Commits. Never add `Co-Authored-By` or AI attribution.
- Ask before destructive operations, publishing, or remote execution. Report
  failed, skipped, and pending checks honestly.
