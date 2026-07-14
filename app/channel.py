"""
FastAPI 入口 — 代理本体 + 管理 API。

所有端点(/v1/* 代理 + /api/* 管理)对任意来源开放,不做应用层来源限制。
安全由网络层兜底:默认封禁 3001 外网入站,需要改配置时再临时放行。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from . import config as config_mod
from .observe import Observer
from .router import route
from .session import SessionStore, compute_session_key

# 日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("autorouter")


# ============================ module-level state(可热重载) ============================

def _config_dir() -> Path:
    return Path(os.getenv("CONFIG_DIR", "config"))


# mutable containers — reload_config() 原地更新
_CFG: config_mod.Config = config_mod.load_all(_config_dir())
_OBSERVER: Observer = Observer(_CFG.observability)
_SESSIONS: SessionStore = SessionStore(default_window_seconds=_CFG.policy.anti_downgrade_window_seconds)


def reload_config() -> None:
    """从 YAML 重新加载。原地更新 _CFG,重建 observer。"""
    global _CFG, _OBSERVER
    _CFG = config_mod.load_all(_config_dir())
    _OBSERVER = Observer(_CFG.observability)
    logger.info(f"Reloaded. {len(_CFG.strategies.items)} strategies")


# 模块级常量来自 _CFG(每次请求用最新的)
def _new_api_base() -> str:
    return _CFG.connection.new_api.base_url.rstrip("/")


# ============================ FastAPI app ============================

import os as _os
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="auto-router pseudo channel", version="2.0.0")


# ============================ helpers ============================

def _upstream_headers(req: Request) -> dict:
    """从 new-api 透传 Authorization 等到回灌请求。"""
    h = {"Content-Type": "application/json"}
    auth = req.headers.get("authorization") or req.headers.get("Authorization")
    if auth:
        h["Authorization"] = auth
    return h


# ============================ endpoint dispatch ============================

EP_CHAT, EP_MESSAGES, EP_RESPONSES = "chat", "messages", "responses"


def _endpoint_user_text(endpoint: str, body: dict) -> str:
    """从各端点 body 抽出用户文本,给 classifier 用。"""
    if endpoint in (EP_CHAT, EP_MESSAGES):
        for m in reversed(body.get("messages") or []):
            if m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, str): return c
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "text":
                            return b.get("text", "")
    elif endpoint == EP_RESPONSES:
        inp = body.get("input")
        if isinstance(inp, str): return inp
        if isinstance(inp, list) and inp:
            last = inp[-1]
            if isinstance(last, dict):
                c = last.get("content")
                if isinstance(c, str): return c
                if isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "text":
                            return b.get("text", "")
    return ""


def _messages_for_router(endpoint: str, body: dict) -> list:
    """归一化成 messages[] 给 router.classify_with_messages 用。"""
    if endpoint in (EP_CHAT, EP_MESSAGES):
        return body.get("messages") or []
    text = _endpoint_user_text(EP_RESPONSES, body)
    return [{"role": "user", "content": text}] if text else []


def _set_chat_system(body: dict, system_text: str) -> None:
    """chat 端点的 system:在 messages[0] 插/替换一条 system 消息。"""
    msgs = body.get("messages") or []
    if msgs and msgs[0].get("role") == "system":
        msgs[0]["content"] = system_text
    else:
        msgs.insert(0, {"role": "system", "content": system_text})
    body["messages"] = msgs


def _apply_field(endpoint: str, canonical: str, value, body: dict) -> None:
    """按端点字段映射,把 canonical 字段写入 body(messages 的 thinking 写两键)。"""
    if canonical == "model":
        body["model"] = value
    elif canonical == "max_tokens":
        body["max_output_tokens" if endpoint == EP_RESPONSES else "max_tokens"] = value
    elif canonical == "system":
        if endpoint == EP_CHAT:
            _set_chat_system(body, value)
        elif endpoint == EP_MESSAGES:
            body["system"] = value
        elif endpoint == EP_RESPONSES:
            body["instructions"] = value
    elif canonical == "thinking":
        if value == "off":   # 不思考:按端点写关闭值,并清掉残留的开启态字段
            if endpoint == EP_CHAT:
                body.pop("reasoning_effort", None)
                body["reasoning"] = {"exclude": True}
            elif endpoint == EP_RESPONSES:
                body["reasoning"] = {"effort": "none"}
            elif endpoint == EP_MESSAGES:
                body.pop("output_config", None)
                body["thinking"] = {"type": "disabled"}
        elif endpoint == EP_CHAT:
            body["reasoning_effort"] = value
        elif endpoint == EP_RESPONSES:
            body["reasoning"] = {"effort": value}
        elif endpoint == EP_MESSAGES:
            body["thinking"] = {"type": "adaptive"}
            body["output_config"] = {"effort": value}


async def _stream_upstream(path: str, body: dict, headers: dict) -> AsyncGenerator[bytes, None]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
        async with client.stream("POST", f"{_new_api_base()}{path}", json=body, headers=headers) as resp:
            async for chunk in resp.aiter_bytes():
                yield chunk


# ============================ 公共路由 ============================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "strategies": list(_CFG.strategies.items.keys()),
        "sessions": _SESSIONS.size(),
        "new_api_base": _new_api_base(),
    }


@app.get("/v1/models")
async def list_models_strategy():
    """列出策略名(让 new-api 后台发现 channel 支持哪些模型)"""
    return {
        "object": "list",
        "data": [
            {"id": name, "object": "model"}
            for name in _CFG.strategies.items.keys()
        ],
    }


# ============================ unified route + overlay core ============================

async def _route_and_forward(endpoint: str, request: Request):
    """chat / messages / responses 三个端点共用:路由 → 字段覆盖 → 转发同端点。"""
    body = await request.json()
    headers = _upstream_headers(request)
    upstream_path = f"/v1/{endpoint}"

    want_model = body.get("model", "")
    messages = _messages_for_router(endpoint, body)
    text = _endpoint_user_text(endpoint, body)

    session_key = compute_session_key(headers.get("Authorization", ""), messages)
    prev_idx = _SESSIONS.get_previous_idx(session_key, _CFG.policy.anti_downgrade_window_seconds)

    decision = route(want_model, body, _CFG,
                     messages=messages, session_key=session_key,
                     prev_idx=prev_idx)

    if decision.rule_idx >= 0:
        _SESSIONS.record(session_key, decision.rule_idx)

    # 字段覆盖:规则里声明什么字段(model/max_tokens/system/thinking)就覆盖什么
    for k, v in (decision.fields or {}).items():
        _apply_field(endpoint, k, v, body)

    _OBSERVER.record(
        strategy=decision.strategy, session_key=session_key,
        msg_preview=text, rule_idx=decision.rule_idx, model=decision.model,
        confidence=decision.confidence, source=decision.source,
        steps=decision.policies,
    )

    extra = {
        "X-Auto-Routed-To": decision.model,
        "X-Auto-Rule":      f"{decision.rule_idx}" if decision.rule_idx >= 0 else "-",
        "X-Strategy":       decision.strategy,
        "X-Source":         decision.source,
    }

    if body.get("stream"):
        async def gen():
            try:
                async for chunk in _stream_upstream(upstream_path, body, headers):
                    yield chunk
            except httpx.HTTPError as e:
                logger.error(f"upstream stream error: {e}")
                yield b'data: {"error":"upstream unreachable"}\n\n'
        return StreamingResponse(gen(), media_type="text/event-stream", headers=extra)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            resp = await client.post(f"{_new_api_base()}{upstream_path}", json=body, headers=headers)
            return JSONResponse(content=resp.json(), status_code=resp.status_code, headers=extra)
    except httpx.HTTPError as e:
        logger.error(f"upstream error on {upstream_path}: {e}")
        return JSONResponse({"error": f"upstream unreachable: {e}"}, status_code=502)


# ============================ 三个路由目标端点 ============================

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    return await _route_and_forward(EP_CHAT, request)


@app.post("/v1/messages")
async def messages_endpoint(request: Request):
    return await _route_and_forward(EP_MESSAGES, request)


@app.post("/v1/responses")
async def responses_endpoint(request: Request):
    return await _route_and_forward(EP_RESPONSES, request)


# ============================ 兜底透传(embeddings / audio / images 等) ============================

async def _transparent_post(path: str, request: Request) -> Response:
    body_bytes = await request.body()
    headers = dict(request.headers)
    headers.pop("host", None)
    headers.pop("content-length", None)
    logger.info(f"passthrough /v1/{path} ({len(body_bytes)} bytes)")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            upstream = await client.post(f"{_new_api_base()}/v1/{path}", content=body_bytes, headers=headers)
        return StreamingResponse(
            iter([upstream.content]), status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )
    except httpx.HTTPError as e:
        logger.error(f"upstream error on /v1/{path}: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.api_route("/v1/{path:path}", methods=["POST"])
async def transparent(path: str, request: Request):
    """其它 /v1/* 端点(embeddings / audio / images …)的兜底透传。"""
    return await _transparent_post(path, request)


# ============================ 管理员 API ============================

@app.get("/api/config")
async def get_config_all():
    """合并后的全量(给 /api/route/preview + 完整快照用)。"""
    return _serialize(_CFG)


@app.get("/api/config/{section}")
async def get_config_section(section: str):
    if section not in config_mod.SECTIONS:
        return JSONResponse({"error": f"unknown section '{section}'"}, 404)
    data = _load_section_raw(section)
    if data is None:
        return JSONResponse({"error": f"section '{section}' has no file"}, 404)
    return data


@app.put("/api/config/{section}")
async def put_config_section(section: str, request: Request):
    if section not in config_mod.SECTIONS:
        return JSONResponse({"error": f"unknown section '{section}'"}, 404)
    new_data = await request.json()
    try:
        config_mod.save_section(section, new_data)
        reload_config()    # 全量重载 + 跨文件校验
    except ValueError as e:
        return JSONResponse({"error": f"validation failed: {e}"}, 400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)
    return {"ok": True}


@app.post("/api/reload")
async def reload_endpoint():
    try:
        reload_config()
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)
    return {"ok": True}


@app.post("/api/route/preview")
async def route_preview(request: Request):
    """不调上游,只返回路由决策 — 给前端实时测试 / 调试用。"""
    req = await request.json()
    strategy = req.get("strategy") or "auto"
    text = req.get("query") or ""
    messages = req.get("messages") or [{"role": "user", "content": text}]
    body = {"model": strategy, "messages": messages}
    decision = route(strategy, body, _CFG, messages=messages, session_key="preview")
    return {
        "strategy": decision.strategy,
        "rule_idx": decision.rule_idx,
        "rule_count": decision.rule_count,
        "model": decision.model,
        "confidence": decision.confidence,
        "source": decision.source,
        "band": decision.band,
        "fields": decision.fields,
        "policies": [
            {"name": s.name, "input_idx": s.input_idx, "output_idx": s.output_idx, "fired": s.fired, "info": s.info}
            for s in decision.policies
        ],
    }


@app.get("/api/models")
async def pull_upstream_models():
    """从 new-api 拉 /v1/models(需要 API key)。"""
    base = _new_api_base()
    key = _CFG.connection.new_api.api_key
    if not key:
        return JSONResponse({"error": "new_api.api_key not configured"}, 400)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/v1/models", headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200:
            return JSONResponse({"error": f"new-api returned {r.status_code}: {r.text[:200]}"}, 502)
        data = r.json()
        return [m["id"] for m in data.get("data", [])]
    except httpx.HTTPError as e:
        return JSONResponse({"error": str(e)}, 502)


# ============================ helpers(序列化/反序列化) ============================

def _serialize(cfg: config_mod.Config) -> dict:
    """合并 Config → dict(给 API 返回)。YAML 风格 dict。"""
    def rule_to_dict(r):
        d = {"model": r.model}
        if r.max_tokens is not None:
            d["max_tokens"] = r.max_tokens
        if r.system:
            d["system"] = r.system
        if r.thinking:
            d["thinking"] = r.thinking
        return d
    return {
        "connection": {
            "server": {"host": cfg.connection.server.host, "port": cfg.connection.server.port},
            "new_api": {"base_url": cfg.connection.new_api.base_url, "api_key": cfg.connection.new_api.api_key},
        },
        "policy": {
            "anti_downgrade":     {"enabled": cfg.policy.anti_downgrade_enabled, "window_seconds": cfg.policy.anti_downgrade_window_seconds},
            "complaint_upgrade":  {"enabled": cfg.policy.complaint_upgrade_enabled, "max_chars": cfg.policy.complaint_max_chars},
        },
        "strategies": {
            n: (
                {"kind": "static", "rule": rule_to_dict(s.rule)}
                if s.kind == "static"
                else {"kind": "heuristic", "rules": [rule_to_dict(r) for r in s.rules]}
            )
            for n, s in cfg.strategies.items.items()
        },
        "observability": {
            "decision_log": cfg.observability.decision_log,
            "jsonl_path": cfg.observability.jsonl_path,
            "log_preview_chars": cfg.observability.log_preview_chars,
        },
    }


def _load_section_raw(name: str):
    return config_mod.load_section(name, _config_dir())


# ============================ SPA mount(放最后,让它 catch-all 兜底) ============================

_SPA_DIR = _os.getenv("SPA_DIST", "web/dist")
if _os.path.isdir(_SPA_DIR):
    # 必须在所有 @app.* 路由注册完之后 mount,
    # 否则 StaticFiles 会先匹配并打到 404。
    app.mount("/", StaticFiles(directory=_SPA_DIR, html=True), name="spa")
    logger.info(f"SPA mounted from {_SPA_DIR}")


# ============================ main ============================

if __name__ == "__main__":
    import uvicorn
    host = _CFG.connection.server.host
    port = _CFG.connection.server.port
    uvicorn.run(app, host=host, port=port, log_level="info")
