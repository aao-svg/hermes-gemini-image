"""Image-only configuration via Hermes CLI; no network or generation."""
from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def load_sibling(name):
    spec = importlib.util.spec_from_file_location('gemini_image_' + name, Path(__file__).with_name(name + '.py'))
    if spec is None or spec.loader is None:
        raise ImportError('Plugin files unavailable.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


settings = load_sibling('settings')


def build_commands(cli, target):
    return [
        [cli, 'plugins', 'enable', settings.PLUGIN, '--no-allow-tool-override'],
        [cli, 'config', 'set', 'image_gen.gemini_messages', json.dumps(target)],
        [cli, 'config', 'set', 'image_gen.model', settings.DEFAULT_MODEL],
        [cli, 'config', 'set', 'image_gen.provider', settings.PROVIDER],
        [cli, 'tools', 'enable', 'image_gen'],
    ]


def preflight():
    from hermes_cli.plugins import PluginContext
    if not callable(getattr(PluginContext, 'register_image_gen_provider', None)):
        raise ImportError('Image provider registration interface missing.')
    return load_sibling('__init__')


def check_configuration():
    from hermes_cli.config import load_config, reload_env
    from hermes_cli.plugins import discover_plugins
    from agent import image_gen_registry
    from agent.secret_scope import get_secret_str
    reload_env()
    config = load_config()
    discover_plugins()
    provider = image_gen_registry.get_active_provider()
    valid, key_present = False, False
    thinking = None
    try:
        resolved = settings.resolve_settings(config)
        valid = True
        thinking = resolved['thinking']
        key_present = bool(get_secret_str(resolved['key_env']))
    except (ValueError, RuntimeError, TypeError):
        pass
    section = config.get('image_gen') or {}
    if not isinstance(section, dict):
        section = {}
    selected = provider is not None and provider.name == settings.PROVIDER
    available = bool(provider is not None and selected and provider.is_available())
    result = {'provider': section.get('provider'), 'model': section.get('model'),
              'settings_valid': valid, 'api_key_configured': key_present,
              'thinking': thinking,
              'registered_and_available': available,
              'ready': bool(valid and key_present and available),
              'network_or_generation_tested': False}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ready'] else 2


def store_key():
    if not sys.stdin.isatty():
        raise ValueError('Use your trusted interactive terminal or Hermes credential settings; never send a key in chat.')
    from hermes_cli.config import save_env_value_secure
    secret = getpass.getpass('GEMINI_IMAGE_API_KEY (hidden): ')
    if not secret.strip():
        raise ValueError('No credential entered; existing credential unchanged.')
    try:
        result = save_env_value_secure(settings.KEY_ENV, secret)
    except Exception:
        raise ValueError('Credential storage failed; details suppressed.') from None
    if not result.get('success'):
        raise ValueError('Hermes did not confirm credential storage.')
    print('Credential saved privately through Hermes.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Configure Gemini Messages image generation only; no generation or restart.')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--source-provider', help='Explicit existing anthropic_messages provider id; reuse its scoped key reference')
    group.add_argument('--base-url', help='Explicit HTTPS API root ending /v1, optional path prefix')
    parser.add_argument('--thinking', choices=('high', 'unspecified'), help='Save plugin-wide thinking default only; no per-call override')
    parser.add_argument('--hermes-cli', help='Path to the target Hermes CLI')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--set-key', action='store_true', help='Human-only hidden terminal input for standalone mode')
    args = parser.parse_args(argv)
    try:
        if args.check:
            if args.source_provider or args.base_url or args.dry_run or args.set_key or args.thinking:
                raise ValueError('--check cannot be combined with configuration options.')
            return check_configuration()
        if args.thinking:
            if args.source_provider or args.base_url or args.set_key:
                raise ValueError('--thinking changes only plugin settings; do not combine with source or credential options.')
            cli = args.hermes_cli or shutil.which('hermes')
            if not cli or not Path(cli).is_file() or not os.access(cli, os.X_OK):
                raise ValueError('Run in the target Hermes environment or pass --hermes-cli.')
            commands = [[cli, 'config', 'set', 'plugins.entries.gemini-image.settings.thinking', args.thinking]]
            if args.dry_run:
                print(json.dumps({'dry_run': True, 'commands': commands, 'secret_read': False}, indent=2))
                return 0
            preflight()
            subprocess.run(commands[0], check=True)
            print('Plugin thinking saved. No other settings, restart or generation changed.')
            return 0
        if args.set_key and args.source_provider:
            raise ValueError('Source provider mode reuses its existing key; do not copy it.')
        if args.set_key and not args.dry_run and not sys.stdin.isatty():
            raise ValueError('--set-key requires a human-operated interactive terminal.')
        if args.set_key and not args.source_provider and not args.base_url:
            if args.dry_run:
                print(json.dumps({'dry_run': True, 'secret_read': False, 'operation': 'save standalone credential interactively'}))
            else:
                store_key()
            return 0
        if not args.source_provider and not args.base_url:
            raise ValueError('Choose --source-provider or --base-url explicitly; there is no default service address.')
        from hermes_cli.config import load_config
        from agent.secret_scope import get_secret_str
        target = {'source_provider': args.source_provider} if args.source_provider else {'base_url': settings.validate_base_url(args.base_url)}
        config = load_config()
        candidate = dict(config)
        candidate['image_gen'] = dict(config.get('image_gen') or {}, gemini_messages=target, model=settings.DEFAULT_MODEL)
        resolved = settings.resolve_settings(candidate)
        cli = args.hermes_cli or shutil.which('hermes')
        if not cli or not Path(cli).is_file() or not os.access(cli, os.X_OK):
            raise ValueError('Run in the target Hermes environment or pass --hermes-cli.')
        commands = build_commands(cli, target)
        if args.dry_run:
            print(json.dumps({'dry_run': True, 'commands': commands, 'secret_read': False}, indent=2))
            return 0
        from hermes_cli.config import reload_env
        reload_env()
        if args.source_provider and not get_secret_str(resolved['key_env']):
            raise ValueError('The selected source provider has no scoped key; configure it privately before proceeding.')
        preflight()
        for command in commands:
            subprocess.run(command, check=True)
        if args.set_key:
            store_key()
        print('Image-only configuration saved. Run --check. No restart or generation performed.')
        return 0
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (ImportError, OSError, RuntimeError, TypeError, subprocess.CalledProcessError):
        print('Target Hermes operation failed; details suppressed. Review state before retrying.', file=sys.stderr)
        return 2
    except (EOFError, KeyboardInterrupt):
        print('Cancelled. No generation was submitted.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
