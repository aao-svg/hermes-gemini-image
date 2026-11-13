"""Hermes image backend for explicitly configured Gemini Messages proxies."""
from __future__ import annotations

import base64
import importlib.util
import io
import json
import os
import re
import stat
import warnings
from pathlib import Path
from typing import Any

import httpx
from PIL import Image
from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO, ImageGenProvider, resolve_aspect_ratio, error_response,
    save_b64_image, success_response,
)
from agent.file_safety import get_read_block_error
from agent.secret_scope import get_secret_str
from hermes_cli.config import load_config


def resolve_settings(config):
    """Load the sibling even when Hermes imports this file without a package."""
    spec = importlib.util.spec_from_file_location("_gemini_image_settings", Path(__file__).with_name("settings.py"))
    if spec is None or spec.loader is None:
        raise ValueError("Settings module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.resolve_settings(config)


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_REFERENCE_BYTES = 24 * 1024 * 1024


def _validate_image(data, mime):
    if not isinstance(data, str) or len(data) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ValueError("Invalid image size")
    raw = base64.b64decode(data, validate=True)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Invalid image size")
    formats = {"PNG": ("image/png", "png"), "JPEG": ("image/jpeg", "jpg"), "WEBP": ("image/webp", "webp")}
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            detected = formats.get(image.format or "")
            if detected is None or (mime is not None and detected[0] != mime):
                raise ValueError("Invalid image format")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("Too many image pixels")
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            for frame in range(getattr(image, "n_frames", 1)):
                image.seek(frame)
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Too many image pixels")
                image.load()
    return raw, detected[1]


_DATA_URI = re.compile(r"data:(image/[A-Za-z0-9.+-]+);base64,([^\s)\"'<>`]+)")


def _extract_image(payload):
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, list):
        raise ValueError("Invalid response")
    candidates = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "image":
            source = block.get("source")
            if not isinstance(source, dict) or source.get("type") != "base64":
                raise ValueError("Only inline image sources are supported")
            if source.get("media_type") not in ("image/png", "image/jpeg", "image/webp"):
                raise ValueError("An explicit image MIME type is required")
            candidates.append(_validate_image(source.get("data"), source.get("media_type")))
        elif block.get("type") == "text" and isinstance(block.get("text"), str):
            for match in _DATA_URI.finditer(block["text"]):
                candidates.append(_validate_image(match.group(2), match.group(1)))
    if not candidates:
        raise ValueError("No inline images")
    return candidates[0]


def _reference_image(value):
    mime = None
    if isinstance(value, str) and value.startswith("data:"):
        match = _DATA_URI.fullmatch(value)
        if match is None:
            raise ValueError("Invalid image URI")
        mime, encoded = match.groups()
    else:
        if not isinstance(value, str) or get_read_block_error(value):
            raise ValueError("Reference access denied")
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("Only absolute local paths are supported")
        path = path.resolve(strict=True)
        if get_read_block_error(str(path)):
            raise ValueError("Reference access denied")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES:
            raise ValueError("Only bounded regular image files are supported")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES:
                raise ValueError("Only bounded regular image files are supported")
            raw = stream.read(MAX_IMAGE_BYTES + 1)
        encoded = base64.b64encode(raw).decode("ascii")
    raw, extension = _validate_image(encoded, mime)
    mime = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[extension]
    return {"type": "image", "source": {"type": "base64", "media_type": mime,
            "data": base64.b64encode(raw).decode("ascii")}}, len(raw)


def _read_response(response):
    data = bytearray()
    for chunk in response.iter_bytes():
        if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
            raise ValueError("Response too large")
        data.extend(chunk)
    return json.loads(data)


class GeminiImageProvider(ImageGenProvider):
    @property
    def name(self):
        return "gemini-messages"

    @property
    def display_name(self):
        return "Gemini (Messages proxy)"

    def __init__(self, *, client_factory=None):
        self._client_factory = client_factory or httpx.Client

    def default_model(self):
        return "gemini-3.1-flash-image"

    def list_models(self):
        return [{"id": self.default_model(), "display": "Gemini 3.1 Flash Image"}]

    def capabilities(self):
        return {"modalities": ["text", "image"], "max_reference_images": 4}

    def get_setup_schema(self):
        try:
            key_env = resolve_settings(load_config())["key_env"]
        except Exception:
            key_env = "GEMINI_IMAGE_API_KEY"
        return {"name": self.display_name, "badge": "paid", "tag": "Messages proxy; reference-image editing is unverified",
                "env_vars": [{"key": key_env, "prompt": "Configured image proxy API key"}]}

    def is_available(self):
        try:
            settings = resolve_settings(load_config())
            return bool(get_secret_str(settings["key_env"]))
        except Exception:
            return False

    def generate(self, prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO, *,
                 image_url=None, reference_image_urls=None, **kwargs: Any):
        model = kwargs.get("model") or self.default_model()
        ratio = resolve_aspect_ratio(aspect_ratio)
        if not isinstance(prompt, str) or not prompt.strip():
            return error_response(error="A non-empty image prompt is required.", error_type="invalid_input",
                                  provider=self.name, model=model, aspect_ratio=ratio)
        prompt = prompt.strip()
        try:
            settings = resolve_settings(load_config())
            model = kwargs.get("model") or settings.get("model") or self.default_model()
            key = get_secret_str(settings["key_env"])
            if not key:
                raise ValueError("Missing image proxy key")
        except Exception:
            return error_response(error="Image proxy settings or credentials are missing or invalid.",
                                  error_type="configuration_error", provider=self.name, model=model,
                                  prompt=prompt, aspect_ratio=ratio)
        hint = {"landscape": "16:9", "portrait": "9:16", "square": "1:1"}[ratio]
        def failure(message, kind):
            return error_response(error=message, error_type=kind, provider=self.name,
                                  model=model, prompt=prompt, aspect_ratio=ratio)

        try:
            if reference_image_urls is None:
                refs = []
            elif isinstance(reference_image_urls, str):
                refs = [reference_image_urls]
            elif isinstance(reference_image_urls, (list, tuple)):
                refs = list(reference_image_urls)
            else:
                raise ValueError("Invalid reference list")
            sources = ([image_url] if image_url is not None else []) + refs
            if len(sources) > 4:
                raise ValueError("Too many references")
            content = [{"type": "text", "text": f"Generate an image. Aspect ratio hint: {hint}.\n{prompt}"}]
            total = 0
            for source in sources:
                block, size = _reference_image(source)
                total += size
                if total > MAX_REFERENCE_BYTES:
                    raise ValueError("References too large")
                content.append(block)
        except Exception:
            return failure("Invalid reference images. Use up to four local absolute paths or PNG/JPEG/WebP data URIs, at most 10 MiB and 40 MP each, 24 MiB total.", "invalid_input")
        thinking = settings.get("thinking", "high")
        body = {"model": model, "max_tokens": 4096, "stream": False,
                "messages": [{"role": "user", "content": content}]}
        if thinking == "high":
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": "high"}
            body["max_tokens"] = 16384

        try:
            with self._client_factory(timeout=180, follow_redirects=False) as client:
                with client.stream("POST", settings["base_url"] + "/messages", headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                }, json=body) as response:
                    if not 200 <= response.status_code < 300:
                        return failure(f"The image proxy returned HTTP {response.status_code}. No automatic retry was attempted.", "http_error")
                    payload = _read_response(response)
            raw, extension = _extract_image(payload)
        except httpx.TimeoutException:
            return failure("Image generation timed out and may have consumed credits. No automatic retry was attempted; check the proxy before resubmitting.", "timeout")
        except httpx.RequestError:
            return failure("The image proxy connection failed and may have consumed credits. No automatic retry was attempted; check the proxy before resubmitting.", "network_error")
        except Exception:
            return failure("The proxy returned invalid image data.", "invalid_response")
        try:
            path = save_b64_image(base64.b64encode(raw).decode("ascii"), prefix=self.name, extension=extension)
        except Exception:
            return failure("The generated image could not be saved locally.", "storage_error")
        return success_response(image=str(path), model=model, prompt=prompt, aspect_ratio=ratio,
                                provider=self.name, modality="image" if sources else "text",
                                extra={"thinking_requested": thinking,
                                       "thinking_effective_verified": False})


def register(ctx):
    """Register only the image backend; never change config or make requests."""
    ctx.register_image_gen_provider(GeminiImageProvider())
