from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import channel
from app import config as config_mod
from app.config import ModelCfg, ProvidersCfg, UpstreamCfg
from app.router import route
from app.session import compute_session_key


class _OneChunkStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes):
        self.content = content

    async def __aiter__(self):
        yield self.content


class RoutingInputTests(unittest.TestCase):
    def test_responses_and_anthropic_multimodal_are_normalized_for_routing(self):
        responses_body = {
            "input": [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "分析图片"},
                    {"type": "input_image", "image_url": "data:image/png;base64,x"},
                ],
            }],
        }
        messages = channel._messages_for_router(channel.EP_RESPONSES, responses_body)
        self.assertEqual(channel._endpoint_user_text(channel.EP_RESPONSES, responses_body), "分析图片")
        self.assertEqual(messages[0]["content"][1]["type"], "image_url")

        anthropic_body = {
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "data": "x"}},
                    {"type": "text", "text": "描述图片"},
                ],
            }],
        }
        normalized = channel._messages_for_router(channel.EP_MESSAGES, anthropic_body)
        self.assertEqual(normalized[0]["content"][0]["type"], "image_url")
        self.assertEqual(channel._endpoint_user_text(channel.EP_MESSAGES, anthropic_body), "描述图片")

    def test_session_key_depends_only_on_authentication(self):
        first = compute_session_key("Bearer same", [{"role": "user", "content": "A"}])
        second = compute_session_key("Bearer same", [{"role": "user", "content": "B"}])
        other = compute_session_key("Bearer other", [{"role": "user", "content": "A"}])
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(compute_session_key("", []), "")

    def test_connection_snapshot_preserves_unsubmitted_password(self):
        incoming = {"server": {}, "providers": {}, "admin": {"user": "admin", "enabled": True}}
        with patch.object(
            channel, "_load_section_raw",
            return_value={"admin": {"user": "admin", "password": "existing"}},
        ):
            prepared = channel._prepare_section_data("connection", incoming)
        self.assertEqual(prepared["admin"]["password"], "existing")
        self.assertNotIn("enabled", prepared["admin"])

    def test_model_snapshot_keeps_upstream(self):
        old = channel._CFG.models.items
        try:
            channel._CFG.models.items = {"model": ModelCfg(upstream="provider-b")}
            snapshot = channel._serialize(channel._CFG)
            self.assertEqual(snapshot["models"]["model"]["upstream"], "provider-b")
        finally:
            channel._CFG.models.items = old

    def test_rule_mode_does_not_apply_ml_confidence_threshold(self):
        text = "x" * 2000
        messages = [{"role": "user", "content": text}]
        decision = route(
            "heuristic_test",
            {"model": "heuristic_test", "messages": messages},
            channel._CFG,
            messages=messages,
        )
        self.assertEqual(decision.source, "heuristic")
        self.assertEqual(decision.rule_idx, 1)


class ProxyResponseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.real_async_client = httpx.AsyncClient
        self.old_providers = channel._CFG.connection.providers
        self.old_models = channel._CFG.models.items
        channel._CFG.connection.providers = ProvidersCfg(
            default="default",
            items={
                "default": UpstreamCfg(
                    name="default", base_url="https://default.test", api_key="",
                ),
                "mock": UpstreamCfg(
                    name="mock", base_url="https://upstream.test/v1", api_key="",
                ),
            },
        )
        channel._CFG.models.items = {
            **self.old_models,
            "not-a-strategy": ModelCfg(upstream="mock"),
            "embedding-model": ModelCfg(upstream="mock"),
        }

    async def asyncTearDown(self):
        channel._CFG.connection.providers = self.old_providers
        channel._CFG.models.items = self.old_models

    async def _request(self, *, stream: bool) -> tuple[httpx.Response, list[httpx.Request]]:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if stream:
                return httpx.Response(
                    401, stream=_OneChunkStream(b"upstream denied"),
                    headers={"content-type": "text/plain", "x-upstream": "stream"},
                )
            return httpx.Response(
                429, stream=_OneChunkStream(b"not-json"),
                headers={"content-type": "text/plain", "x-upstream": "buffered"},
            )

        transport = httpx.MockTransport(handler)

        def upstream_client(*args, **kwargs):
            kwargs["transport"] = transport
            return self.real_async_client(*args, **kwargs)

        app_transport = httpx.ASGITransport(app=channel.app)
        async with self.real_async_client(transport=app_transport, base_url="http://autorouter") as client:
            with patch.object(channel.httpx, "AsyncClient", side_effect=upstream_client):
                response = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer caller"},
                    json={
                        "model": "not-a-strategy",
                        "stream": stream,
                        "messages": [{"role": "user", "content": "hello"}],
                        "metadata": {"keep": [1, 2, 3]},
                    },
                )
        return response, seen

    async def test_non_stream_response_is_passed_through_without_json_parsing(self):
        response, seen = await self._request(stream=False)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.content, b"not-json")
        self.assertEqual(response.headers["x-upstream"], "buffered")
        self.assertEqual(seen[0].headers["authorization"], "Bearer caller")
        self.assertIn(b'"metadata":{"keep":[1,2,3]}', seen[0].content)
        self.assertEqual(str(seen[0].url), "https://upstream.test/v1/chat/completions")

    async def test_stream_error_status_and_body_are_passed_through(self):
        response, seen = await self._request(stream=True)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.content, b"upstream denied")
        self.assertEqual(response.headers["x-upstream"], "stream")
        self.assertEqual(str(seen[0].url), "https://upstream.test/v1/chat/completions")

    async def test_other_v1_endpoints_stream_raw_request_and_response(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                503,
                stream=_OneChunkStream(b"raw-upstream-body"),
                headers={"content-type": "application/octet-stream"},
            )

        transport = httpx.MockTransport(handler)

        def upstream_client(*args, **kwargs):
            kwargs["transport"] = transport
            return self.real_async_client(*args, **kwargs)

        raw_request = b'{"model":"embedding-model","input":[1,2,3]}'
        app_transport = httpx.ASGITransport(app=channel.app)
        async with self.real_async_client(transport=app_transport, base_url="http://autorouter") as client:
            with patch.object(channel.httpx, "AsyncClient", side_effect=upstream_client):
                response = await client.post(
                    "/v1/embeddings",
                    headers={"content-type": "application/json", "x-api-key": "caller-key"},
                    content=raw_request,
                )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b"raw-upstream-body")
        self.assertEqual(await seen[0].aread(), raw_request)
        self.assertEqual(seen[0].headers["x-api-key"], "caller-key")
        self.assertEqual(str(seen[0].url), "https://upstream.test/v1/embeddings")


class ConfigApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_bulk_save_preserves_password_and_validates_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            shutil.copytree("config", config_dir)
            connection = config_mod.load_section("connection", config_dir) or {}
            connection.setdefault("admin", {})["password"] = "existing-password"
            config_mod.save_section("connection", connection, config_dir)

            payload = channel._serialize(channel._CFG)
            transport = httpx.ASGITransport(app=channel.app)
            with (
                patch.object(channel, "_config_dir", return_value=config_dir),
                patch.object(channel, "reload_config", return_value=None),
            ):
                async with httpx.AsyncClient(transport=transport, base_url="http://autorouter") as client:
                    saved = await client.put("/api/config", json=payload)
                    self.assertEqual(saved.status_code, 200, saved.text)
                    after_connection = config_mod.load_section("connection", config_dir) or {}
                    self.assertEqual(after_connection["admin"]["password"], "existing-password")

                    before_models = (config_dir / "models.yaml").read_bytes()
                    invalid = copy.deepcopy(payload)
                    invalid["models"] = {}
                    rejected = await client.put("/api/config", json=invalid)
                    self.assertEqual(rejected.status_code, 400, rejected.text)
                    self.assertEqual((config_dir / "models.yaml").read_bytes(), before_models)


if __name__ == "__main__":
    unittest.main()
