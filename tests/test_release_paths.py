from __future__ import annotations

import copy
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app import channel
from app import config as config_mod
from app.config import ModelCfg, ProvidersCfg, StrategyCfg, StrategiesCfg, UpstreamCfg
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

    def test_apply_field_thinking_off_chat_sets_reasoning_effort_none(self):
        """OpenAI Chat Completions 格式:thinking=off → 顶层 reasoning_effort="none"。

        注意不是 reasoning={effort:"none"}(那个是 Responses API 写法)。
        OpenAI Chat Completions 官方把 reasoning_effort 作为顶层标量字段,
        值允许 "low" / "medium" / "high" / "none"。
        """
        body: dict = {"model": "MiniMax-M3", "reasoning": {"effort": "high"}}
        channel._apply_field(channel.EP_CHAT, "thinking", "off", body)
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertNotIn("reasoning", body)

    def test_apply_field_thinking_off_messages_sets_type_disabled(self):
        """Anthropic messages 格式:thinking=off → thinking.type=disabled。"""
        body: dict = {"model": "claude-opus-4-7", "thinking": {"type": "enabled", "budget_tokens": 4096}}
        channel._apply_field(channel.EP_MESSAGES, "thinking", "off", body)
        self.assertEqual(body["thinking"], {"type": "disabled"})

    def test_apply_field_thinking_off_responses_sets_effort_none(self):
        """OpenAI responses 格式:thinking=off → reasoning.effort=none。"""
        body: dict = {"model": "MiniMax-M3"}
        channel._apply_field(channel.EP_RESPONSES, "thinking", "off", body)
        self.assertEqual(body["reasoning"], {"effort": "none"})


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

    async def test_x_original_auth_overrides_authorization_for_nested_newapi(self):
        """嵌套 new-api 渠道:有 X-Original-Auth 时用它替换 Authorization 转发给上游。

        new-api 渠道配 `X-Original-Auth: {client_header:Authorization}`,把原用户
        token 透传给 AutoRouter;AutoRouter 转发到上游(通常又是 new-api)时用这个值
        作 Authorization,让上游能正确按用户归属计费。
        """
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, stream=_OneChunkStream(b'{"ok":true}'),
                                  headers={"content-type": "application/json"})

        transport = httpx.MockTransport(handler)

        def upstream_client(*args, **kwargs):
            kwargs["transport"] = transport
            return self.real_async_client(*args, **kwargs)

        app_transport = httpx.ASGITransport(app=channel.app)
        async with self.real_async_client(transport=app_transport, base_url="http://autorouter") as client:
            with patch.object(channel.httpx, "AsyncClient", side_effect=upstream_client):
                await client.post(
                    "/v1/chat/completions",
                    headers={
                        "Authorization": "Bearer channel-key",
                        "X-Original-Auth": "Bearer alice-user-token",
                    },
                    json={"model": "not-a-strategy", "messages": [{"role": "user", "content": "hi"}]},
                )
        # 上游收到的应该是用户真 token,不是 channel-key
        self.assertEqual(seen[0].headers["authorization"], "Bearer alice-user-token")
        # X-Original-Auth 自身不再透传(已消费)
        self.assertNotIn("x-original-auth", seen[0].headers)

    async def test_no_x_original_auth_keeps_passthrough_behavior(self):
        """没有 X-Original-Auth 时保持原样透传 Authorization(直接客户端场景不变)。"""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, stream=_OneChunkStream(b'{"ok":true}'),
                                  headers={"content-type": "application/json"})

        transport = httpx.MockTransport(handler)

        def upstream_client(*args, **kwargs):
            kwargs["transport"] = transport
            return self.real_async_client(*args, **kwargs)

        app_transport = httpx.ASGITransport(app=channel.app)
        async with self.real_async_client(transport=app_transport, base_url="http://autorouter") as client:
            with patch.object(channel.httpx, "AsyncClient", side_effect=upstream_client):
                await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer direct-client-key"},
                    json={"model": "not-a-strategy", "messages": [{"role": "user", "content": "hi"}]},
                )
        self.assertEqual(seen[0].headers["authorization"], "Bearer direct-client-key")


class RobustFailoverTests(unittest.IsolatedAsyncioTestCase):
    """robust 策略:失败(网络错误/429/5xx)按序切换下一个模型,其余状态原样透传。"""

    PRIMARY = "MiniMax-M3"   # 两个名字都在现有 models.yaml 注册表 fixture 中
    BACKUP = "glm-5.2"

    async def asyncSetUp(self):
        self.real_async_client = httpx.AsyncClient
        self.old_providers = channel._CFG.connection.providers
        self.old_models = channel._CFG.models.items
        self.old_strategies = channel._CFG.strategies
        self.old_policy = channel._CFG.policy
        channel._CFG.connection.providers = ProvidersCfg(
            default="default",
            items={
                "default": UpstreamCfg(name="default", base_url="https://default.test", api_key=""),
                "mock": UpstreamCfg(name="mock", base_url="https://upstream.test/v1", api_key=""),
            },
        )
        channel._CFG.strategies = StrategiesCfg(items={
            "stable": StrategyCfg(
                name="stable", kind="robust",
                models=[self.PRIMARY, self.BACKUP],
            ),
        })
        channel._MODEL_COOLDOWN.clear()

    async def asyncTearDown(self):
        channel._CFG.connection.providers = self.old_providers
        channel._CFG.models.items = self.old_models
        channel._CFG.strategies = self.old_strategies
        channel._CFG.policy = self.old_policy
        channel._MODEL_COOLDOWN.clear()

    async def _request(self, handler, *, stream: bool) -> tuple[httpx.Response, list[httpx.Request]]:
        """走 /v1/chat/completions,model=robust 策略名;handler 按请求体 model 分流。"""
        seen: list[httpx.Request] = []

        def wrapped(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        transport = httpx.MockTransport(wrapped)

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
                        "model": "stable",
                        "stream": stream,
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                )
        return response, seen

    def _status_by_model(self, primary_status: int, backup_status: int, backup_body: bytes):
        """handler 工厂:按请求体里的 model 名返回对应状态。"""
        def handler(request: httpx.Request) -> httpx.Response:
            model = json.loads(request.content)["model"]
            if model == self.PRIMARY:
                return httpx.Response(
                    primary_status, stream=_OneChunkStream(b'{"error":"primary-failed"}'),
                    headers={"content-type": "application/json", "x-upstream": "primary"},
                )
            return httpx.Response(
                backup_status, stream=_OneChunkStream(backup_body),
                headers={"content-type": "application/json", "x-upstream": "backup"},
            )
        return handler

    async def test_buffered_500_then_200_switches_to_next_model(self):
        response, seen = await self._request(
            self._status_by_model(500, 200, b'{"ok":"backup"}'), stream=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'{"ok":"backup"}')
        self.assertEqual(len(seen), 2)
        # 两次上游调用各自携带正确的模型名
        self.assertEqual(json.loads(seen[0].content)["model"], self.PRIMARY)
        self.assertEqual(json.loads(seen[1].content)["model"], self.BACKUP)
        # 响应头反映实际服务模型
        self.assertEqual(response.headers["x-auto-routed-to"], self.BACKUP)

    async def test_stream_500_then_200_switches_before_first_byte(self):
        """流式:500 的响应头到达但字节未流出 → 可安全切换。"""
        response, seen = await self._request(
            self._status_by_model(500, 200, b'data: {"ok":"backup"}\n\n'), stream=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'data: {"ok":"backup"}\n\n')
        self.assertEqual(len(seen), 2)
        self.assertEqual(response.headers["x-auto-routed-to"], self.BACKUP)

    async def test_429_triggers_failover(self):
        response, seen = await self._request(
            self._status_by_model(429, 200, b'{"ok":"backup"}'), stream=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(seen), 2)

    async def test_403_triggers_failover(self):
        response, seen = await self._request(
            self._status_by_model(403, 200, b'{"ok":"backup"}'), stream=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(seen), 2)

    async def test_failed_model_enters_cooldown_and_is_skipped(self):
        handler = self._status_by_model(500, 200, b'{"ok":"backup"}')
        first, seen1 = await self._request(handler, stream=False)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(seen1), 2)
        self.assertTrue(channel._model_in_cooldown(self.PRIMARY))
        # 第二次请求:冷却中的 PRIMARY 被跳过,直接打 BACKUP
        second, seen2 = await self._request(handler, stream=False)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(seen2), 1)
        self.assertEqual(json.loads(seen2[0].content)["model"], self.BACKUP)

    async def test_cooldown_expiry_retries_model(self):
        handler = self._status_by_model(500, 200, b'{"ok":"backup"}')
        await self._request(handler, stream=False)
        # 手动让 PRIMARY 的冷却过期
        channel._MODEL_COOLDOWN[self.PRIMARY] = time.monotonic() - 1
        self.assertFalse(channel._model_in_cooldown(self.PRIMARY))
        _, seen2 = await self._request(handler, stream=False)
        self.assertEqual(len(seen2), 2)
        self.assertEqual(json.loads(seen2[0].content)["model"], self.PRIMARY)

    async def test_all_models_in_cooldown_still_tried_in_order(self):
        handler = self._status_by_model(500, 503, b'{"error":"backup-failed"}')
        first, seen1 = await self._request(handler, stream=False)
        self.assertEqual(first.status_code, 503)
        self.assertTrue(channel._model_in_cooldown(self.PRIMARY))
        self.assertTrue(channel._model_in_cooldown(self.BACKUP))
        # 全冷却:忽略冷却按原序照常尝试,不直接报错
        second, seen2 = await self._request(handler, stream=False)
        self.assertEqual(second.status_code, 503)
        self.assertEqual(len(seen2), 2)
        self.assertEqual(json.loads(seen2[0].content)["model"], self.PRIMARY)

    async def test_non_failover_status_does_not_mark_cooldown(self):
        handler = self._status_by_model(401, 200, b'{"ok":"backup"}')
        first, seen1 = await self._request(handler, stream=False)
        self.assertEqual(first.status_code, 401)
        self.assertEqual(len(seen1), 1)
        self.assertFalse(channel._model_in_cooldown(self.PRIMARY))
        # 401 不冷却 → 下次请求仍先打 PRIMARY
        _, seen2 = await self._request(handler, stream=False)
        self.assertEqual(len(seen2), 1)
        self.assertEqual(json.loads(seen2[0].content)["model"], self.PRIMARY)

    async def test_cooldown_zero_disables_marking(self):
        old = channel._CFG.policy
        try:
            channel._CFG.policy = config_mod.PolicyCfg(failover_cooldown_seconds=0)
            handler = self._status_by_model(500, 200, b'{"ok":"backup"}')
            await self._request(handler, stream=False)
            self.assertFalse(channel._model_in_cooldown(self.PRIMARY))
        finally:
            channel._CFG.policy = old

    def test_policy_failover_parse_and_validate(self):
        self.assertEqual(config_mod._parse_policy({}).failover_cooldown_seconds, 600)
        parsed = config_mod._parse_policy({"failover": {"cooldown_seconds": 30}})
        self.assertEqual(parsed.failover_cooldown_seconds, 30)
        old = channel._CFG.policy
        try:
            channel._CFG.policy = config_mod.PolicyCfg(failover_cooldown_seconds=-1)
            with self.assertRaises(ValueError):
                config_mod.validate(channel._CFG)
        finally:
            channel._CFG.policy = old

    async def test_401_does_not_trigger_failover(self):
        response, seen = await self._request(
            self._status_by_model(401, 200, b'{"ok":"backup"}'), stream=False)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.content, b'{"error":"primary-failed"}')
        self.assertEqual(len(seen), 1)
        self.assertEqual(response.headers["x-auto-routed-to"], self.PRIMARY)

    async def test_all_models_fail_returns_last_response_verbatim(self):
        """最后一搏(503 也属切换类)不再切换,原样返回。"""
        response, seen = await self._request(
            self._status_by_model(500, 503, b'{"error":"backup-failed"}'), stream=False)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b'{"error":"backup-failed"}')
        self.assertEqual(response.headers["x-upstream"], "backup")
        self.assertEqual(len(seen), 2)

    async def test_all_transport_errors_return_502(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")
        response, seen = await self._request(handler, stream=False)
        self.assertEqual(response.status_code, 502)
        self.assertIn(b"unreachable", response.content)
        self.assertEqual(len(seen), 2)

    # --- 配置解析 / 校验(同步用例) ---

    def test_config_rejects_robust_with_empty_models(self):
        old = channel._CFG.strategies
        try:
            channel._CFG.strategies = StrategiesCfg(items={
                "stable": StrategyCfg(name="stable", kind="robust", models=[]),
            })
            with self.assertRaises(ValueError):
                config_mod.validate(channel._CFG)
        finally:
            channel._CFG.strategies = old

    def test_config_rejects_robust_with_unknown_model(self):
        old = channel._CFG.strategies
        try:
            channel._CFG.strategies = StrategiesCfg(items={
                "stable": StrategyCfg(name="stable", kind="robust", models=["no-such-model"]),
            })
            with self.assertRaises(ValueError):
                config_mod.validate(channel._CFG)
        finally:
            channel._CFG.strategies = old

    def test_parse_and_serialize_roundtrip(self):
        parsed = config_mod._parse_strategies(
            {"stable": {"kind": "robust", "models": [self.PRIMARY, self.BACKUP]}})
        self.assertEqual(parsed.items["stable"].models, [self.PRIMARY, self.BACKUP])
        old = channel._CFG.strategies
        try:
            channel._CFG.strategies = StrategiesCfg(items={
                "stable": StrategyCfg(name="stable", kind="robust",
                                      models=[self.PRIMARY, self.BACKUP]),
            })
            snapshot = channel._serialize(channel._CFG)
        finally:
            channel._CFG.strategies = old
        self.assertEqual(snapshot["strategies"]["stable"],
                         {"kind": "robust", "models": [self.PRIMARY, self.BACKUP]})


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

    async def test_put_broadcasts_sighup_so_other_workers_reload(self):
        """--workers N>1 时,PUT 处理后必须广播 SIGHUP 让其它 worker 一起 reload_config()。

        避免「UI 显示新配置 / 文件是新配置 / 但请求落到未更新的 worker 上」这种 stale 状态。
        """
        transport = httpx.ASGITransport(app=channel.app)
        with patch.object(channel, "_broadcast_sighup_to_workers") as broadcast:
            async with httpx.AsyncClient(transport=transport, base_url="http://autorouter") as client:
                # 完整 PUT
                payload = channel._serialize(channel._CFG)
                r1 = await client.put("/api/config", json=payload)
                self.assertEqual(r1.status_code, 200, r1.text)
                broadcast.assert_called()
                broadcast.reset_mock()
                # 单 section PUT
                strategies = config_mod.load_section("strategies", Path(channel._config_dir())) or {}
                r2 = await client.put("/api/config/strategies", json=strategies)
                self.assertEqual(r2.status_code, 200, r2.text)
                broadcast.assert_called()


if __name__ == "__main__":
    unittest.main()
