# AutoRouter

> FastAPI 伪 channel:把 LLM 请求按内容智能路由到多个模型(基于 ML 路由 + 启发式 + 策略链)。

---

## 这是什么

部署在 new-api 和客户端之间,接收 `POST /v1/{chat,messages,responses}`,按请求内容选择下游模型,改写 `body.model` 后转发到上游 new-api。

**典型用例**:多个模型(便宜/中等/最强)的池子里,把 trivial 查询落到便宜模型、heavy/code 落到强模型,自动省成本。

### 核心特性

- **ML 路由层**:基于 OpenSquilla v4.2 bundle(LightGBM + BGE/ONNX,390 维特征),即开即用,ML 不可用时自动降级到启发式
- **3 种路由模式**:`single`(固定模型)、`rule`(启发式 band)、`classifier`(ML)。旧名 `static`/`heuristic` 作别名
- **5 步策略链**:chitchat_only / 抱怨升档 / 抗降档(会话锁高档)/ capability_gate(图片/上下文约束升档)/ large_context_floor(超大上下文强制高档)
- **模型注册表**:`models.yaml` 集中标能力(supports_vision/context_window),capability_gate 按名查;策略 rule 的 model 必须在注册表里才有效(后端强制)
- **三端点支持**:`/v1/chat/completions` / `/v1/messages` / `/v1/responses`,每端点字段映射(thinking 用各自的 endpoint-native 名字)
- **每日滚动日志**:`auto_router-YYYY-MM-DD.log` 按本地日期切,异步 write(QueueHandler + 后台 listener),永不删
- **管理 UI**:Svelte SPA,通过 `/api/config/*` 编辑所有 yaml 配置,reload 即时生效;**Basic Auth** 守 `/api/*`(密码空 = 关闭),`/v1/*` 转发完全开放

### 架构

```
浏览器 / new-api 客户端
      │
      ▼
   ┌─────────────────┐
   │   AutoRouter    │── /v1/* 转发到
   │   (:3001)       │── 上游 :3000 (new-api)
   │                 │
   │  ML bundle + 启发式 + 策略链 │
   └─────────────────┘
      ▲
      │  /api/* 管理 API(Basic Auth 守门)
   管理界面 (Svelte SPA)
```

每条请求流:`user → autorouter → 路由决策 → 改写 body.model → 转发到 new-api → 回灌`。

---

## 快速开始

```bash
# 1. 安装
uv sync                          # 基础(纯 rule/heuristic 模式)
uv sync --extra ml               # 含 ML(开启 classifier 模式必须)

# 2. 编辑 config/connection.yaml
#   new_api.base_url: 上游地址(如 http://127.0.0.1:3000)
#   admin.password:  留空 = 关闭登录;填上 = 启用 Basic Auth

# 3. 启动
uv run uvicorn app.channel:app --host 0.0.0.0 --port 3001
```

启动后:
- 管理界面:`http://<host>:3001/`
- 健康检查:`curl http://<host>:3001/health`
- 实时路由测试:管理界面 → 「策略」tab → LiveTest(不调上游,只跑路由管道)
- 日志查看:管理界面 → 「日志」tab

### 第一次配置流程

1. **「连接」tab**:填上游 `new_api.base_url` 和 `api_key`,可选填 `admin.password` → 保存
2. **「模型」tab**:点击「拉取上游」拉模型列表 → 给每个模型勾选 supports_vision / 填 context_window → 保存
3. **「策略」tab**:增删策略、配置每个 strategy 的 kind 和 4 档 rules(选刚才注册的模型) → 保存
4. **「ML」tab**:选 classifier 模式必设;rule 模式可空
5. **「后处理」tab**:策略链开关 + 阈值

---

## 关键概念(60 秒读完)

### 路由模式

| Kind | 含义 |
|---|---|
| `single` | 一个 rule、固定模型,不分类 |
| `rule` | 启发式 band(长度+代码),确定性,idx 0-3 → rules[idx] |
| `classifier` | ML 分类器输出 idx 0-3,ML 不可用自动降级到 `rule` |

### 策略链(默认 5 步按序执行)

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
| `connection.yaml` | 本服务监听 + 上游 new-api base_url + API key + admin 登录 |
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

生产 replace `connection.yaml` 的 `new_api.base_url` 指真上游。

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
| 转发 502/404 | 验证 `connection.yaml.base_url`;确认 new-api 上有对应模型 |
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
  policy.py            # 5 步策略链
  heuristic.py         # 启发式 band
  ml_router.py         # ML 路由层(惰性)
  ml/                  # ML 内部(移植自 opensquilla)
config/                # 6 个 yaml 配置(connection/strategies/policy/observability/models/ml)
web/                   # Svelte SPA(管理界面)
scripts/               # 压测 + 离线 mock + admin 响应检查
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
connection.yaml   # host/port + 上游 new-api + admin 登录
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
fake_newapi.py          # 离线 mock new-api
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