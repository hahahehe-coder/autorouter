# LLMRouter × new-api auto 路由 — 项目交接文档

> 用途:把这次会话的所有讨论、结论、代码、配置汇总到这里,方便迁移会话后继续工作。
> 最后更新:2026-07-11

---

## 一、项目背景

部署了自己的 LLM API 中转站(基于 [new-api](https://github.com/QuantumNous/new-api),地址 `http://127.0.0.1:3000`),接入了大量上游模型。用户已经有独立 API key、按 key 配额/计费。

**目标**:增加 `auto` 模型功能 —— 用户请求 `model: "auto"` 时,系统自动判断 query 难度,路由到合适的下游模型(便宜的给简单 query,贵的给复杂 query),帮用户省钱。

**不使用**:不训练任何 ML router(初期)。先纯规则(`rules` 策略),后续用日志训练 `thresholdrouter` / `knnrouter`。

---

## 二、最终架构(已经定稿,不要再讨论)

```
用户 App
  │ POST /v1/chat/completions
  │ Authorization: Bearer <用户独立 key>
  │ body: {"model": "auto", "messages": [...]}
  ▼
new-api (:3000)                                    [现有,不动]
  │ 鉴权、限流、auto 预扣 = 0 (auto 单价 0)
  │ model="auto" → 路由到 channel "auto-router"
  ▼
auto_router (:3001)                                [新增,Python 服务]
  │ 看到 model="auto"
  │ pick_model(query) → "gpt-4o-mini" (或 gpt-4o / claude-sonnet)
  │ 改写 body.model = "gpt-4o-mini"
  │
  │ POST http://127.0.0.1:3000/v1/chat/completions  ← 回灌 new-api
  │ Authorization: Bearer <原用户 key,透传>
  │ body: {"model": "gpt-4o-mini", ...}
  ▼
new-api (:3000)                                    [再次进入]
  │ 鉴权、按 gpt-4o-mini 单价预扣
  │ 路由到真实 OpenAI/Anthropic/... channel
  │ 调上游、拿响应
  │ 按实际 token 后扣费
  ▼
auto_router (:3001)                                [原样回传]
  ▼
new-api (:3000) → 用户
```

**关键点**:
- `:3001` **绝不直接调上游**,一定回灌 new-api
- 用户的 Authorization 透传,让 new-api 正常鉴权/扣费
- `auto` 单价 0,用户实际只付"被路由到的真实模型"的钱
- 不会有死循环(因为 :3001 改写 model 后才回灌)

---

## 三、代码改动清单

### 3.1 new-api 后台配置(管理员操作,无需改代码)

**步骤 1:创建 channel `auto-router`**

渠道管理 → 添加渠道:
```
渠道类型:OpenAI
渠道名称:auto-router
API Base URL:http://127.0.0.1:3001/v1
API Key:not-needed    (本地服务不校验)
模型:auto
分组:default
状态:启用
```

**步骤 2:添加自定义模型 `auto`**

模型管理 → 添加模型:
```
模型名称:auto
```

**步骤 3:设置 `auto` 单价 = 0**

在系统设置 → 模型价格(或模型倍率)里,把 `auto` 单价/倍率设为 0。

### 3.2 `:3001` 服务代码

文件:`auto_router.py`(放在服务器任意目录,例如 `/opt/llmrouter-auto/auto_router.py`)

```python
# auto_router.py — :3001 路由服务
import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

NEW_API = os.getenv("NEW_API_BASE", "http://127.0.0.1:3000")
app = FastAPI()

# ===== 路由决策(Phase 1: 规则) =====
def pick_model(query: str) -> str:
    """query → 实际模型名。后续可换 ML router。"""
    q = query.lower()
    if any(k in q for k in ["代码", "code", "python", "bug", "function"]):
        return "gpt-4o"
    if any(k in q for k in ["分析", "推理", "why", "explain", "reason", "证明"]):
        return "claude-sonnet-4-5"
    return "gpt-4o-mini"

def extract_query(body: dict) -> str:
    """从 messages 抽最后一条用户消息"""
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(p.get("text", "") for p in c if p.get("type") == "text")
    return ""

# ===== 核心:回灌 new-api =====
async def forward_back(request: Request, body: dict, routed_to: str = None):
    headers = {
        "Content-Type": "application/json",
        "Authorization": request.headers.get("Authorization", ""),  # 透传用户 key
    }
    is_stream = body.get("stream", False)
    extra = {"X-Auto-Routed-To": routed_to} if routed_to else {}

    async with httpx.AsyncClient(timeout=300.0) as client:
        if is_stream:
            async def gen():
                async with client.stream(
                    "POST", f"{NEW_API}/v1/chat/completions",
                    json=body, headers=headers,
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
            return StreamingResponse(gen(), media_type="text/event-stream", headers=extra)
        else:
            resp = await client.post(
                f"{NEW_API}/v1/chat/completions", json=body, headers=headers,
            )
            return JSONResponse(
                content=resp.json(), status_code=resp.status_code, headers=extra,
            )

# ===== 入口 =====
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    if body.get("model") == "auto":
        chosen = pick_model(extract_query(body))
        body["model"] = chosen
        return await forward_back(request, body, chosen)
    # 兜底:非 auto 也直接回灌(理论上不会到这里)
    return await forward_back(request, body)

@app.get("/health")
async def health():
    return {"status": "ok"}
```

### 3.3 启动 `:3001`

```bash
D:\uv\uv.exe pip install fastapi uvicorn httpx

uvicorn auto_router:app --host 127.0.0.1 --port 3001 --workers 2
```

---

## 四、用户侧调用(零改动)

```bash
curl http://127.0.0.1:3000/v1/chat/completions \
  -H "Authorization: Bearer $NEW_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "写个快排"}]
  }'
```

响应 header 里会有 `X-Auto-Routed-To: gpt-4o-mini`,客户端可以展示给用户:"本次 query 路由到 gpt-4o-mini,为你节省 ¥X"。

---

## 五、计费核对逻辑

| 步骤 | 计费 |
|------|------|
| 用户发 `model=auto` 进 new-api | 预扣 0(auto 单价 0) |
| :3001 改写后回灌 new-api | 按"真实模型"单价预扣 |
| 上游响应 | 按实际 token 后扣费 |
| **用户最终付费** | = 真实模型的钱 |

**绝对没有双重计费**,因为 auto 单价是 0。

---

## 六、上线 checklist

- [ ] `:3001` 监听 `127.0.0.1`,**不暴露公网**
- [ ] 流式 SSE 测一遍,确认 `X-Auto-Routed-To` header 在流式下能拿
- [ ] 用真实 user key 走一遍:确认 new-api 鉴权成功,扣的是真实模型的钱
- [ ] 故意把 :3001 关掉:确认请求会失败而不是死循环
- [ ] 日志审计:确认每次 auto 请求在 new-api 产生 2 条记录(auto=0 + 真实模型=实际费用)
- [ ] 加 systemd / supervisor 守护 `:3001`,挂了自动重启

---

## 七、未来演进路径

### Phase 1(当前):规则路由
- `pick_model()` 是关键词匹配
- 覆盖 80% 场景
- 部署成本:30 分钟

### Phase 2(2-4 周后):用日志训练 thresholdrouter
- 从 new-api 日志抽"query + 用户反馈/正确答案"数据
- 训一个 `thresholdrouter`(MLP,几十 KB)
- 替换 `pick_model()`:
  ```python
  from llmrouter.cli.router_inference import load_router
  router = load_router("thresholdrouter", "configs/thresholdrouter.yaml")
  def pick_model(query):
      return router.route_single({"query": query})["model_name"]
  ```
- 部署成本:1 小时

### Phase 3(1-2 月后):knnrouter
- 类似 Phase 2,但用相似度匹配而不是难度
- 数据要求更多,效果更好

---

## 八、关键技术要点备忘

### 8.1 LLMRouter 是什么

[GitHub: ulab-uiuc/LLMRouter](https://github.com/ulab-uiuc/LLMRouter)

LLM 路由库:对每个 query 选最合适的 LLM,在质量/成本/延迟间折中。

**核心架构**:
- `MetaRouter`:所有路由器的抽象基类
- `BaseTrainer`:训练流程抽象
- 内置 16+ 路由策略(KNN/MLP/GNN/MF/Elo/Router-R1...)
- `llmrouter serve`:OpenAI 兼容服务
- `llmrouter train/infer/chat`:CLI 子命令

### 8.2 预训练模型情况

**唯一官方预训练**:`Router-R1` 系列([HuggingFace ulab-ai](https://huggingface.co/ulab-ai))
- `ulab-ai/Router-R1-Qwen2.5-3B-Instruct`
- `ulab-ai/Router-R1-Qwen2.5-3B-Instruct-Alpha0.9`
- `ulab-ai/Router-R1-Llama-3.2-3B-Instruct`
- `ulab-ai/Router-R1-Llama-3.2-3B-Instruct-Alpha0.9`

⚠️ Router-R1 是 3B 参数的 LLM,**不是小分类器**。延迟和成本都不低。我们当前不需要它。

**其他路由策略(KNN/MLP/GNN/MF/Elo...)**:官方**没有**预训练权重,要自己训。但训练本身只要几秒到几分钟,瓶颈是数据。

### 8.3 无需训练的策略

| 策略 | 怎么工作 | 用法 |
|------|----------|------|
| `rules` | 关键词规则 | **本项目当前使用** |
| `random` | 加权随机 | 压测 |
| `round_robin` | 轮流 | 均匀分布 |
| `llm` | 用 LLM 当裁判 | 智能但贵 |
| `smallest_llm`/`largest_llm` | 永远用最便宜/最强 | baseline |

### 8.4 安装

PyPI 有 `llmrouter-lib`:

```bash
D:\uv\uv.exe pip install "llmrouter-lib[serve]"
```

**为什么用 uv**(CLAUDE.md 强制):系统 pip 走 WindowsApps 的 Store Python,污染环境。uv 用独立的 CPython。

### 8.5 自定义 router 位置

`custom_routers/` 目录有官方预制的免训练 router:
- `randomrouter`:权重随机
- `thresholdrouter`:难度阈值(需要训一个 ~50KB 的小 MLP)

本项目**不直接用 LLMRouter serve**,而是写自己的 `auto_router.py`(因为我们要回灌 new-api,LLMRouter serve 是直接调上游的设计)。

---

## 九、new-api 关键知识

[GitHub: QuantumNous/new-api](https://github.com/QuantumNous/new-api)

- **技术栈**:Go + Gin
- **请求生命周期**:
  ```
  用户请求
    → CORS → Decompress → TokenAuth → RateLimit → Distribute → Controller/Relay
  ```
- **`Distribute` 中间件**([middleware/distributor.go](https://github.com/QuantumNous/new-api/blob/main/middleware/distributor.go)):选 channel/模型的地方
- **`ChannelBaseUrl`**:channel 可配置上游地址(我们的 :3001 利用这点)
- **计费流程**:`PreConsumeBilling` → 调上游 → `PostTextConsumeQuota`(按 `usage` 字段后扣费)
- **响应里的 `usage` 字段**:new-api 据此计算实际费用

**官方文档**:
- [Request Lifecycle](https://deepwiki.com/QuantumNous/new-api/2.1-request-lifecycle)
- [Authentication & Middleware](https://deepwiki.com/QuantumNous/new-api/2.2-authentication-and-middleware)
- [Core Relay System](https://deepwiki.com/QuantumNous/new-api/4-model-management)

---

## 十、本地仓库状态

```
路径:D:\code\LLMRouter\
来源:git clone https://github.com/ulab-uiuc/LLMRouter.git
当前 remote:
  upstream → https://github.com/ulab-uiuc/LLMRouter.git  (fetch/push)
  origin   → (未设置,等用户 fork 后自己加)
```

**用户后续操作**:
1. 在 GitHub 网页 fork `ulab-uiuc/LLMRouter`
2. `git remote add origin https://github.com/<用户名>/LLMRouter.git`
3. `git remote -v` 验证

**注意**:本项目主体是 new-api + :3001 服务,**LLMRouter 仓库本身基本不动**。它只是知识库和未来 ML router 的来源。

---

## 十一、当前 open items / 下一步

- [ ] 部署 :3001 服务到服务器
- [ ] new-api 后台添加 channel "auto-router" 和模型 "auto"
- [ ] 真实流量测试(找一个用户做灰度)
- [ ] 收集日志,准备训练 thresholdrouter 的数据
- [ ] 写部署文档给运维(把"3.1 new-api 后台配置"那部分独立出来)

---

## 十二、相关链接汇总

| 资源 | 链接 |
|------|------|
| LLMRouter GitHub | https://github.com/ulab-uiuc/LLMRouter |
| LLMRouter PyPI | https://pypi.org/project/llmrouter-lib/ |
| LLMRouter 文档 | https://ulab-uiuc.github.io/LLMRouter/ |
| new-api GitHub | https://github.com/QuantumNous/new-api |
| new-api DeepWiki | https://deepwiki.com/QuantumNous/new-api |
| Router-R1 HF | https://huggingface.co/ulab-ai |
| LLMRouter 上游 README | https://github.com/ulab-uiuc/LLMRouter/blob/main/README.md |

---

## 十三、设计决策记录(避免下次重复讨论)

| 决策 | 理由 |
|------|------|
| 用户侧零改动 | 现有 key 体系、计费流程都不动 |
| new-api 单价 auto=0 | 避免双重计费,用户只付真实模型的钱 |
| :3001 回灌 new-api 而不是直连上游 | 复用 new-api 的鉴权/计费/限流,不在 :3001 重复实现 |
| 不直接用 `llmrouter serve` | 它是"直接调上游"的设计,跟我们"回灌 new-api"的架构冲突 |
| 初期用 rules 而非 ML router | 无需训练、零数据要求、覆盖 80% 场景 |
| 不动 new-api Go 代码 | 用 channel 配置绕开,降低维护负担 |
| :3001 用 FastAPI 不用 Flask | 原生支持 async/streaming,跟 new-api 性能匹配 |
| `rules` 关键词分类规则 | 基于"代码/推理/默认"三桶,简单可解释 |

---

**最后:这是会话迁移文档,下次开新会话先让 Claude 读这个文件。**