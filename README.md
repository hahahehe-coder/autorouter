[简体中文](README.md) | [English](README.en.md)

# AutoRouter

> **让合适的任务，自动交给合适的模型。**

**简单任务少花钱，复杂任务不将就。**

还在根据任务难度手动切换模型？

日常问答没必要每次都调用最昂贵的模型，编程、复杂推理和长上下文任务也不该交给能力不足的模型。但模型越来越多，靠自己反复判断任务难度、切换模型，不仅麻烦，也很难兼顾效果和成本。

AutoRouter 就是为了解决这个问题。

它借鉴 [OpenSquilla](https://github.com/opensquilla/opensquilla) 的模型路由设计，结合 ML 分类、启发式规则和后处理策略，分析每次请求的内容与复杂程度，自动选择更合适的模型：

- 日常问答优先使用快速、实惠的模型
- 编程任务自动切换到更擅长代码的模型
- 复杂推理调用能力更强的模型
- 图片和长上下文请求自动匹配具备相应能力的模型
- ML 不可用时自动回退到启发式路由，不影响正常请求

你不再需要提前判断任务有多难，也不用在多个模型之间来回切换。

> **你只管提问，模型选择交给 AutoRouter。**

## 少花不必要的钱，也不让困难任务降级

如果所有请求都调用最强模型，当然省心，但大量简单任务会产生不必要的费用；如果始终使用便宜模型，遇到编程、推理和复杂分析时，回答效果又可能不够理想。

AutoRouter 会动态分配模型：让高性价比模型处理它擅长的简单任务，让强模型专注于真正困难的问题，在使用效果和 API 成本之间取得更合理的平衡。

**省掉的是不必要的模型成本，保留的是复杂任务真正需要的能力。**

## API 站长和普通用户都能使用

| 使用者 | 如何接入 | 能得到什么 |
|---|---|---|
| **API 中转站站长** | 将 AutoRouter 部署到服务器，再增加一个渠道 | 把多个不同价格、不同能力的模型组合成统一的自动选模入口，无需改变用户习惯 |
| **普通用户** | 在本机启动 AutoRouter，把聊天客户端或开发工具的 API 地址指向本地服务 | 继续使用原来的客户端，日常问答、写代码和复杂分析都不必再手动换模型 |

一次配置，之后每次请求都能自动选择。

## 一个入口，连接多个模型和上游

AutoRouter 位于客户端和上游 LLM 服务之间。客户端只需要连接 AutoRouter，后续请求由它完成分析、选模和转发。

```text
浏览器 / LLM 客户端 / API 中转站
              │
              ▼
       ┌─────────────────┐
       │   AutoRouter    │
       │ ML + 规则 + 策略链 │
       └─────────────────┘
          │      │      │
          ▼      ▼      ▼
       上游 A  上游 B  上游 C
       便宜模型 代码模型 强力模型
```

每条请求的处理流程：

```text
接收请求 → 分析任务 → 选择档位 → 检查图片/上下文等能力 → 改写配置字段 → 转发上游
```

实际转发时，除策略明确配置的 `model`、`max_tokens`、`system`、`thinking` 等覆盖字段外，其他请求参数和认证头保持不变；上游的响应状态、响应头和响应字节直接返回客户端。

## 核心能力

- **智能模型路由**：基于 OpenSquilla v4.2 bundle（LightGBM + BGE/ONNX，390 维特征）判断任务难度
- **三种路由模式**：`single` 固定模型、`rule` 启发式分类、`classifier` ML 分类
- **多层路由保护**：置信度回退、闲聊限制、抱怨升档、会话抗降档、模型能力检查和大上下文升档
- **多上游供应商**：按模型的 `upstream` 标签自动选择目标供应商
- **多端点支持**：`/v1/chat/completions`、`/v1/messages`、`/v1/responses`，其他 `/v1/*` 请求透明转发
- **模型能力注册表**：集中管理视觉支持和上下文窗口，策略引用无效模型时拒绝保存
- **可视化管理后台**：在浏览器中配置连接、模型、策略、ML、后处理和日志，保存后立即生效
- **可靠降级**：ML 依赖或模型不可用时自动回退到启发式路由
- **异步每日滚动日志**：路由决策写入按日期区分的日志文件

**一个入口，多个模型，自动选择。**

## 快速开始

### 1. 安装并启动

```bash
# 纯规则路由（rule 模式）
uv sync

# 使用 ML 分类路由（classifier 模式）
uv sync --extra ml

# 普通用户仅在本机使用
uv run uvicorn app.channel:app --host 127.0.0.1 --port 3001

# 服务器部署时改为监听所有网卡
# uv run uvicorn app.channel:app --host 0.0.0.0 --port 3001
```

启动后打开管理界面：`http://127.0.0.1:3001/`。仓库已经包含预构建的 `web/dist`，普通用户不需要安装 Node.js。

### 2. 配置上游供应商

推荐在管理界面的「连接」页添加供应商。每个供应商包含以下信息：

| 字段 | 作用 |
|---|---|
| 供应商名称 | AutoRouter 内部使用的唯一标识，例如 `opencode`、`openrouter`；模型通过这个名称绑定上游 |
| `base_url` | 上游 API 地址，可以填写 `https://api.example.com` 或 `https://api.example.com/v1` |
| `api_key` | 仅供管理后台从该供应商的 `/v1/models` 拉取模型；留空时跳过该供应商 |
| `default` | 未指定 `upstream` 的模型以及无法识别模型来源的请求使用哪个供应商 |

也可以直接编辑 `config/connection.yaml`：

```yaml
server:
  host: 127.0.0.1
  port: 3001

providers:
  default: opencode
  opencode:
    base_url: https://opencode.ai/zen/v1
    api_key: "<OpenCode Zen API Key>"
  openrouter:
    base_url: https://openrouter.ai/api/v1
    api_key: "<OpenRouter API Key>"

admin:
  user: admin
  password: ""  # 留空关闭管理登录；公开部署前请设置强密码
```

> **认证说明：**`providers.*.api_key` 不会被用于推理。AutoRouter 会把客户端请求中的 `Authorization`、`x-api-key` 及其他协议请求头透传给最终选中的供应商，因此客户端或前置 API 站需要提供目标供应商能够接受的认证信息。

上面的配置分别使用了 [OpenCode Zen](https://opencode.ai/docs/zen/) 和 [OpenRouter](https://openrouter.ai/docs/api/reference/overview) 的 OpenAI 兼容地址。供应商名称只是 AutoRouter 内部标识，可以自行修改，但模型的 `upstream` 必须使用相同名称。

### 3. 拉取模型并绑定供应商

进入管理界面的「模型」页，点击「拉取上游」：

> ⚠️ **提示：**如果上游提供的模型较多，建议先在上游筛选后再点击「拉取上游」，否则可能一次拉取大量模型。例如 OpenRouter 一次可能返回 **400+ 个模型**。

1. AutoRouter 会查询所有填写了 `api_key` 的供应商。
2. 拉到的模型会自动记录来源供应商，即模型的 `upstream`。
3. 为模型补充 `supports_vision` 和 `context_window`，供图片和长上下文策略判断。
4. 保存模型配置。

如果供应商不支持 `/v1/models`，可以在界面中手动添加，或直接编辑 `config/models.yaml`：

```yaml
cheap-model:
  supports_vision: false
  context_window: 128000
  upstream: opencode

code-model:
  supports_vision: false
  context_window: 200000
  upstream: openrouter

strong-model:
  supports_vision: true
  context_window: 1000000
  upstream: openrouter
```

`upstream` 必须与 `connection.yaml` 中的供应商名称一致；不填写时，该模型会使用 `providers.default`。

### 4. 创建自动路由策略

进入「策略」页创建一个策略，例如 `auto`，再为不同难度选择刚刚注册的模型。首次使用建议先选 `rule` 模式；安装了 ML 依赖后可改为 `classifier`。

对应的 `config/strategies.yaml` 示例：

```yaml
auto:
  kind: rule
  rules:
  - model: cheap-model   # R0：闲聊等简单任务
    max_tokens: 4096
    thinking: off
  - model: cheap-model   # R1：普通任务
  - model: code-model    # R2：编程等较难任务
    thinking: medium
  - model: strong-model  # R3：复杂推理任务
    thinking: high
```

请求时将 `model` 填为策略名称 `auto`。AutoRouter 会先选择档位，再把它改写成对应的真实模型名称并转发到该模型绑定的供应商。

### 5. 发送第一个请求

下面以 OpenAI 兼容的 Chat Completions 请求为例。使用 `-i` 可以同时查看 `X-Auto-Routed-To` 等路由结果响应头：

```bash
curl -i http://127.0.0.1:3001/v1/chat/completions \
  -H "Authorization: Bearer <推理请求使用的 API Key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"写一个归并排序并解释时间复杂度"}]}'
```

还可以通过以下入口检查运行状态：

- 健康检查：`http://127.0.0.1:3001/health`
- 路由测试：管理界面 → 「策略」→ LiveTest（只运行路由流程，不调用上游）
- 路由日志：管理界面 → 「日志」

### 开发与发布检查

只有修改管理界面源码时才需要安装 Node.js 并重新构建：

```bash
cd web
npm ci
npm run check
npm run build

# 回到仓库根目录运行后端回归测试
cd ..
uv run --no-sync python -m unittest discover -s tests -v
```

---

## 关键概念

### 路由模式

| Kind | 含义 |
|---|---|
| `single` | 一个 rule、固定模型,不分类 |
| `rule` | 启发式 band(长度+代码),确定性,idx 0-3 → rules[idx] |
| `classifier` | ML 分类器输出 idx 0-3,ML 不可用自动降级到 `rule` |

### 策略链（默认 6 步按序执行）

```
confidence_gate → chitchat_only → complaint → anti_downgrade
                  → capability_gate → large_context_floor
```

每步检查 idx,可能升档/降档/不改。每步独立可关(`config/policy.yaml`)。

### R0 只留给闲聊

`chitchat_only` 默认开启:除非文本是招呼/自我介绍/天气/客套,否则从 R0 升 R1。这条规则保护 R0 的 `thinking: off` 快路径不被短但复杂的问题(例如「为什么天空是蓝的?」)拖累。

---

## 配置(`config/*.yaml`)

| 文件 | 含义 |
|---|---|
| `connection.yaml` | 本服务监听 + 多上游供应商列表(default + name → base_url/api_key) + admin 登录 |
| `strategies.yaml` | 路由策略(single / rule / classifier 三种 kind) |
| `policy.yaml` | 策略链开 + 阈值(anti_downgrade window / LC floor / 等) |
| `observability.yaml` | **只一项**:`log_dir`(默认 `./log`) |
| `models.yaml` | 模型注册表(能力元数据,capability_gate 按名查) |
| `ml.yaml` | ML bundle 路径 / 置信度阈值 |

### 关键调参(`POST /api/reload` 即时生效)

**`policy.yaml`**

| 参数 | 默认 | 含义 |
|---|---|---|
| `anti_downgrade.window_seconds` | 600 | 同一会话 N 秒内禁降档(护上游 KV cache) |
| `chitchat_only.enabled` | true | R0 只留给真闲聊(招呼/自我介绍/天气/客套),否则升 R1 |
| `large_context_floor.t3_floor_tokens` | 150000 | ≥15万 token → 强制顶档(超大上下文) |
| `large_context_floor.t2_floor_tokens` | 80000 | ≥8万 token → 强制次高档 |
| `large_context_floor.context_window` | 262000 | 对齐最小 context 窗口(给 ratio 用) |

**`ml.yaml`**

| 参数 | 默认 | 含义 |
|---|---|---|
| `confidence_threshold` | 0.5 | ML max prob < 此值 → 回退 fallback_idx |
| `confidence_fallback_idx` | -1 | =rules_count-1(顶档);可改 1 = R1 |

`confidence_fallback_idx = -1` 倾向质量(不确定就上顶档),改 `1` 倾向省钱(退到 R1)。
改前先看 `log/auto_router-*.log` 里 `src=ml` 的 conf 分布,记下大多数区间再调。

**`strategies.yaml`** — 典型 4 tier auto 配置:

```yaml
auto:
  kind: classifier  # 或 rule
  rules:
  - model: MiniMax-M3       # R0 trivial
    max_tokens: 4096
    thinking: off
  - model: MiniMax-M3       # R1 medium
  - model: kimi-for-coding  # R2 code
    thinking: medium
  - model: glm-5.2          # R3 heavy
    thinking: high
```

每条 rule 的 model **必须在 models.yaml 注册表里有**(后端 validate 强制)。

### 管理后台登录(Basic Auth)

`config/connection.yaml` 加 `admin` 块:

```yaml
admin:
  user: "admin"
  password: ""           # 留空 = 关闭登录(开发模式默认)
```

启用后,所有 `/api/*` 设置端点要求 `Authorization: Basic base64(user:pass)`,`/v1/*` 转发完全不受影响,SPA 启动后弹 LoginScreen,凭证存 localStorage,toolbar 有「退出」按钮。

`/v1/*` 是否对公网开放由部署者决定。普通用户只在本机使用时建议监听 `127.0.0.1`;公开部署时请配合防火墙或外部网关控制访问。

⚠️ **生产建议**:yaml 里是明文密码,推荐改成 hash 校验(`hashlib.pbkdf2_hmac`)。

---

## Daily 日志滚动

`config/observability.yaml:log_dir` 决定日志目录(默认 `./log`),从 worker 进程启动时按本地日期按文件切:

- 今天:`auto_router-2026-07-14.log`
- 历史:`auto_router-2026-07-13.log`(永不删)

**异步日志模型**:
```
app 线程(emit) → QueueHandler → Queue → 1 后台 listener 线程 → DailyFileHandler → 文件
```

**好处**:应用线程不被文件 I/O 同步阻塞,**提升高并发吞吐 ~47%**(200 concurrent 时 RPS 106 → 156)。
**所有 worker 写同一文件**:靠 Windows `_O_APPEND` 的原子 append,不互锁。若想严格分离,可改成 `auto_router-{date}-w{pid}.log`(每进程一份,代价是日志分散)。

UI 查看:管理界面 → 「日志」tab → 左侧文件列表(今天的带 badge),点选看全文(无 tail 截断)。

---

## 生产部署

### 启动命令(推荐 4-8 workers)

```bash
uv run uvicorn app.channel:app \
  --host 0.0.0.0 \
  --port 3001 \
  --workers 4 \
  --log-level info
```

外加 fake 隔离(只在本机测试时):

```bash
uv run uvicorn scripts.fake_newapi:app \
  --host 127.0.0.1 \
  --port 3000 \
  --workers 4
```

生产 replace `connection.yaml` 的 `providers` 段,把每个供应商的 `base_url` / `api_key` 改成真上游。

### 内存需求(主要:ML bundle)

每个 worker 加载一份完整 ML bundle(LightGBM + BGE/ONNX + sklearn + MLP),约 **300-400 MB**。

| Worker 数 | 内存预算 | 备注 |
|---|---|---|
| 1  | ~500 MB | 单进程,模型完整加载 |
| 4  | ~1.5 GB | 推荐甜区 |
| 8  | ~3 GB | 已经测过的稳定上限 |
| 16 | ~6 GB | 多机器或巨型 box |

**不要单机堆 16+ worker**,改用多机 + 负载均衡。

### 性能基准(已实测)

| 配置 | 并发 | RPS | p50 | p99 |
|---|---|---|---|---|
| 1 worker | 100 | 31.6 | 2.0s | 3.0s |
| 2 workers | 100 | 46.6 | 1.8s | 2.6s |
| 4 workers | 100 | 79.4 | 0.9s | 1.2s |
| **8 workers + 异步日志** | **100** | **156.9** | **0.55s** | **0.61s** |
| **8 workers + 异步日志** | **200** | **156.3** | **1.14s** | **1.26s** |

测试方式:`scripts/load_test.py`(100) / `scripts/load_test_200.py`(200)。
注意这是**单客户端**压测,真实生产多个上游 + 多客户端并发通常更高。

---

## 常见问题

| 现象 | 排查 |
|---|---|
| 日志没出来 | 检查 `log_dir` 是否可写;查看 listener 线程是否在(`/health` 看 `ml.status=ready`) |
| 路由全去 glm-5.2 | 看 `log/auto_router-*.log` 里 `conf=` 多大,可能 ML 持续低置信走 fallback |
| 转发 502/404 | 验证 `connection.yaml.providers.*.base_url`;确认对应供应商有该模型 |
| 内存涨不停 | 每个 worker 300-400 MB 是正常;不停涨说明 listener 卡了或 reload 死循环 |
| 单 query 延迟 10s+ | 多数 8 worker 跑 200 并发的情况;减并发或加 worker |
| 改 yaml 不生效 | POST `/api/reload`;观察 stdout "Reloaded. N strategies" |
| 进 SPA 弹 LoginScreen | 后端启用了 `admin.password`;填对用户名密码或清空 password |

---

## 文件地图

```
app/                   # 核心 Python 模块
  channel.py           # FastAPI 入口 + 路由 + 日志配置 + admin 中间件
  router.py            # 顶层路由函数
  policy.py            # 6 步策略链
  heuristic.py         # 启发式 band
  ml_router.py         # ML 路由层(惰性)
  ml/                  # ML 内部(移植自 opensquilla)
config/                # 6 个 yaml 配置(connection/strategies/policy/observability/models/ml)
web/                   # Svelte SPA(管理界面)
scripts/               # 压测 + 离线 mock + admin 响应检查
tests/                 # 配置事务、多供应商和端点透传回归测试
models/                # OpenSquilla 预训练权重(只读)
log/                   # 运行时 daily 日志(永不删)
```

`app/` 详细:
```
app/
  channel.py        # FastAPI 入口 + 所有路由 + 日志配置
  router.py         # 顶层路由(route() function)
  policy.py         # 策略链(chitchat_only / complaint / anti_downgrade / capability / LC)
  heuristic.py      # 启发式分类(classify / classify_with_messages / context_signals)
  ml_router.py      # ML 路由层(惰性加载 bundle,asyncio.to_thread 推断)
  ml/               # ML 内部模块(从 opensquilla 移植,自包含)
    bundle.py       # 加载 artifacts
    core.py         # InferenceCore(特征 → 头 → fuse)
    artifacts.py    # V3FeatureRuntime
    bge_onnx.py     # BGE-small-zh ONNX
    ensemble.py     # LGBM + MLP head
    v4_features.py  # 390 维特征
    features.py     # V3 特征工具
    types.py        # dataclass 数据结构
    heads.py        # 单 head 抽象
    trajectory.py   # trajectory 累积
  observe.py        # 决策 → logger.info(structured)
  session.py        # SessionStore(anti_downgrade 用)
  config.py         # 多 yaml 加载、合并、跨文件校验、热重载
```

`config/`:
```
connection.yaml   # host/port + 上游 + admin 登录
strategies.yaml   # 路由策略
policy.yaml       # 策略链开 + 阈值
observability.yaml# log_dir
models.yaml       # 模型注册表(能力元数据)
ml.yaml           # ML bundle 配置
```

`web/`:
```
src/App.svelte    # 主壳 + tab 列表 + Login 门控 + Logout
src/api.ts        # 后端 client + 类型(带 Authorization header)
src/tabs/
  ConnectionTab.svelte
  ModelConfigTab.svelte  # 模型注册表 / 拉上游
  StrategiesTab.svelte  # 路由策略 + LiveTest
  MlTab.svelte
  PolicyTab.svelte
  ObservabilityTab.svelte  # 日志查看器
```

`scripts/`:
```
fake_newapi.py          # 离线 mock 上游(仅测试)
load_test.py            # 100 并发压测
load_test_200.py        # 200 并发压测
check_admin_response.py # 验证 /api/config 不回显 password
req_*.json              # curl 测试体(开发参考)
```

---

## License

本项目代码以 [MIT License](LICENSE)(`Copyright (c) 2026 hahahehe`)发布。

项目内嵌的 ML 路由能力来自 [OpenSquilla](https://github.com/opensquilla/opensquilla) 的
SquillaRouter V4 Phase 3 inference bundle(`models/v4.2_phase3_inference/`,遵循上游 MIT 协议)。
BGE 嵌入模型来自 [FlagOpen/FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding)
(`BAAI/bge-small-zh-v1.5`)。

完整三方依赖列表见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
