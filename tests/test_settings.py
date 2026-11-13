"""Offline settings contract; no network or actual deployment configuration."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SettingsTests(unittest.TestCase):
    def test_explicit_named_provider_reuses_key_reference_without_copying_secret(self):
        self.assertTrue((ROOT / 'settings.py').exists(), 'settings resolver is not implemented')
        spec = importlib.util.spec_from_file_location('gemini_settings_test', ROOT / 'settings.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = {'providers': {'fixture': {'base_url': 'http://127.0.0.1:8080/v1',
                   'api_mode': 'anthropic_messages', 'key_env': 'FIXTURE_KEY'}},
                  'image_gen': {'model': 'gemini-3.1-flash-image',
                                'gemini_messages': {'source_provider': 'fixture'}}}
        self.assertEqual(module.resolve_settings(config), {
            'base_url': 'http://127.0.0.1:8080/v1', 'key_env': 'FIXTURE_KEY',
            'model': 'gemini-3.1-flash-image', 'thinking': 'high'})


    def test_plugin_thinking_defaults_high_and_accepts_unspecified_only(self):
        spec = importlib.util.spec_from_file_location('gemini_settings_thinking', ROOT / 'settings.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cfg: dict = {'image_gen': {'gemini_messages': {'base_url': 'http://127.0.0.1/v1'}}}
        self.assertEqual(module.resolve_settings(cfg).get('thinking'), 'high')
        for value in ('high', 'unspecified'):
            cfg['plugins'] = {'entries': {'gemini-image': {'settings': {'thinking': value}}}}
            self.assertEqual(module.resolve_settings(cfg)['thinking'], value)
        for value in ('fast', '', None, False, [], 'HIGH'):
            cfg['plugins'] = {'entries': {'gemini-image': {'settings': {'thinking': value}}}}
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.resolve_settings(cfg)

    def test_literal_inline_key_is_never_reused(self):
        spec = importlib.util.spec_from_file_location('gemini_settings_inline', ROOT / 'settings.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cfg = {'providers': {'fixture': {'base_url': 'http://127.0.0.1/v1',
               'api_mode': 'anthropic_messages', 'api_key': 'offline-sentinel'}},
               'image_gen': {'gemini_messages': {'source_provider': 'fixture'}}}
        with self.assertRaises(ValueError):
            module.resolve_settings(cfg)

    def test_malformed_section_is_sanitized_as_configuration_error(self):
        spec = importlib.util.spec_from_file_location('gemini_settings_bad', ROOT / 'settings.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for config in ({'image_gen': 'bad'}, {'image_gen': {'gemini_messages': 'bad'}},
                       {'image_gen': {'gemini_messages': {'source_provider': []}}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                module.resolve_settings(config)

    def test_rejects_noncanonical_tls_values(self):
        spec = importlib.util.spec_from_file_location('gemini_settings_tls', ROOT / 'settings.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for value in (False, 'false', 'no', 'off', '0', 0, 'true'):
            cfg = {'providers': {'fixture': {'base_url': 'http://127.0.0.1/v1',
                   'api_mode': 'anthropic_messages', 'key_env': 'FIXTURE_KEY', 'ssl_verify': value}},
                   'image_gen': {'gemini_messages': {'source_provider': 'fixture'}}}
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.resolve_settings(cfg)

    def test_key_env_is_required_even_for_unexpanded_api_key_reference(self):
        spec = importlib.util.spec_from_file_location('gemini_settings_ref', ROOT / 'settings.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cfg = {'providers': {'fixture': {'base_url': 'http://127.0.0.1/v1',
               'api_mode': 'anthropic_messages', 'api_key': '${FIXTURE_KEY}'}},
               'image_gen': {'gemini_messages': {'source_provider': 'fixture'}}}
        with self.assertRaises(ValueError):
            module.resolve_settings(cfg)

    def test_rejects_unexpanded_url_variables(self):
        spec = importlib.util.spec_from_file_location('gemini_settings_placeholder', ROOT / 'settings.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaises(ValueError):
            module.validate_base_url('https://${PROXY_HOST}/v1')

    def test_url_validation_and_explicit_prefix(self):
        spec = importlib.util.spec_from_file_location('gemini_settings_url', ROOT / 'settings.py')
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.validate_base_url('http://127.0.0.1:8080/proxy/v1'), 'http://127.0.0.1:8080/proxy/v1')
        for value in ('https://example.com/v1', 'http://192.0.2.1/v1', 'https://localhost/v1/',
                      'https://user:pass@localhost/v1', 'https://localhost/v1?',
                      'https://localhost/v1#', 'https://localhost/a/../v1',
                      'https://localhost/%2e/v1', 'https://localhost:0/v1', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.validate_base_url(value)


if __name__ == '__main__':
    unittest.main()
