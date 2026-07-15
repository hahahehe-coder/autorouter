[简体中文](README.md) | [English](README.en.md)

# AutoRouter

> **Automatically match every task with the right model.**

**Spend less on simple tasks without compromising on hard ones.**

Still switching models manually whenever the task changes?

Everyday questions do not need the most expensive model, while coding, complex reasoning, and long-context tasks should not be left to an underpowered one. As the number of available models grows, judging task difficulty and switching models by hand becomes tedious—and makes it hard to balance quality and cost.

AutoRouter solves that problem.

Inspired by the routing design of [OpenSquilla](https://github.com/opensquilla/opensquilla), AutoRouter combines ML classification, heuristic rules, and post-routing policies to analyze each request and automatically choose a suitable model:

- Use fast, affordable models for everyday questions
- Route coding tasks to models that are better at code
- Use stronger models for complex reasoning
- Match image and long-context requests with models that support them
- Fall back to heuristic routing when ML is unavailable

You no longer have to estimate task difficulty or switch between models yourself.

> **You ask the question. AutoRouter picks the model.**

## Cut unnecessary costs without downgrading difficult tasks

Sending every request to the strongest model is convenient, but wastes money on simple work. Always using the cheapest model lowers costs, but can hurt quality on coding, reasoning, and complex analysis.

AutoRouter allocates models dynamically: cost-effective models handle the tasks they do well, while stronger models focus on work that actually needs them. The result is a more practical balance between API cost and output quality.

**Pay less where you can, and keep the capability where it matters.**

## Built for both API operators and individual users

| User | How to integrate | What you get |
|---|---|---|
| **API gateway operator** | Deploy AutoRouter on a server and add it as another channel | Combine models with different prices and capabilities behind one automatic-routing entry point without changing user habits |
| **Individual user** | Run AutoRouter locally and point your chat client or developer tool at it | Keep using your existing client without manually changing models for chat, coding, or complex analysis |

Configure it once, then let every request choose automatically.

## One endpoint, multiple models and providers

AutoRouter sits between your client and upstream LLM services. The client connects to AutoRouter, which analyzes, routes, and forwards each request.

```text
Browser / LLM client / API gateway
                │
                ▼
         ┌─────────────────┐
         │   AutoRouter    │
         │ ML + rules + policies │
         └─────────────────┘
            │      │      │
            ▼      ▼      ▼
       Provider A  B      C
       Budget      Code   Strong
```

Request flow:

```text
Receive → analyze → select tier → check image/context capabilities → apply configured overrides → forward
```

Apart from fields explicitly overridden by the selected rule—such as `model`, `max_tokens`, `system`, and `thinking`—request parameters and authentication headers are preserved. The upstream status code, end-to-end response headers, and raw response bytes are returned to the client.

## Core capabilities

- **Intelligent model routing:** Uses the OpenSquilla v4.2 bundle (LightGBM + BGE/ONNX, 390 features) to estimate task difficulty
- **Three routing modes:** `single` for a fixed model, `rule` for heuristic classification, and `classifier` for ML classification
- **Multi-layer routing safeguards:** Confidence fallback, chitchat restriction, complaint upgrade, session anti-downgrade, capability checks, and large-context upgrades
- **Multiple upstream providers:** Selects a provider from the routed model's `upstream` tag
- **Multiple API endpoints:** Supports `/v1/chat/completions`, `/v1/messages`, and `/v1/responses`, with transparent forwarding for other `/v1/*` requests
- **Model capability registry:** Centralizes vision and context-window metadata and rejects strategies that reference unregistered models
- **Web management UI:** Configure connections, models, strategies, ML, post-processing, and logs in a browser; changes take effect after saving
- **Graceful fallback:** Automatically falls back to heuristic routing when ML dependencies or models are unavailable
- **Asynchronous daily logs:** Writes routing decisions to date-based log files

**One endpoint. Multiple models. Automatic selection.**

## Quick start

### 1. Install and start

```bash
# Rule-based routing
uv sync

# ML classifier routing
uv sync --extra ml

# Local use only
uv run uvicorn app.channel:app --host 127.0.0.1 --port 3001

# Listen on all interfaces for a server deployment
# uv run uvicorn app.channel:app --host 0.0.0.0 --port 3001
```

Open `http://127.0.0.1:3001/` after startup. A prebuilt `web/dist` is included, so regular users do not need Node.js.

### 2. Configure upstream providers

The recommended approach is to add providers from the **Connections** page in the management UI. Each provider has these fields:

| Field | Purpose |
|---|---|
| Provider name | A unique internal identifier such as `opencode` or `openrouter`; models use this name to select their upstream |
| `base_url` | The upstream API URL, such as `https://api.example.com` or `https://api.example.com/v1` |
| `api_key` | Used only by the management UI to fetch `/v1/models` from this provider; providers with an empty key are skipped |
| `default` | The provider used when a model has no `upstream` tag or the model source cannot be determined |

You can also edit `config/connection.yaml` directly:

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
  password: ""  # Empty disables admin login; set a strong password before public deployment
```

> **Authentication:** `providers.*.api_key` is not used for inference. AutoRouter forwards the incoming `Authorization`, `x-api-key`, and other protocol headers to the selected provider. Your client or front-facing API gateway must therefore send authentication accepted by the destination provider.

The example uses the OpenAI-compatible endpoints from [OpenCode Zen](https://opencode.ai/docs/zen/) and [OpenRouter](https://openrouter.ai/docs/api/reference/overview). Provider names are internal AutoRouter identifiers and may be changed, but model `upstream` values must use the same names.

### 3. Fetch models and bind them to providers

Open the **Models** page and click **Fetch upstream models**:

> ⚠️ **Note:** If an upstream offers a large model catalog, filter it there before clicking **Fetch upstream models**. Otherwise AutoRouter may fetch a large number of models at once. For example, OpenRouter may return **400+ models** in a single request.

1. AutoRouter queries every provider with a configured `api_key`.
2. Every discovered model is tagged with its source provider in `upstream`.
3. Add `supports_vision` and `context_window` metadata for image and long-context policy checks.
4. Save the model configuration.

If a provider does not expose `/v1/models`, add its models manually in the UI or edit `config/models.yaml`:

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

`upstream` must match a provider name from `connection.yaml`. A model without `upstream` uses `providers.default`.

### 4. Create an automatic-routing strategy

Open the **Strategies** page, create a strategy such as `auto`, and assign the registered models to its difficulty tiers. Start with `rule` mode; after installing the ML dependencies, you can switch to `classifier`.

Equivalent `config/strategies.yaml` example:

```yaml
auto:
  kind: rule
  rules:
  - model: cheap-model   # R0: chitchat and trivial tasks
    max_tokens: 4096
    thinking: off
  - model: cheap-model   # R1: regular tasks
  - model: code-model    # R2: coding and harder tasks
    thinking: medium
  - model: strong-model  # R3: complex reasoning
    thinking: high
```

Set the request's `model` field to the strategy name, `auto`. AutoRouter selects a tier, replaces it with the real model name, and forwards the request to that model's provider.

### 5. Send your first request

This OpenAI-compatible Chat Completions example uses `-i` so you can inspect routing headers such as `X-Auto-Routed-To`:

```bash
curl -i http://127.0.0.1:3001/v1/chat/completions \
  -H "Authorization: Bearer <API key used for inference>" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Implement merge sort and explain its time complexity."}]}'
```

Useful diagnostics:

- Health check: `http://127.0.0.1:3001/health`
- Routing test: Management UI → **Strategies** → LiveTest (runs routing only and does not call the upstream)
- Routing logs: Management UI → **Logs**

### Development and release checks

Node.js is only required when modifying the management UI:

```bash
cd web
npm ci
npm run check
npm run build

# Run backend regression tests from the repository root
cd ..
uv run --no-sync python -m unittest discover -s tests -v
```

---

## Key concepts

### Routing modes

| Kind | Behavior |
|---|---|
| `single` | One rule and one fixed model; no classification |
| `rule` | Deterministic heuristic bands based on length and code signals; index 0–3 maps to `rules[idx]` |
| `classifier` | ML classifier produces index 0–3; automatically falls back to `rule` when ML is unavailable |

### Policy chain (six steps by default)

```text
confidence_gate → chitchat_only → complaint → anti_downgrade
                  → capability_gate → large_context_floor
```

Each step can upgrade, downgrade, or preserve the selected tier. Every step can be disabled independently in `config/policy.yaml`.

### Reserve R0 for chitchat

`chitchat_only` is enabled by default. Unless the text is a greeting, self-introduction, weather question, or social nicety, an R0 result is upgraded to R1. This prevents the fast `thinking: off` path from receiving short but non-trivial questions such as “Why is the sky blue?”

---

## Configuration (`config/*.yaml`)

| File | Purpose |
|---|---|
| `connection.yaml` | Server listener, multiple upstream providers (`default` plus name → `base_url`/`api_key`), and admin login |
| `strategies.yaml` | Routing strategies using `single`, `rule`, or `classifier` |
| `policy.yaml` | Policy-chain switches and thresholds such as the anti-downgrade window and large-context floors |
| `observability.yaml` | Contains only `log_dir`, which defaults to `./log` |
| `models.yaml` | Model capability registry used by `capability_gate` |
| `ml.yaml` | ML bundle path and confidence settings |

### Important settings (`POST /api/reload` applies changes immediately)

**`policy.yaml`**

| Setting | Default | Meaning |
|---|---|---|
| `anti_downgrade.window_seconds` | 600 | Prevent tier downgrades within the same session for N seconds, helping preserve upstream KV caches |
| `chitchat_only.enabled` | true | Reserve R0 for genuine chitchat; otherwise upgrade to R1 |
| `large_context_floor.t3_floor_tokens` | 150000 | Force the highest tier at 150k tokens or more |
| `large_context_floor.t2_floor_tokens` | 80000 | Force the second-highest tier at 80k tokens or more |
| `large_context_floor.context_window` | 262000 | Baseline context window used for ratio calculations |

**`ml.yaml`**

| Setting | Default | Meaning |
|---|---|---|
| `confidence_threshold` | 0.5 | Fall back to `fallback_idx` when the maximum ML probability is below this value |
| `confidence_fallback_idx` | -1 | `rules_count - 1`, the highest tier; set to `1` to fall back to R1 |

`confidence_fallback_idx = -1` favors quality by using the highest tier when uncertain. Setting it to `1` favors savings. Before changing it, inspect the `conf=` distribution for `src=ml` entries in `log/auto_router-*.log`.

**`strategies.yaml`** — typical four-tier automatic strategy:

```yaml
auto:
  kind: classifier  # or rule
  rules:
  - model: MiniMax-M3       # R0: trivial
    max_tokens: 4096
    thinking: off
  - model: MiniMax-M3       # R1: medium
  - model: kimi-for-coding  # R2: code
    thinking: medium
  - model: glm-5.2          # R3: heavy
    thinking: high
```

Every rule model **must exist in the `models.yaml` registry**; backend validation enforces this.

### Management UI login (Basic Auth)

Add an `admin` block to `config/connection.yaml`:

```yaml
admin:
  user: "admin"
  password: ""  # Empty disables login, which is the development default
```

When enabled, all `/api/*` configuration endpoints require `Authorization: Basic base64(user:pass)`. Forwarding under `/v1/*` is unaffected. The SPA displays a login screen, stores credentials in `localStorage`, and provides a logout button in the toolbar.

The deployer decides whether `/v1/*` is publicly reachable. For local use, bind to `127.0.0.1`. For public deployments, restrict access with a firewall or external gateway.

⚠️ **Production note:** The YAML password is stored as plain text. Hash-based verification such as `hashlib.pbkdf2_hmac` is recommended for hardened deployments.

---

## Daily log rotation

`config/observability.yaml:log_dir` selects the log directory, which defaults to `./log`. Each worker writes to a file named for the local date:

- Today: `auto_router-2026-07-14.log`
- Previous day: `auto_router-2026-07-13.log` (files are never automatically deleted)

**Asynchronous logging pipeline:**

```text
Application thread → QueueHandler → Queue → one listener thread → DailyFileHandler → file
```

This keeps application threads from blocking on file I/O and improved measured throughput by about **47%** at 200 concurrent requests (106 → 156 RPS).

All workers append to the same file using atomic Windows `_O_APPEND` writes without an inter-process lock. For strict separation, use a pattern such as `auto_router-{date}-w{pid}.log`, at the cost of splitting logs across files.

View logs from Management UI → **Logs**. Select a file from the left-hand list; today's file is marked with a badge, and the full file is displayed without tail truncation.

---

## Production deployment

### Recommended command (4–8 workers)

```bash
uv run uvicorn app.channel:app \
  --host 0.0.0.0 \
  --port 3001 \
  --workers 4 \
  --log-level info
```

Run the isolated fake upstream for local testing only:

```bash
uv run uvicorn scripts.fake_newapi:app \
  --host 127.0.0.1 \
  --port 3000 \
  --workers 4
```

For production, replace the `providers` section in `connection.yaml` with the real `base_url` and model-fetching `api_key` for each provider.

### Memory requirements (primarily the ML bundle)

Each worker loads a complete ML bundle—LightGBM, BGE/ONNX, scikit-learn, and an MLP head—using approximately **300–400 MB**.

| Workers | Memory budget | Notes |
|---|---|---|
| 1 | ~500 MB | One process with a full model load |
| 4 | ~1.5 GB | Recommended balance |
| 8 | ~3 GB | Highest configuration tested as stable |
| 16 | ~6 GB | Better suited to large machines or multiple hosts |

Avoid stacking 16 or more workers on one host; scale across hosts behind a load balancer instead.

### Measured performance

| Configuration | Concurrency | RPS | p50 | p99 |
|---|---:|---:|---:|---:|
| 1 worker | 100 | 31.6 | 2.0s | 3.0s |
| 2 workers | 100 | 46.6 | 1.8s | 2.6s |
| 4 workers | 100 | 79.4 | 0.9s | 1.2s |
| **8 workers + async logging** | **100** | **156.9** | **0.55s** | **0.61s** |
| **8 workers + async logging** | **200** | **156.3** | **1.14s** | **1.26s** |

Benchmarks use `scripts/load_test.py` for 100 concurrent requests and `scripts/load_test_200.py` for 200. These are single-client measurements; real deployments with multiple upstreams and clients may behave differently.

---

## Troubleshooting

| Symptom | What to check |
|---|---|
| No log files | Confirm that `log_dir` is writable and the listener thread is running |
| Every request routes to `glm-5.2` | Inspect `conf=` for `src=ml` in `log/auto_router-*.log`; the classifier may be repeatedly hitting its low-confidence fallback |
| Forwarding returns 502/404 | Verify `connection.yaml.providers.*.base_url` and confirm that the selected provider offers the routed model |
| Memory keeps growing | 300–400 MB per worker is expected; continuous growth may indicate a stuck listener or reload loop |
| A request takes over 10 seconds | Often caused by running 200 concurrent requests against eight workers; lower concurrency or add workers |
| YAML changes do not apply | Send `POST /api/reload` and look for `Reloaded. N strategies` in stdout |
| The SPA shows the login screen | `admin.password` is enabled; enter the configured credentials or clear the password |

---

## Repository map

```text
app/                   # Core Python modules
  channel.py           # FastAPI entry point, forwarding, logging, and admin middleware
  router.py            # Top-level routing function
  policy.py            # Six-step policy chain
  heuristic.py         # Heuristic bands
  ml_router.py         # Lazily loaded ML routing layer
  ml/                  # ML internals ported from OpenSquilla
config/                # Six YAML files: connection, strategies, policy, observability, models, and ML
web/                   # Svelte management SPA
scripts/               # Load tests, offline mock, and admin response checks
tests/                 # Configuration transactions, multi-provider, and endpoint passthrough regression tests
models/                # Read-only OpenSquilla pretrained artifacts
log/                   # Runtime daily logs, never automatically deleted
```

Detailed `app/` layout:

```text
app/
  channel.py        # FastAPI entry point, routes, and logging
  router.py         # Top-level route() function
  policy.py         # Chitchat, complaint, anti-downgrade, capability, and large-context policies
  heuristic.py      # classify, classify_with_messages, and context_signals
  ml_router.py      # Lazy bundle loading and asyncio.to_thread inference
  ml/
    bundle.py       # Artifact loading
    core.py         # InferenceCore: features → heads → fusion
    artifacts.py    # V3FeatureRuntime
    bge_onnx.py     # BGE-small-zh ONNX
    ensemble.py     # LightGBM + MLP head
    v4_features.py  # 390-dimensional features
    features.py     # V3 feature helpers
    types.py        # Dataclasses
    heads.py        # Single-head abstraction
    trajectory.py   # Trajectory accumulation
  observe.py        # Routing decision → structured log entry
  session.py        # SessionStore used by anti-downgrade
  config.py         # Multi-YAML loading, validation, and hot reload
```

`config/`:

```text
connection.yaml    # Host/port, upstream providers, and admin login
strategies.yaml    # Routing strategies
policy.yaml        # Policy-chain switches and thresholds
observability.yaml # log_dir
models.yaml        # Model capability registry
ml.yaml            # ML bundle configuration
```

`web/`:

```text
src/App.svelte                  # Shell, tabs, login gate, and logout
src/api.ts                      # Backend client and types with Authorization support
src/tabs/
  ConnectionTab.svelte
  ModelConfigTab.svelte         # Model registry and upstream fetching
  StrategiesTab.svelte          # Routing strategies and LiveTest
  MlTab.svelte
  PolicyTab.svelte
  ObservabilityTab.svelte       # Log viewer
```

`scripts/`:

```text
fake_newapi.py           # Offline mock upstream for tests only
load_test.py             # 100-concurrent-request benchmark
load_test_200.py         # 200-concurrent-request benchmark
check_admin_response.py  # Verifies that /api/config does not expose passwords
req_*.json               # Request bodies used as curl development references
```

---

## License

This project's code is released under the [MIT License](LICENSE) (`Copyright (c) 2026 hahahehe`).

The embedded ML routing capability comes from the [OpenSquilla](https://github.com/opensquilla/opensquilla) SquillaRouter V4 Phase 3 inference bundle (`models/v4.2_phase3_inference/`) under its upstream MIT license.

The BGE embedding model comes from [FlagOpen/FlagEmbedding](https://github.com/FlagOpen/FlagEmbedding) (`BAAI/bge-small-zh-v1.5`).

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the complete list of third-party dependencies.
