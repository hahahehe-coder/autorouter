# AutoRouter — new-api 伪渠道

`model∈{fast, expert, auto}` → 各自策略挑真实模型 → 回灌 new-api。架构见 [readme2.md](../readme2.md)。

## 文件

```
.
├── pyproject.toml            # 只装 fastapi/uvicorn/httpx
├── app/
│   ├── router.py             # StaticRouter + RulesRouter(Protocol = LLMRouter.route_single)
│   └── channel.py            # FastAPI 服务 — new-api 伪渠道 + 策略派发
├── scripts/
│   ├── fake_newapi.py        # 本地 mock new-api,端到端测试用
│   ├── req_*.json            # curl 测试体
└── .venv/                    # uv 创建,勿 commit
```

## 启动

```bash
D:\uv\uv.exe sync                                  # 装依赖
D:\uv\uv.exe run uvicorn app.channel:app --host 127.0.0.1 --port 3001
```

环境变量:`NEW_API_BASE`(默认 `http://127.0.0.1:3000`)、`BACKEND_HOST`、`BACKEND_PORT`。

## 三个策略

new-api 后台 channel "模型" 字段填这三个名(都映射到本服务的 :3001):

| 模型名   | 策略             | 真实下游                | 适合                  |
|---------|----------------|-----------------------|---------------------|
| `fast`  | `StaticRouter` | `gpt-4o-mini`         | 永远便宜                 |
| `expert`| `StaticRouter` | `claude-sonnet-4-5`   | 永远最强                 |
| `auto`  | `RulesRouter`  | 按关键词规则选上述三者之一       | 用户不指定,让系统挑合适的 |

模型管理里把 `fast` / `expert` / `auto` 都加上去,价格/倍率设 0。

## new-api 后台配置(纠正 readme2 的笔误)

`readme2.md` 写的 `API Base URL: http://127.0.0.1:3001/v1` 实际是错的。
new-api `model/channel.go:GetBaseURL()` 不做 trim,直接拼 `BaseURL + RequestURLPath`。
用户在 :3000 调 `/v1/chat/completions` → RequestURLPath = `/v1/chat/completions` →
拼出来是 `http://127.0.0.1:3001/v1/v1/chat/completions`,后端路由不到。

正确填法:**`API Base URL: http://127.0.0.1:3001`**(不带 `/v1`)。
渠道类型 OpenAI,API Key 随便(`not-needed`)。

## 响应头(调试用)

所有回灌请求都带:
- `X-Auto-Routed-To`:本次实际调用的真实模型名
- `X-Strategy`:用户传入的策略名(`fast` / `expert` / `auto`)
- `X-Router-Reason`:策略内部理由(`rules` / `rules-default` / `fast` / `expert` / `passthrough`)

## 端到端测试(本机 mock new-api)

```bash
# terminal 1 — fake new-api
D:\uv\uv.exe run python scripts/fake_newapi.py

# terminal 2 — auto-router
D:\uv\uv.exe run uvicorn app.channel:app --host 127.0.0.1 --port 3001

# terminal 3 — 调用
curl -s -X POST http://127.0.0.1:3001/v1/chat/completions \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary "@scripts/req_auto_code.json"
```

期望:
- `model=fast`            → `gpt-4o-mini`
- `model=expert`          → `claude-sonnet-4-5`
- `model=auto`, "hi"      → `gpt-4o-mini`
- `model=auto` "写个 python 快排" → `gpt-4o`

## Phase 2 切 ML router

换 `app/channel.py` 里的 `STRATEGIES` 字典里某个值为新实现即可,
例:把 `RulesRouter()` 换成 `HybridLLMRouter(...)`,那时再 `uv add scikit-learn`(不必现在装)。

## 安全

服务 listen `127.0.0.1` 只接 new-api 本机回环,不暴露公网。
