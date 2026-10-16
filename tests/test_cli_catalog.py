import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from retrofetch.cli import main


class CatalogCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        gif = b"GIF89a\x64\x00\x64\x00"
        (self.root / "animations").mkdir()
        self.assets = {}
        entries = []
        for name, category in (("dog", "animals/dogs"), ("computer", "technology")):
            contents = gif + name.encode()
            digest = hashlib.sha256(contents).hexdigest()
            self.assets[name] = self.root / "animations" / (digest + ".gif")
            self.assets[name].write_bytes(contents)
            entries.append(dict(id="d1/" + category + "/" + name, name=name,
                                path="animations/" + digest + ".gif", width=100,
                                height=100, frames=2, sha256=digest,
                                categories=[category], aliases=[]))
        (self.root / "catalog.json").write_text(json.dumps(dict(version=1, animations=entries)))
        self.output, self.errors = io.StringIO(), io.StringIO()
        self.output.isatty = lambda: True

    def invoke(self, *args, interactive=True):
        self.output.isatty = lambda: interactive
        env = {"TERM": "xterm-kitty", "RETROFETCH_DATA_DIR": str(self.root),
               "XDG_CONFIG_HOME": str(self.root / "config")}
        with patch.dict(os.environ, env, clear=True), patch("sys.stdout", self.output), \
             patch("sys.stderr", self.errors), patch("retrofetch.cli.render") as render:
            code = main(list(args))
        return code, render

    def test_listing_search_and_categories_work_without_a_terminal(self):
        for args, text in ((["--list"], "computer"), (["--search", "DOG"], "dog"),
                           (["--categories"], "animals/dogs")):
            with self.subTest(args=args):
                self.output.seek(0)
                self.output.truncate()
                code, render = self.invoke(*args, interactive=False)
                self.assertEqual(code, 0)
                self.assertIn(text, self.output.getvalue())
                render.assert_not_called()

    def test_default_random_and_named_selection_respect_filters(self):
        for args in (("--include", "animals"), ("dog",), ("d1/animals/dogs/dog",),
                     ("--exclude", "technology")):
            code, render = self.invoke(*args)
            self.assertEqual(code, 0)
            self.assertEqual(render.call_args.args[0], self.assets["dog"])
        code, render = self.invoke("dog", "--exclude", "animals")
        self.assertEqual(code, 1)
        render.assert_not_called()

    def test_config_defaults_and_cli_overrides(self):
        config = self.root / "settings.toml"
        config.write_text('include = ["technology"]\nsize = "10x5"\nfastfetch_config = "original.jsonc"\n')
        before = config.read_bytes()
        code, render = self.invoke("--config", str(config), "--size", "12x6", "--include", "animals")
        self.assertEqual(code, 0)
        self.assertEqual(render.call_args.args[0], self.assets["dog"])
        self.assertEqual(render.call_args.kwargs["size"], (12, 6))
        self.assertEqual(render.call_args.kwargs["fastfetch_config"], "original.jsonc")
        self.assertEqual(config.read_bytes(), before)

    def test_explicit_render_options_are_not_ignored_in_listing(self):
        with self.assertRaises(SystemExit) as error:
            self.invoke("--list", "--animation-only")
        self.assertEqual(error.exception.code, 2)

    def test_prepare_and_validate_do_not_require_kitty(self):
        with patch("retrofetch.importer.prepare_library", return_value={"animations": 1}) as prepare:
            code, render = self.invoke("--prepare", str(self.root), interactive=False)
        self.assertEqual(code, 0)
        prepare.assert_called_once()
        render.assert_not_called()
        with patch("retrofetch.importer.validate_library", return_value={"animations": 1, "files": 1}) as validate:
            code, _ = self.invoke("--validate", interactive=False)
        self.assertEqual(code, 0)
        validate.assert_called_once_with(self.root)


if __name__ == "__main__":
    unittest.main()
