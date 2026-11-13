"""Offline contract tests against the real Hermes runtime and Pillow."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def picture(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (3, 2), "red").save(stream, format=fmt)
    return stream.getvalue()


def image_block(raw=None, mime="image/png"):
    return {"type": "image", "source": {"type": "base64", "media_type": mime,
            "data": base64.b64encode(picture() if raw is None else raw).decode()}}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue((ROOT / "__init__.py").is_file(), "Gemini provider implementation is missing")
        spec = importlib.util.spec_from_file_location("gemini_image_test_plugin", ROOT / "__init__.py")
        assert spec is not None and spec.loader is not None
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.real_resolve_settings = self.module.resolve_settings
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.requests = []
        self.client_options = []
        self.settings = {"base_url": "http://127.0.0.1:8765/v1", "key_env": "OFFLINE_KEY", "model": "gemini-3.1-flash-image"}
        for name, value in (("load_config", {}), ("resolve_settings", self.settings), ("get_secret_str", "offline-test-key")):
            p = patch.object(self.module, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        p.start()
        self.addCleanup(p.stop)

    def provider(self, response=None, handler=None):
        def transport(request):
            self.requests.append(request)
            return handler(request) if handler else httpx.Response(200, json=response or {"content": [image_block()]})
        def factory(**kwargs):
            self.client_options.append(kwargs)
            return httpx.Client(transport=httpx.MockTransport(transport), **kwargs)
        return self.module.GeminiImageProvider(client_factory=factory)

    def outputs(self):
        return list(self.home.glob("cache/generated/images/*"))

    def test_generates_and_saves_anthropic_image_with_one_messages_post(self):
        result = self.provider().generate("A red bird", "square", unused_control="ignored")
        self.assertTrue(result["success"], result)
        output = Path(result["image"])
        self.assertEqual(output.read_bytes(), picture())
        self.assertEqual(output.suffix, ".png")
        self.assertIn(self.home, output.parents)
        self.assertEqual(result["provider"], "gemini-messages")
        self.assertEqual(result["modality"], "text")
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(str(request.url), "http://127.0.0.1:8765/v1/messages")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["x-api-key"], "offline-test-key")
        self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
        body = json.loads(request.content)
        self.assertEqual(body["model"], "gemini-3.1-flash-image")
        self.assertEqual(body["max_tokens"], 4096)
        self.assertIs(body["stream"], False)
        content = body["messages"][0]["content"]
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertIn("Generate", content[0]["text"])
        self.assertIn("A red bird", content[0]["text"])
        self.assertIn("1:1", content[0]["text"])
        self.assertFalse(self.client_options[0]["follow_redirects"])
        self.assertEqual(self.client_options[0]["timeout"], 180)

    def test_rejects_invalid_image_bytes_without_saving_or_echoing_upstream(self):
        invalid = [
            image_block(b"upstream-private-text"),
            image_block(mime="image/jpeg"),
            image_block(picture("GIF"), "image/gif"),
            image_block(picture()[:24]),
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "!!!!private"}},
        ]
        for block in invalid:
            with self.subTest(block=block["source"]["media_type"]):
                result = self.provider({"content": [block]}).generate("bird")
                self.assertFalse(result["success"])
                self.assertEqual(result["error_type"], "invalid_response")
                self.assertNotIn("private", result["error"])
                self.assertEqual(self.outputs(), [])

    def test_parses_embedded_data_uris_and_uses_detected_file_extension(self):
        for fmt, mime, suffix in (("JPEG", "image/jpeg", ".jpg"), ("WEBP", "image/webp", ".webp")):
            raw = picture(fmt)
            uri = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
            for text in (uri, f"Here is the image: ![bird]({uri})"):
                with self.subTest(fmt=fmt, markdown=text.startswith("Here")):
                    result = self.provider({"content": [{"type": "text", "text": text}]}).generate("bird")
                    self.assertTrue(result["success"], result)
                    self.assertEqual(Path(result["image"]).suffix, suffix)
                    self.assertEqual(Path(result["image"]).read_bytes(), raw)

    def test_refuses_remote_and_text_only_output_without_downloads_or_leaks(self):
        cases = [{"content": [{"type": "text", "text": "private-upstream ![](https://example.com/output.png)"}]},
                 {"content": [{"type": "image", "source": {"type": "url", "url": "https://example.com/output.png"}}]},
                 {"error": "private-upstream"}, {"content": []}, {"content": "private-upstream"}]
        for payload in cases:
            with self.subTest(payload=payload):
                before = len(self.requests)
                result = self.provider(payload).generate("bird")
                self.assertFalse(result["success"])
                self.assertEqual(result["error_type"], "invalid_response")
                self.assertNotIn("private-upstream", result["error"])
                self.assertEqual(len(self.requests), before + 1)
                self.assertEqual(self.outputs(), [])

    def test_bounds_image_decoding_bytes_and_pixels_before_saving(self):
        for constant, limit in (("MAX_IMAGE_BYTES", len(picture()) - 1), ("MAX_IMAGE_PIXELS", 5)):
            with self.subTest(limit=constant), patch.object(self.module, constant, limit, create=True):
                result = self.provider().generate("bird")
                self.assertFalse(result["success"])
                self.assertEqual(result["error_type"], "invalid_response")
                self.assertEqual(self.outputs(), [])

    def test_bounds_streamed_decoded_response_and_stops_reading(self):
        payload = json.dumps({"padding": "x" * 4096, "content": [image_block()]}).encode()
        chunks_read = []
        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                for offset in range(0, len(payload), 64):
                    chunks_read.append(offset)
                    yield payload[offset:offset + 64]
        with patch.object(self.module, "MAX_RESPONSE_BYTES", 128, create=True):
            result = self.provider(handler=lambda request: httpx.Response(200, stream=Stream())).generate("bird")
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "invalid_response")
        self.assertLessEqual(len(chunks_read), 3)
        self.assertEqual(self.outputs(), [])

    def test_http_errors_are_fixed_sanitized_failures_without_redirect_or_retry(self):
        for status in (302, 401, 403, 429, 500):
            with self.subTest(status=status):
                before = len(self.requests)
                result = self.provider(handler=lambda request: httpx.Response(status, json={"content": [image_block()], "error": "offline-test-key-private"}, headers={"location": "https://example.com/leak"})).generate("bird")
                self.assertFalse(result["success"])
                self.assertEqual(result["error_type"], "http_error")
                self.assertEqual(result["error"], f"The image proxy returned HTTP {status}. No automatic retry was attempted.")
                self.assertEqual(len(self.requests), before + 1)
                self.assertEqual(self.outputs(), [])

    def test_transport_failure_warns_about_credits_and_never_retries_or_leaks(self):
        for exception, kind in ((httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "network_error")):
            with self.subTest(exception=exception):
                before = len(self.requests)
                def handler(request):
                    raise exception("offline-test-key-private", request=request)
                result = self.provider(handler=handler).generate("bird")
                self.assertFalse(result["success"])
                self.assertEqual(result["error_type"], kind)
                self.assertIn("credits", result["error"])
                self.assertIn("No automatic retry", result["error"])
                self.assertNotIn("private", result["error"])
                self.assertEqual(len(self.requests), before + 1)
                self.assertEqual(self.outputs(), [])

    def test_non_json_response_is_sanitized(self):
        result = self.provider(handler=lambda request: httpx.Response(200, content=b"private-upstream")).generate("bird")
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "invalid_response")
        self.assertNotIn("private-upstream", result["error"])
        self.assertEqual(self.outputs(), [])

    def test_sends_local_and_data_uri_reference_images_inline(self):
        local = self.home / "reference.not-png"
        local.write_bytes(picture("JPEG"))
        alias = self.home / "reference-alias"
        alias.symlink_to(local)
        uri = "data:image/png;base64," + base64.b64encode(picture()).decode()
        self.assertTrue(hasattr(self.module, "get_read_block_error"), "Reference file safety is missing")
        with patch.object(self.module, "get_read_block_error", wraps=self.module.get_read_block_error) as guard:
            result = self.provider().generate("Restyle the bird", "portrait", image_url=str(alias), reference_image_urls=[uri])
        self.assertTrue(result["success"], result)
        self.assertEqual(result["modality"], "image")
        content = json.loads(self.requests[0].content)["messages"][0]["content"]
        self.assertEqual(len(content), 3)
        self.assertIn("9:16", content[0]["text"])
        self.assertEqual(content[1]["source"]["media_type"], "image/jpeg")
        self.assertEqual(base64.b64decode(content[1]["source"]["data"]), picture("JPEG"))
        self.assertEqual(content[2], image_block())
        checked = [call.args[0] for call in guard.call_args_list]
        self.assertIn(str(alias), checked)
        self.assertIn(str(local.resolve()), checked)

    def test_invalid_references_never_fall_back_to_text_or_send_a_request(self):
        valid = "data:image/png;base64," + base64.b64encode(picture()).decode()
        invalid = ["https://example.com/ref.png", "relative.png", "", 123,
                   "data:image/jpeg;base64," + base64.b64encode(picture()).decode(),
                   "data:image/png;base64,!!!!", str(self.home), str(self.home / "missing.png")]
        for reference in invalid:
            with self.subTest(reference=reference):
                result = self.provider().generate("bird", reference_image_urls=[valid, reference])
                self.assertFalse(result["success"])
                self.assertEqual(result["error_type"], "invalid_input")
                self.assertEqual(self.requests, [])
                self.assertEqual(self.outputs(), [])

    def test_reference_limits_reject_count_and_total_bytes(self):
        uri = "data:image/png;base64," + base64.b64encode(picture()).decode()
        result = self.provider().generate("bird", image_url=uri, reference_image_urls=[uri] * 4)
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "invalid_input")
        with patch.object(self.module, "MAX_REFERENCE_BYTES", len(picture()) * 2 - 1, create=True):
            result = self.provider().generate("bird", reference_image_urls=[uri, uri])
        self.assertFalse(result["success"])
        self.assertEqual(self.requests, [])
        self.assertEqual(self.outputs(), [])

    def test_reference_guard_denies_original_and_resolved_paths_without_reading(self):
        target = self.home / "reference.png"
        target.write_bytes(picture())
        alias = self.home / "alias.png"
        alias.symlink_to(target)
        for denied in (str(alias), str(target)):
            with self.subTest(denied=denied), patch.object(self.module, "get_read_block_error", side_effect=lambda value: "private-policy-denial" if value == denied else None), patch.object(Path, "open", side_effect=AssertionError("Must not read blocked files")):
                result = self.provider().generate("bird", image_url=str(alias))
                self.assertFalse(result["success"])
                self.assertEqual(result["error_type"], "invalid_input")
                self.assertNotIn("private", result["error"])
                self.assertEqual(self.requests, [])

    def test_nonregular_reference_is_rejected_before_opening(self):
        original_open = Path.open
        def open_fixture(path, *args, **kwargs):
            return io.BytesIO(picture()) if path == self.home else original_open(path, *args, **kwargs)
        with patch.object(Path, "open", autospec=True, side_effect=open_fixture) as opened:
            result = self.provider().generate("bird", image_url=str(self.home))
        self.assertFalse(result["success"], "A directory must not be treated as an image")
        self.assertEqual(result["error_type"], "invalid_input")
        opened.assert_not_called()
        self.assertEqual(self.requests, [])

    def test_registers_only_image_provider_with_honest_capabilities_and_setup(self):
        registered = []
        class Context:
            def register_image_gen_provider(self, provider):
                registered.append(provider)
        self.assertTrue(hasattr(self.module, "register"), "Plugin registration is missing")
        self.module.register(Context())
        self.assertEqual(len(registered), 1)
        provider = registered[0]
        from agent.image_gen_provider import ImageGenProvider
        self.assertIsInstance(provider, ImageGenProvider)
        self.assertEqual(provider.name, "gemini-messages")
        self.assertEqual(provider.display_name, "Gemini (Messages proxy)")
        self.assertEqual(provider.default_model(), "gemini-3.1-flash-image")
        self.assertEqual(provider.capabilities(), {"modalities": ["text", "image"], "max_reference_images": 4})
        self.assertEqual(provider.list_models()[0]["id"], provider.default_model())
        self.assertEqual(provider.get_setup_schema()["env_vars"][0]["key"], "OFFLINE_KEY")
        self.assertTrue(provider.is_available())
        with patch.object(self.module, "get_secret_str", return_value=""):
            self.assertFalse(provider.is_available())

    def test_bad_configuration_and_missing_credentials_are_sanitized_without_network(self):
        with patch.object(self.module, "resolve_settings", side_effect=ValueError("private-endpoint-key")):
            provider = self.provider()
            result = provider.generate("bird")
            self.assertFalse(result["success"])
            self.assertEqual(result["error_type"], "configuration_error")
            self.assertNotIn("private", result["error"])
            self.assertFalse(provider.is_available())
        with patch.object(self.module, "get_secret_str", return_value=""):
            result = self.provider().generate("bird")
            self.assertFalse(result["success"])
            self.assertEqual(result["error_type"], "configuration_error")
        self.assertEqual(self.requests, [])

    def test_rejects_empty_or_non_string_prompt_before_network(self):
        for prompt in ("", "  ", None, 12):
            with self.subTest(prompt=prompt):
                result = self.provider().generate(prompt)
                self.assertFalse(result["success"])
                self.assertEqual(result["error_type"], "invalid_input")
        self.assertEqual(self.requests, [])

    def test_model_override_and_config_default_ignore_unknown_controls(self):
        self.settings["model"] = "configured-image-model"
        provider = self.provider()
        self.assertTrue(provider.generate("bird", resolution="8K")["success"])
        self.assertTrue(provider.generate("bird", model="override-image-model")["success"])
        self.assertEqual([json.loads(request.content)["model"] for request in self.requests], ["configured-image-model", "override-image-model"])
        self.assertNotIn("resolution", json.loads(self.requests[0].content))
        self.assertNotIn("creative_controls", provider.capabilities())

    def test_output_requires_declared_mime_and_does_not_salvage_malformed_data_uri(self):
        missing_mime = image_block()
        del missing_mime["source"]["media_type"]
        uri = "data:image/png;base64," + base64.b64encode(picture()).decode() + "!!!!"
        for payload in ({"content": [missing_mime]}, {"content": [{"type": "text", "text": uri}]}):
            with self.subTest(payload=payload):
                result = self.provider(payload).generate("bird")
                self.assertFalse(result["success"])
                self.assertEqual(result["error_type"], "invalid_response")
                self.assertEqual(self.outputs(), [])

    def test_save_failure_is_sanitized(self):
        with patch.object(self.module, "save_b64_image", side_effect=OSError("private-filesystem-secret")):
            result = self.provider().generate("bird")
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "storage_error")
        self.assertNotIn("private", result["error"])

    def test_real_sibling_settings_preserve_prefix_and_scoped_provider_credential_reference(self):
        config = {"image_gen": {"gemini_messages": {"source_provider": "offline-messages"}, "model": "gemini-3.1-flash-image"},
                  "providers": {"offline-messages": {"api_mode": "anthropic_messages", "base_url": "http://127.0.0.1:8765/proxy/v1", "key_env": "OFFLINE_SCOPED_KEY"}}}
        with patch.object(self.module, "resolve_settings", self.real_resolve_settings), patch.object(self.module, "load_config", return_value=config), patch.object(self.module, "get_secret_str", return_value="offline-key") as secret:
            result = self.provider().generate("bird")
        self.assertTrue(result["success"], result)
        secret.assert_called_once_with("OFFLINE_SCOPED_KEY")
        self.assertEqual(str(self.requests[0].url), "http://127.0.0.1:8765/proxy/v1/messages")

    def test_real_sibling_settings_work_with_independent_image_key(self):
        config = {"image_gen": {"gemini_messages": {"base_url": "http://127.0.0.1:8765/v1"}}}
        with patch.object(self.module, "resolve_settings", self.real_resolve_settings), patch.object(self.module, "load_config", return_value=config), patch.object(self.module, "get_secret_str", return_value="offline-key") as secret:
            result = self.provider().generate("bird")
        self.assertTrue(result["success"], result)
        secret.assert_called_once_with("GEMINI_IMAGE_API_KEY")

    def test_response_budget_counts_decompressed_bytes_not_wire_bytes(self):
        import gzip
        raw = json.dumps({"padding": "x" * 4096, "content": [image_block()]}).encode()
        compressed = gzip.compress(raw)
        with patch.object(self.module, "MAX_RESPONSE_BYTES", 1024):
            result = self.provider(handler=lambda request: httpx.Response(200, content=compressed, headers={"content-encoding": "gzip"})).generate("bird")
        self.assertLess(len(compressed), 1024)
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "invalid_response")
        self.assertEqual(self.outputs(), [])

    def test_a_corrupt_second_inline_image_prevents_any_output_file(self):
        result = self.provider({"content": [image_block(), image_block(b"private-corrupt")]}).generate("bird")
        self.assertFalse(result["success"])
        self.assertEqual(self.outputs(), [])

    def test_all_frames_of_animated_webp_are_fully_loaded(self):
        stream = io.BytesIO()
        first = Image.new("RGB", (3, 2), "red")
        second = Image.new("RGB", (3, 2), "blue")
        first.save(stream, format="WEBP", save_all=True, append_images=[second], duration=50, loop=0)
        raw = stream.getvalue()
        with Image.open(io.BytesIO(raw)) as sample:
            image_class = type(sample)
        loaded = set()
        original_load = image_class.load
        def record_load(image, *args, **kwargs):
            loaded.add(image.tell())
            return original_load(image, *args, **kwargs)
        with patch.object(image_class, "load", record_load):
            result = self.provider({"content": [image_block(raw, "image/webp")]}).generate("bird")
        self.assertTrue(result["success"], result)
        self.assertEqual(loaded, {0, 1})


if __name__ == "__main__":
    unittest.main()
