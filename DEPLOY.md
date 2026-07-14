# AutoRouter 部署文档

> FastAPI 伪 channel:路由 LLM 请求到多模型(基于 ML bundle + 启发式 + 策略链)。

---

## 架构一览

```
浏览器 / client
      │
      ▼
   ┌────────────────────────────────────────────┐
   │       AutoRouter  (:3001)                  │
   │  ┌─────────────────────────────────────┐   │
   │  │  /v1/chat/completions (代理)         │   │
   │  │  /v1/messages       (代理)          │   │
   │  │  /v1/responses      (代理)          │   │
   │  │  + /api/config/*    (管理 API)      │   │
   │  │  + /api/route/preview (调试)        │   │
   │  └─────────────────────────────────────┘   │
   │  ──── 路由管道 ────                        │
   │   ML bundle(390 维特征 / LightGBM+ONNX)  │
   │   启发式 band(classify_with_messages)     │
   │   策略链(5 步):chitchat_only → 抱怨升档    │
   │           → 抗降档 → capability_gate → LC │
   │  ────────────────────────                  │
   │  异步日志(QueueHandler → 1 listener 线程)  │
   └────────────────────────────────────────────┘
      │  POST /v1/chat/completions (含 model 字段)
      ▼
   ┌────────────────────────────────────────────┐
   │       new-api  (:3000)  ← 上游             │
   └────────────────────────────────────────────┘
```

每条请求流:`user → autorouter → 路由决策 → 改写 body.model → 转发到 new-api → 回灌`。

---

## 一键启动(开发模式)

```bash
# 1. 安装依赖(基础版,不含 ML extra)
uv sync

# 2. 安装 ML 依赖(可选;不装也能跑,自动降级到 rule/heuristic)
uv sync --extra ml

# 3. 启动(fake_newapi 在另一终端,如需离线测试)
uv run uvicorn scripts.fake_newapi:app --host 127.0.0.1 --port 3000 --workers 4 &

# 4. 启动主服务(单 worker,够本地开发)
uv run uvicorn app.channel:app --host 0.0.0.0 --port 3001
```

启动后:
- 管理界面:`http://<host>:3001/`(SPA)
- 管理 API:`http://<host>:3001/api/config`(JSON)
- 实时路由测试:管理界面 → 「策略」tab → LiveTest
- 日志查看:管理界面 → 「日志」tab
- 健康检查:`curl http://<host>:3001/health`

---

## 关键配置项(`config/*.yaml`)

| 文件 | 含义 |
|---|---|
| `connection.yaml` | 本服务监听 + 上游 new-api base_url + API key |
| `strategies.yaml` | 路由策略(single / rule / classifier 三种 kind) |
| `policy.yaml` | 策略链开 + 阈值(anti_downgrade window / LC floor / 等) |
| `observability.yaml` | **只一项**:`log_dir`(默认 `./log`) |
| `models.yaml` | 模型注册表(能力元数据,capability_gate 按名查) |
| `ml.yaml` | ML bundle 路径 / 置信度阈值 |

策略 kind 命名:
- `single` — 固定一个模型(直接路由,无分类)
- `rule` — 启发式 band(长度+代码),确定性路由
- `classifier` — ML 分类器输出 idx 0..3,ML 不可用自动降级 `rule`

`static` / `heuristic` 是 `single` / `classifier` 的旧名别名,**保留兼容不推荐使用**。

---

## 生产部署

### 内存需求(主要:ML bundle)

| Worker 数 | 内存预算 | 备注 |
|---|---|---|
| 1  | ~500 MB | 单进程,模型完整加载 |
| 4  | ~1.5 GB | 推荐甜区 |
| 8  | ~3 GB | 已经测过的稳定上限 |
| 16 | ~6 GB | 多机器或巨型 box |

每个 worker 加载一份完整 ML bundle(LightGBM + BGE/ONNX + sklearn + MLP),约 **300-400 MB**。
**不要单机堆 16+ worker**,改用多机 + 负载均衡。

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

生产 replace:`connection.yaml.base_url = "http://<real-new-api-host>:3000"`

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

## Daily 日志滚动

`config/observability.yaml:log_dir` 决定日志目录(默认 `./log`),从 worker 进程启动时按本地日期按文件切:

- 今天:`auto_router-2026-07-14.log`
- 历史:`auto_router-2026-07-13.log`(永不删)

**异步日志模型**:
```
app 线程(emit) → QueueHandler → Queue → 1 后台 listener 线程 → DailyFileHandler → 文件
```

**好处**:应用线程不被文件 I/O 同步阻塞,**提升高并发吞吐 ~47%**(200 concurrent 时 RPS 106 → 156)。
**不影响**:日志内容一字不差(同一 formatter,同一字段顺序)。
**所有 worker 写同一文件**:靠 Windows `_O_APPEND` 的原子 append,不互锁。多 worker 都能写,但偶发竞争;若想严格分离,可改成 `auto_router-{date}-w{pid}.log`(每进程一份,代价是日志分散)。

UI 查看:管理界面 → 「日志」tab → 左侧文件列表(今天的带 badge),点选看全文(无 tail 截断)。

---

## 关键调参(改 yaml,POST /api/reload 即时生效)

### `policy.yaml`

| 参数 | 默认 | 含义 |
|---|---|---|
| `anti_downgrade.window_seconds` | 600 | 同一会话 N 秒内禁降档(护上游 KV cache) |
| `chitchat_only.enabled` | true | R0 只留给真闲聊(招呼/自我介绍/天气/客套),否则升 R1 |
| `large_context_floor.t3_floor_tokens` | 150000 | ≥15万 token → 强制顶档(超大上下文) |
| `large_context_floor.t2_floor_tokens` | 80000 | ≥8万 token → 强制次高档 |
| `large_context_floor.context_window` | 262000 | 对齐最小 context 窗口(给 ratio 用) |

### `ml.yaml`

| 参数 | 默认 | 含义 |
|---|---|---|
| `confidence_threshold` | 0.5 | ML max prob < 此值 → 回退 fallback_idx |
| `confidence_fallback_idx` | -1 | =rules_count-1(顶档);可改 1 = R1 |

`confidence_fallback_idx = -1` 倾向质量(不确定就上顶档),改 `1` 倾向省钱(退到 R1)。
改前先看 `log/auto_router-*.log` 里 `src=ml` 的 conf 分布,记下大多数区间再调。

### `strategies.yaml`

典型 4 tier auto 配置(已采纳模板):

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

---

## 安全

- 默认监听 `0.0.0.0:3001`,需要外部访问时由网络层(防火墙 / nginx)兜底
- 管理 API(`/api/config/*`)对所有来源开放;**生产建议**反向代理层加 IP 白名单
- 客户端鉴权靠 `Authorization` 头透传到 new-api,本身无 token 验证
- 日志文件不记录原始 prompt(只截前 80 字符 preview),不在磁盘留 PII

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

---

## 文件地图

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

config/
  connection.yaml   # host/port + 上游 new-api
  strategies.yaml   # 路由策略
  policy.yaml       # 策略链开 + 阈值
  observability.yaml# log_dir
  models.yaml       # 模型注册表(能力元数据)
  ml.yaml           # ML bundle 配置

web/                # Svelte 4 SPA(vite 构建 → dist/)
  src/App.svelte    # 主壳 + tab 列表
  src/api.ts        # 后端 client + 类型
  src/tabs/
    ConnectionTab.svelte
    ModelConfigTab.svelte  # 模型注册表 / 拉上游
    StrategiesTab.svelte  # 路由策略 + LiveTest
    MlTab.svelte
    PolicyTab.svelte
    ObservabilityTab.svelte  # 日志查看器

scripts/
  fake_newapi.py    # 离线压测用,模拟 new-api
  load_test.py      # 100 并发压测
  load_test_200.py  # 200 并发压测
  req_*.json        # 各种 curl 抓的请求样本(开发参考)

models/
  v4.2_phase3_inference/  # OpenSquilla ML bundle(预训练权重,只读)

log/                # 运行时:今天 + 历史 daily 文件,从不删
```
