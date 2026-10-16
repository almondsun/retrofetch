# Select animations from a prepared catalog

Retrofetch reads `catalog.json` from its data directory without modifying it or
scanning source staging. Random selection is uniform across eligible family
entries, even when several families share the same content-addressed GIF.

## Configuration

The optional settings file is `$XDG_CONFIG_HOME/retrofetch/config.toml` (default
`~/.config/retrofetch/config.toml`):

```toml
include = ["animals"]
exclude = ["animals/insects", "*/spiders"]
size = "30x15"
fastfetch_config = "~/.config/fastfetch/config.jsonc"
```

All keys are optional. Unknown keys, invalid types, invalid TOML, and nonpositive
sizes are errors. An explicitly requested missing configuration is an error;
a missing default configuration uses defaults. Only leading `~/` expands for
paths; environment variables and shell expressions are not evaluated.

The data location is `RETROFETCH_DATA_DIR`, then `$XDG_DATA_HOME/retrofetch`, then
`~/.local/share/retrofetch`. Reading settings never edits Fastfetch configuration
or changes colors. CLI override handling belongs to the CLI integration layer.

## Discovery rules

- Categories match case-insensitively by exact name, subtree prefix, or shell-style
  glob. Includes are optional and exclusions always win.
- Search matches substrings of IDs, display names, aliases, and categories.
- Explicit selection matches canonical full IDs first, then aliases, then exact
  display names.
  Missing or ambiguous matches fail rather than silently selecting a different GIF.
- Selection validates that the chosen file still exists and rejects symlinks in
  its asset path, including directory symlinks and changes made after loading.
  Catalog loading validates
  schema, geometry, animation frame count, IDs, digest shape, and safe paths,
  but does not hash every GIF at terminal startup.

## Catalog format

A version-1 object contains an `animations` array. Each entry has `id`, `name`,
`path`, `width`, `height`, `frames`, `sha256`, `categories`, and `aliases`.
Paths must be `animations/<sha256>.gif` with a lowercase 64-character hexadecimal
SHA-256. Dimensions are positive integers; frames is at least two. Category and
alias arrays contain strings. Frame count is a schema constraint, not proof of
visible animation; the importer validates actual animation content.

## Verify

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_catalog.py' -v
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_config.py' -v
```

These checks cover filters, aliases, ambiguity, injectable random selection,
read-only rejection, schema/path validation, missing files, and settings defaults.
