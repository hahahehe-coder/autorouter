"""
FastAPI 入口 — 代理本体 + 管理 API。

所有端点(/v1/* 代理 + /api/* 管理)对任意来源开放,不做应用层来源限制。
安全由网络层兜底:默认封禁 3001 外网入站,需要改配置时再临时放行。
"""
from __future__ import annotations

import asyncio
import atexit
import base64
import copy
import json
import logging
import logging.handlers as logging_handlers
import os
import re
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from queue import Queue

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from . import config as config_mod
from . import ml_router
from .observe import Observer
from .router import route
from .session import SessionStore, compute_session_key

# 日志(先 basicConfig 撑早期 import,等 _CFG 加载后再挂 file handler)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("autorouter")


# ============================ module-level state(可热重载) ============================

def _config_dir() -> Path:
    return Path(os.getenv("CONFIG_DIR", "config"))


# 日志文件名白名单(防路径穿越 + 适配按天命名)
_LOG_NAME_RE = re.compile(r"^auto_router-\d{4}-\d{2}-\d{2}\.log$")


class DailyFileHandler(logging.Handler):
    """按本地日期切文件:每天一个 `auto_router-YYYY-MM-DD.log`,append,从不删除。

    行为:
    - 每次 emit 检查今天日期;如果跨日则关旧文件开新一天的(懒切,无后台线程)。
    - 文件名格式 auto_router-YYYY-MM-DD.log(自定 base 可改)。
    - 不主动清理历史 —— 全量保留。
    - reload 时通过外面 _setup_file_logging 重新装一个新的 instance,旧 instance 在 close 时关 fp。
    """

    def __init__(self, log_dir: str, base_name: str = "auto_router"):
        super().__init__()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.base_name = base_name
        self._date: str | None = None
        self._fp = None
        self.setFormatter(logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _path_for(self, date_str: str) -> Path:
        return self.log_dir / f"{self.base_name}-{date_str}.log"

    def _ensure_open(self):
        today = self._today()
        if self._date != today:
            if self._fp is not None:
                try:
                    self._fp.close()
                except Exception:
                    pass
            self._date = today
            self._fp = self._path_for(today).open("a", encoding="utf-8")

    def emit(self, record):
        try:
            self._ensure_open()
            msg = self.format(record) + "\n"
            self._fp.write(msg)
            self._fp.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        if self._fp is not None:
            try:
                self._fp.flush()
            except Exception:
                pass
            try:
                self._fp.close()
            except Exception:
                pass
            finally:
                self._fp = None
        super().close()


def _setup_file_logging(log_dir: str) -> None:
    """装异步日志:emit → QueueHandler → 后台线程的 DailyFileHandler → 文件。

    解决了"app 线程被文件 I/O 同步阻塞"的问题。每个进程(uvicorn worker)各自有
    自己的 queue + listener;worker 之间仍共享同一个 log 文件,每个 listener 在自己
    进程内单线程串行 append,无锁竞争。
    """
    global _LOG_LISTENER, _LOG_QUEUE, _ATEXIT_REGISTERED

    # 卸掉旧的(支持 reload 时切 log_dir)
    if _LOG_LISTENER is not None:
        try:
            _LOG_LISTENER.stop()
        except Exception:
            pass
        _LOG_LISTENER = None

    root = logging.getLogger()
    # 卸掉旧的 QueueHandler(避免 reload 堆叠)
    for h in list(root.handlers):
        if isinstance(h, logging_handlers.QueueHandler):
            root.removeHandler(h)

    # 后台线程专写的 file handler
    file_handler = DailyFileHandler(log_dir)
    file_handler.setLevel(logging.INFO)

    # 队列 + 后台 listener
    _LOG_QUEUE = Queue(-1)   # unbounded
    queue_handler = logging_handlers.QueueHandler(_LOG_QUEUE)
    queue_handler.setLevel(logging.INFO)
    root.addHandler(queue_handler)

    _LOG_LISTENER = logging_handlers.QueueListener(_LOG_QUEUE, file_handler)
    _LOG_LISTENER.start()
    # 每个进程(worker)只注册一次 atexit
    if not _ATEXIT_REGISTERED:
        atexit.register(_shutdown_log_listener)
        _ATEXIT_REGISTERED = True


def _shutdown_log_listener():
    global _LOG_LISTENER
    if _LOG_LISTENER is not None:
        try:
            _LOG_LISTENER.stop()
        except Exception:
            pass
        _LOG_LISTENER = None


# 模块级异步日志状态(每 worker 进程独立)
_LOG_QUEUE: Queue | None = None
_LOG_LISTENER: logging_handlers.QueueListener | None = None
_ATEXIT_REGISTERED: bool = False


# mutable containers — reload_config() 原地更新
_CFG: config_mod.Config = config_mod.load_all(_config_dir())
_setup_file_logging(_CFG.observability.log_dir)
_OBSERVER: Observer = Observer()
_SESSIONS: SessionStore = SessionStore(default_window_seconds=_CFG.policy.anti_downgrade_window_seconds)
# 启动即加载 ML(bundle/依赖缺失会自动降级,不抛、不阻塞启动)
try:
    ml_router.configure(_CFG.ml)
except Exception as e:
    logger.warning(f"ML configure at boot failed (will degrade to heuristic): {e}")


def reload_config() -> None:
    """从 YAML 重新加载。原地更新 _CFG,重建 observer,重配 ML。"""
    global _CFG, _OBSERVER
    _CFG = config_mod.load_all(_config_dir())
    _setup_file_logging(_CFG.observability.log_dir)
    _OBSERVER = Observer()
    logger.info(f"Reloaded. {len(_CFG.strategies.items)} strategies")
    # ML 重配 —— 失败不能拖垮代理(ML 挂了会自动降级启发式)
    try:
        ml_router.configure(_CFG.ml)
    except Exception as e:
        logger.warning(f"ML reconfigure failed (will degrade to heuristic): {e}")


# 模块级常量来自 _CFG(每次请求用最新的)
def _upstream_base(provider_name: str | None = None) -> str:
    """按名字取上游 base_url;None 或找不到走 default / 第一项。"""
    p = _CFG.connection.providers.resolve(provider_name)
    return (p.base_url if p else "").rstrip("/")


def _upstream_url(provider_name: str | None, path: str) -> str:
    """把 provider base_url 和标准 `/v1/...` 路径拼成一个且仅一个 `/v1`。"""
    base = _upstream_base(provider_name)
    if not base:
        raise ValueError("no upstream provider configured")
    path = "/" + path.lstrip("/")
    if base.endswith("/v1") and (path == "/v1" or path.startswith("/v1/")):
        path = path[3:] or "/"
    return f"{base}{path}"


def _model_upstream(model: str) -> str:
    """查模型注册表里它来自哪个供应商(空 = 默认)。"""
    if not model:
        return ""
    cfg = _CFG.models.items.get(model)
    return cfg.upstream if cfg else ""


# ============================ FastAPI app ============================

import os as _os
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="auto-router pseudo channel", version="2.0.0")


# ============================ admin auth 中间件 ============================

# 不需要 admin auth 的路径(白名单):健康检查
_AUTH_BYPASS_PATHS = frozenset(("/health",))


def _check_admin_basic(request: Request) -> bool:
    """Basic Auth 校验。password 留空 = 关闭 auth(开发模式),任何人都过。"""
    cfg = _CFG.connection
    if not cfg.admin_password:                # dev 模式:password 空 → 不挡
        return True
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:].strip(), validate=True).decode("utf-8")
        user, _, pw = decoded.partition(":")
        return user == cfg.admin_user and pw == cfg.admin_password
    except Exception:
        return False


@app.middleware("http")
async def _admin_auth_middleware(request: Request, call_next):
    """只挡 /api/* 设置端点(白名单除外)。
    /v1/* 转发完全不挡,任意客户端直接调用。"""
    path = request.url.path
    if path.startswith("/v1/") or path in _AUTH_BYPASS_PATHS:
        return await call_next(request)
    if path.startswith("/api/"):
        if not _check_admin_basic(request):
            # 故意不带 WWW-Authenticate:避免浏览器弹原生登录框,SPA 的 LoginScreen 处理。
            return JSONResponse({"error": "auth required"}, status_code=401)
    return await call_next(request)


# ============================ helpers ============================

def _upstream_headers(req: Request) -> dict:
    """透传 inbound 全部请求头(Host/Content-Length 等 hop-by-hop 自动剔除)。

    客户端发到 AutoRouter 的请求已经按目标协议组装好了头(Authorization、
    x-api-key、anthropic-version 等),AutoRouter 不解析也不改写。
    """
    return {k: v for k, v in req.headers.items()
            if k.lower() not in _HOP_BY_HOP}


def _request_auth_identity(req: Request) -> str:
    """会话只按认证信息识别；没有认证信息时禁用会话锁。"""
    authorization = req.headers.get("authorization", "")
    api_key = req.headers.get("x-api-key", "")
    if not authorization and not api_key:
        return ""
    return f"authorization={authorization}|x-api-key={api_key}"


# hop-by-hop 头(RFC 7230 §6.1):转发时必须由 httpx/uvicorn 重新生成,不能原样透传。
_HOP_BY_HOP = frozenset({
    "host", "content-length", "transfer-encoding", "connection",
    "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "upgrade", "expect",
})


# ============================ endpoint dispatch ============================

EP_CHAT, EP_MESSAGES, EP_RESPONSES = "chat/completions", "messages", "responses"

_TEXT_PART_TYPES = frozenset(("text", "input_text", "output_text"))
_IMAGE_PART_TYPES = frozenset(("image", "image_url", "input_image"))


def _router_content(content):
    """把各端点原生 content 归一化给路由器；不会修改实际转发 body。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type in _TEXT_PART_TYPES:
            parts.append({"type": "text", "text": str(part.get("text") or "")})
        elif part_type in _IMAGE_PART_TYPES:
            # 路由器只需要知道存在图片，不需要复制图片数据。
            parts.append({"type": "image_url"})
    return parts


def _normalized_message(message: dict) -> dict | None:
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if not role:
        return None
    return {"role": role, "content": _router_content(message.get("content", ""))}


def _endpoint_user_text(endpoint: str, body: dict) -> str:
    """从各端点 body 抽出用户文本,给 classifier 用。"""
    for message in reversed(_messages_for_router(endpoint, body)):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                str(part.get("text") or "") for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
    return ""


def _messages_for_router(endpoint: str, body: dict) -> list:
    """归一化成 messages[] 给路由器用；转发仍使用未经改写的原始 body。"""
    if endpoint in (EP_CHAT, EP_MESSAGES):
        result = []
        for message in body.get("messages") or []:
            normalized = _normalized_message(message)
            if normalized is not None:
                result.append(normalized)
        return result

    inp = body.get("input")
    if isinstance(inp, str):
        return [{"role": "user", "content": inp}]
    if not isinstance(inp, list):
        return []

    result = []
    loose_parts = []
    for item in inp:
        if not isinstance(item, dict):
            continue
        normalized = _normalized_message(item)
        if normalized is not None:
            result.append(normalized)
        elif item.get("type") in _TEXT_PART_TYPES | _IMAGE_PART_TYPES:
            loose_parts.append(item)
    if loose_parts:
        result.append({"role": "user", "content": _router_content(loose_parts)})
    return result


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


def _response_headers(headers: httpx.Headers, extra: dict | None = None) -> dict:
    """保留上游 end-to-end 响应头，剔除必须由当前连接重新生成的头。"""
    result = {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}
    if extra:
        result.update(extra)
    return result


async def _close_stream(response: httpx.Response, client: httpx.AsyncClient) -> None:
    await response.aclose()
    await client.aclose()


async def _forward_stream(
    url: str, headers: dict, extra: dict | None = None, *,
    json_body: dict | None = None, content: bytes | None = None,
) -> Response:
    """建立上游流后再返回客户端，原样传播状态、响应头和原始字节。"""
    client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
    try:
        request = client.build_request(
            "POST", url, json=json_body, content=content, headers=headers,
        )
        upstream = await client.send(request, stream=True)
    except (httpx.HTTPError, ValueError) as exc:
        await client.aclose()
        logger.error(f"upstream stream error: {exc}")
        return JSONResponse({"error": f"upstream unreachable: {exc}"}, status_code=502)
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=_response_headers(upstream.headers, extra),
        background=BackgroundTask(_close_stream, upstream, client),
    )


# ============================ 公共路由 ============================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "strategies": list(_CFG.strategies.items.keys()),
        "sessions": _SESSIONS.size(),
        "upstream_base": _upstream_base(),
        "providers": list(_CFG.connection.providers.items.keys()),
        "default_provider": _CFG.connection.providers.default,
        "ml": ml_router.status(),
    }


@app.get("/v1/models")
async def list_models_strategy():
    """列出策略名(让上游后台发现 channel 支持哪些模型)"""
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

    session_key = compute_session_key(_request_auth_identity(request), messages)
    prev_idx = _SESSIONS.get_previous_idx(session_key, _CFG.policy.anti_downgrade_window_seconds)

    # ML 推理是 CPU 密集(~50-200ms),放线程池避免阻塞事件循环(启发式廉价,同步也无妨,统一走 to_thread)
    decision = await asyncio.to_thread(
        route, want_model, body, _CFG,
        messages=messages, session_key=session_key, prev_idx=prev_idx,
    )

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
        try:
            url = _upstream_url(_model_upstream(decision.model), upstream_path)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=502)
        return await _forward_stream(url, headers, extra, json_body=body)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            async with client.stream(
                "POST", _upstream_url(_model_upstream(decision.model), upstream_path),
                json=body, headers=headers,
            ) as resp:
                content = b"".join([chunk async for chunk in resp.aiter_raw()])
                return Response(
                    content=content,
                    status_code=resp.status_code,
                    headers=_response_headers(resp.headers, extra),
                )
    except (httpx.HTTPError, ValueError) as e:
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
    headers = _upstream_headers(request)
    model = ""
    try:
        payload = json.loads(body_bytes)
        if isinstance(payload, dict):
            model = str(payload.get("model") or "")
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    logger.info(f"passthrough /v1/{path} ({len(body_bytes)} bytes)")
    try:
        return await _forward_stream(
            _upstream_url(_model_upstream(model), f"/v1/{path}"),
            headers,
            content=body_bytes,
        )
    except (httpx.HTTPError, ValueError) as e:
        logger.error(f"upstream error on /v1/{path}: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.api_route("/v1/{path:path}", methods=["POST"])
async def transparent(path: str, request: Request):
    """其它 /v1/* 端点(embeddings / audio / images …)的兜底透传。"""
    return await _transparent_post(path, request)


# ============================ 管理员 API ============================

def _prepare_section_data(section: str, data: dict) -> dict:
    """规范化 UI 快照；未显式提交的敏感字段沿用磁盘值。"""
    if not isinstance(data, dict):
        raise ValueError(f"section '{section}' must be an object")
    prepared = copy.deepcopy(data)
    if section != "connection":
        return prepared

    current = _load_section_raw("connection") or {}
    current_admin = current.get("admin") or {}
    admin = dict(prepared.get("admin") or {})
    admin.pop("enabled", None)
    if "password" not in admin:
        admin["password"] = current_admin.get("password", "")
    elif admin["password"] is None:
        admin["password"] = ""
    prepared["admin"] = admin
    return prepared

@app.get("/api/config")
async def get_config_all():
    """合并后的全量(给 /api/route/preview + 完整快照用)。"""
    return _serialize(_CFG)


@app.put("/api/config")
async def put_config_all(request: Request):
    """一次校验并保存完整/部分配置快照，避免跨 section 的中间无效状态。"""
    incoming = await request.json()
    if not isinstance(incoming, dict):
        return JSONResponse({"error": "config payload must be an object"}, 400)
    try:
        updates = {
            section: _prepare_section_data(section, incoming[section])
            for section in config_mod.SECTIONS if section in incoming
        }
        if not updates:
            raise ValueError("no known config sections supplied")
        config_mod.validate_updates(updates, _config_dir())
        config_mod.save_sections(updates, _config_dir())
        reload_config()
    except ValueError as e:
        return JSONResponse({"error": f"validation failed: {e}"}, 400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 500)
    return {"ok": True}


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
        new_data = _prepare_section_data(section, new_data)
        config_mod.validate_updates({section: new_data}, _config_dir())
        config_mod.save_section(section, new_data, _config_dir())
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
    decision = await asyncio.to_thread(
        route, strategy, body, _CFG, messages=messages, session_key="preview"
    )
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
    """从每个有 api_key 的供应商拉模型列表,合并返回 [{id, upstream}, ...]。

    前端拿到后会把新模型补进注册表(并自动填 upstream tag)。
    base_url 已含 `/v1` 时直接拼 `/models`,否则补 `/v1`(兼容 new-api 风格的裸地址)。
    """
    items = _CFG.connection.providers.items
    if not items:
        return JSONResponse({"error": "no providers configured"}, 400)
    merged: list[dict] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, p in items.items():
            if not p.api_key:
                continue
            try:
                url = _upstream_url(name, "/v1/models")
                r = await client.get(url, headers={"Authorization": f"Bearer {p.api_key}"})
                if r.status_code != 200:
                    errors.append(f"{name}: HTTP {r.status_code} {r.text[:120]}")
                    continue
                data = r.json()
                for m in data.get("data", []):
                    if isinstance(m, dict) and m.get("id"):
                        merged.append({"id": m["id"], "upstream": name})
            except (httpx.HTTPError, ValueError, json.JSONDecodeError) as e:
                errors.append(f"{name}: {e}")
    return {"models": merged, "errors": errors}


# ============================ 日志查看 API ============================

@app.get("/api/logs")
async def list_logs():
    """列出 log_dir 下的 auto_router-YYYY-MM-DD.log(按天)。"""
    log_dir = Path(_CFG.observability.log_dir)
    files = []
    today_str = datetime.now().strftime("%Y-%m-%d")
    if log_dir.exists():
        for f in sorted(log_dir.glob("auto_router-*.log"), reverse=True):
            if not f.is_file():
                continue
            st = f.stat()
            # 文件名: auto_router-YYYY-MM-DD.log → 抽 YYYY-MM-DD 段判断 is_today
            stem = f.stem  # "auto_router-2026-07-14"
            tail = stem.split("-", 1)[1] if "-" in stem else ""
            files.append({
                "name": f.name,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "is_today": tail == today_str,
            })
    return {"files": files, "log_dir": str(log_dir.resolve())}


@app.get("/api/logs/{name}")
async def read_log(name: str):
    """读日志文件全部内容(不限制行数 — 全量显示)。"""
    if not _LOG_NAME_RE.match(name):
        raise HTTPException(403, "invalid log filename")
    log_dir = Path(_CFG.observability.log_dir)
    path = (log_dir / name).resolve()
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "log file not found")
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "name": name,
        "content": text,
        "total_lines": text.count("\n"),
        "size": path.stat().st_size,
    }


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
            "providers": {
                "default": cfg.connection.providers.default,
                **{n: {"base_url": p.base_url, "api_key": p.api_key}
                   for n, p in cfg.connection.providers.items.items()},
            },
            "admin": {
                "user": cfg.connection.admin_user,
                # password 不回显(安全),前端用它判断"是否启用登录"
                "enabled": bool(cfg.connection.admin_password),
            },
        },
        "policy": {
            "anti_downgrade":     {"enabled": cfg.policy.anti_downgrade_enabled, "window_seconds": cfg.policy.anti_downgrade_window_seconds},
            "complaint_upgrade":  {"enabled": cfg.policy.complaint_upgrade_enabled, "max_chars": cfg.policy.complaint_max_chars},
            "chitchat_only":      {"enabled": cfg.policy.chitchat_only_enabled},
            "capability_gate":    {"enabled": cfg.policy.capability_gate_enabled},
            "large_context_floor": {
                "enabled": cfg.policy.large_context_floor_enabled,
                "t3_floor_tokens": cfg.policy.lc_t3_floor_tokens,
                "t2_floor_tokens": cfg.policy.lc_t2_floor_tokens,
                "t3_context_ratio": cfg.policy.lc_t3_context_ratio,
                "context_window": cfg.policy.lc_context_window,
            },
        },
        "strategies": {
            n: (
                {"kind": "single", "rule": rule_to_dict(s.rule)}
                if s.kind == "single"
                else {"kind": s.kind, "rules": [rule_to_dict(r) for r in s.rules]}
            )
            for n, s in cfg.strategies.items.items()
        },
        "observability": {
            "log_dir": cfg.observability.log_dir,
        },
        "ml": {
            "enabled": cfg.ml.enabled,
            "bundle_path": cfg.ml.bundle_path,
            "confidence_threshold": cfg.ml.confidence_threshold,
            "confidence_fallback_idx": cfg.ml.confidence_fallback_idx,
            "warmup_on_load": cfg.ml.warmup_on_load,
        },
        "models": {
            n: {
                "supports_vision": m.supports_vision,
                "context_window": m.context_window,
                "upstream": m.upstream,
            }
            for n, m in cfg.models.items.items()
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
