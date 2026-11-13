"""Configuration contracts; CLI integration uses scratch profiles only."""
import importlib.util
import shutil
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConfigureTests(unittest.TestCase):
    def test_commands_only_change_image_settings_and_enable_plugin(self):
        self.assertTrue((ROOT / 'configure.py').exists(), 'configure workflow is not implemented')
        spec = importlib.util.spec_from_file_location('gemini_config_test', ROOT / 'configure.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        commands = module.build_commands('/hermes', {'source_provider': 'fixture'})
        self.assertIn(['/hermes', 'config', 'set', 'image_gen.provider', 'gemini-messages'], commands)
        self.assertIn(['/hermes', 'config', 'set', 'image_gen.gemini_messages', '{"source_provider": "fixture"}'], commands)
        self.assertFalse(any('video' in ' '.join(c) for c in commands))
        self.assertFalse(any('model.provider' in ' '.join(c) for c in commands))
        self.assertEqual(commands[-1], ['/hermes', 'tools', 'enable', 'image_gen'])

    def test_check_malformed_image_section_returns_json_not_traceback(self):
        from unittest.mock import patch
        import contextlib
        import io
        import json
        spec = importlib.util.spec_from_file_location('gemini_config_check_bad', ROOT / 'configure.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = io.StringIO()
        with patch('hermes_cli.config.load_config', return_value={'image_gen': 'bad'}), patch('hermes_cli.config.reload_env'), patch('hermes_cli.plugins.discover_plugins'), patch('agent.image_gen_registry.get_active_provider', return_value=None), contextlib.redirect_stdout(output):
            self.assertEqual(module.check_configuration(), 2)
        self.assertFalse(json.loads(output.getvalue())['ready'])

    def test_actual_cli_persists_mapping_and_preserves_video(self):
        import json
        import os
        import subprocess
        import tempfile
        cli = shutil.which('hermes')
        if cli is None:
            self.skipTest('Hermes CLI is not available')
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as home:
            env = dict(os.environ, HERMES_HOME=home)
            for key, value in [('video_gen.provider', 'offline-video'), ('image_gen.model', 'old-image'), ('image_gen.gemini_messages', '{"source_provider": "fixture"}')]:
                result = subprocess.run([cli, 'config', 'set', key, value], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([cli, 'config', 'get', 'image_gen.gemini_messages', '--json'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'source_provider': 'fixture'})
            result = subprocess.run([cli, 'config', 'get', 'video_gen.provider', '--json'], env=env, capture_output=True, text=True)
            self.assertEqual(json.loads(result.stdout), 'offline-video')

    def test_manifest_exposes_high_unspecified_plugin_setting(self):
        from hermes_cli.plugins_settings import plugin_settings_fields
        from unittest.mock import patch
        with patch('hermes_cli.config.load_config_readonly', return_value={}):
            fields = plugin_settings_fields('gemini-image', ROOT)
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]['key'], 'thinking')
        self.assertEqual(fields[0]['type'], 'enum')
        self.assertEqual(fields[0]['choices'], ['high', 'unspecified'])
        self.assertEqual(fields[0]['value'], 'high')

    def test_thinking_only_dry_run_and_real_cli_preserve_source_and_video(self):
        import json
        import os
        import subprocess
        import tempfile
        cli = shutil.which('hermes')
        if cli is None:
            self.skipTest('Hermes CLI is not available')
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as home:
            env = dict(os.environ, HERMES_HOME=home)
            for key, value in [('video_gen.provider', 'offline-video'), ('image_gen.gemini_messages', '{"base_url": "http://127.0.0.1/v1"}')]:
                r = subprocess.run([cli, 'config', 'set', key, value], env=env, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
            script = [sys.executable, str(ROOT / 'configure.py'), '--thinking', 'unspecified', '--hermes-cli', cli]
            before = (Path(home) / 'config.yaml').read_bytes()
            dry = subprocess.run(script + ['--dry-run'], env=env, capture_output=True, text=True)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual((Path(home) / 'config.yaml').read_bytes(), before)
            self.assertEqual(json.loads(dry.stdout)['commands'], [[cli, 'config', 'set', 'plugins.entries.gemini-image.settings.thinking', 'unspecified']])
            applied = subprocess.run(script, env=env, capture_output=True, text=True)
            self.assertEqual(applied.returncode, 0, applied.stderr)
            from utils import fast_safe_load
            config = fast_safe_load((Path(home) / 'config.yaml').read_text())
            self.assertEqual(config['plugins']['entries']['gemini-image']['settings']['thinking'], 'unspecified')
            self.assertEqual(config['image_gen']['gemini_messages'], {'base_url': 'http://127.0.0.1/v1'})
            self.assertEqual(config['video_gen']['provider'], 'offline-video')

    def test_dry_run_does_not_resolve_secret_values(self):
        from unittest.mock import patch
        import contextlib
        import io
        spec = importlib.util.spec_from_file_location('gemini_config_dry', ROOT / 'configure.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = {'providers': {'fixture': {'base_url': 'http://127.0.0.1/v1',
                   'api_mode': 'anthropic_messages', 'key_env': 'FIXTURE_KEY'}}}
        with patch('hermes_cli.config.load_config', return_value=config), patch('hermes_cli.config.reload_env'), patch('agent.secret_scope.get_secret_str') as secret, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(['--source-provider', 'fixture', '--dry-run', '--hermes-cli', sys.executable]), 0)
            secret.assert_not_called()


if __name__ == '__main__':
    unittest.main()
