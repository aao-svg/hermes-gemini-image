"""Resolve explicit image-only settings without reading or copying secrets."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

DEFAULT_MODEL = 'gemini-3.1-flash-image'
PROVIDER = 'gemini-messages'
PLUGIN = 'gemini-image'
KEY_ENV = 'GEMINI_IMAGE_API_KEY'


def validate_base_url(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError('Provide an explicit API root ending in /v1.')
    try:
        u = urlsplit(value)
        host = (u.hostname or '').lower().rstrip('.')
        port = u.port
    except ValueError:
        raise ValueError('Invalid API root.') from None
    if (u.scheme not in ('https', 'http') or not host or u.username is not None
            or u.password is not None or '?' in value or '#' in value
            or not u.path.endswith('/v1') or any(x in value for x in ('\\', '%', '<', '>', '{', '}', '$'))
            or any(p in ('.', '..', '') for p in u.path.split('/')[1:])
            or port == 0):
        raise ValueError('Use an origin with an explicit path ending /v1, without credentials, query or fragment.')
    if host in ('example.com', 'example.net', 'example.org', 'example', 'test', 'invalid') or any(host.endswith('.' + d) for d in ('example.com', 'example.net', 'example.org', 'example', 'test', 'invalid')):
        raise ValueError('Documentation placeholders cannot be used as service addresses.')
    if u.scheme == 'http':
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == 'localhost'
        if not loopback:
            raise ValueError('HTTP is allowed only for loopback; otherwise use HTTPS.')
    return value


def resolve_settings(config: dict) -> dict[str, str]:
    section = config.get('image_gen') or {}
    if not isinstance(section, dict):
        raise ValueError('Image configuration must be a mapping.')
    settings = section.get('gemini_messages') or {}
    if not isinstance(settings, dict):
        raise ValueError('Gemini settings must be a mapping.')
    source = settings.get('source_provider')
    if source is not None and (not isinstance(source, str) or not source.strip()):
        raise ValueError('Source provider must be an explicit nonempty name.')
    if source:
        if settings.get('base_url'):
            raise ValueError('Choose source_provider OR a separate base_url, not both.')
        providers = config.get('providers') or {}
        entry = providers.get(source) if isinstance(providers, dict) else None
        if entry is None:
            entries = [p for p in config.get('custom_providers', []) if isinstance(p, dict) and p.get('name') == source]
            entry = entries[0] if len(entries) == 1 else None
        if not isinstance(entry, dict) or entry.get('api_mode') != 'anthropic_messages':
            raise ValueError('The explicit source provider must exist and use anthropic_messages.')
        if entry.get('extra_headers') or entry.get('extra_body') or (entry.get('ssl_verify') is not None and entry.get('ssl_verify') is not True) or entry.get('ssl_ca_cert'):
            raise ValueError('Custom headers/body/TLS settings are unsupported; use a separate base_url and key.')
        key_env = entry.get('key_env') or entry.get('api_key_env')
        base_url = entry.get('base_url')
    else:
        base_url = settings.get('base_url')
        key_env = KEY_ENV
    if not isinstance(key_env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key_env):
        raise ValueError('A scoped credential variable reference is required; inline secrets are unsupported.')
    model = section.get('model') or DEFAULT_MODEL
    if not isinstance(model, str) or not re.fullmatch(r'gemini-[A-Za-z0-9._-]*image[A-Za-z0-9._-]*', model):
        raise ValueError('Choose a Gemini image model explicitly.')
    plugins = config.get('plugins', {})
    if not isinstance(plugins, dict):
        raise ValueError('Plugin configuration must be a mapping.')
    entries = plugins.get('entries', {})
    if not isinstance(entries, dict):
        raise ValueError('Plugin entries must be a mapping.')
    entry = entries.get(PLUGIN, {})
    if not isinstance(entry, dict) or not isinstance(entry.get('settings', {}), dict):
        raise ValueError('Gemini plugin settings must be a mapping.')
    thinking = entry.get('settings', {}).get('thinking', 'high')
    if not isinstance(thinking, str) or thinking not in ('high', 'unspecified'):
        raise ValueError('Plugin thinking must be high or unspecified.')
    return {'base_url': validate_base_url(base_url), 'key_env': key_env, 'model': model,
            'thinking': thinking}
