"""Settings use XDG paths and never rewrite user configuration."""

from pathlib import Path
import tempfile
import unittest

from retrofetch.config import Settings, data_directory, load_settings


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {'HOME': str(self.root)}

    def test_defaults_and_xdg(self):
        self.assertEqual(load_settings(environ=self.env), Settings())
        self.assertEqual(data_directory(self.env), self.root / '.local/share/retrofetch')
        self.assertEqual(data_directory(dict(self.env, XDG_DATA_HOME='/data')), Path('/data/retrofetch'))
        self.assertEqual(data_directory(dict(self.env, RETROFETCH_DATA_DIR='~/library')), self.root / 'library')
        config = self.root / 'retrofetch/config.toml'
        config.parent.mkdir()
        config.write_text('include=["animals"]\nexclude=["*/cats"]\nsize="20x10"\nfastfetch_config="~/custom.jsonc"\n')
        settings = load_settings(environ=dict(self.env, XDG_CONFIG_HOME=str(self.root)))
        self.assertEqual(settings.include, ('animals',))
        self.assertEqual(settings.exclude, ('*/cats',))
        self.assertEqual(settings.size, (20, 10))
        self.assertEqual(settings.fastfetch_config, str(self.root / 'custom.jsonc'))

    def test_explicit_missing_and_invalid_preserve_bytes(self):
        config = self.root / 'config.toml'
        with self.assertRaises(ValueError):
            load_settings(config, self.env)
        for content in ['bad=', 'unknown=1', 'include="animals"', 'exclude=[1]',
                        'size=true', 'size="0x2"', 'size="2x-1"', 'fastfetch_config=1']:
            config.write_text(content)
            before = config.read_bytes()
            with self.subTest(content=content), self.assertRaises(ValueError):
                load_settings(config, self.env)
            self.assertEqual(config.read_bytes(), before)
