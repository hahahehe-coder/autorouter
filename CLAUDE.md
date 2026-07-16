# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

- Python 3.10+, **use `uv`** (`D:\uv\uv.exe`) — never `pip` or `python -m pip` (Store Python clobbers system).
- Optional ML: `uv sync --extra ml` (default install stays light — FastAPI/uvicorn/httpx/PyYAML only).
- ML bundle (~76 MB, gitignored): drop into `models/v4.2_phase3_inference/` (data only; inference code is vendored under `app/ml/`).

## Commands

| Action | Command |
|---|---|
| Install deps | `uv sync` (+ `--extra ml` for ML router) |
| Run dev server | `uv run uvicorn app.channel:app --reload --host 0.0.0.0 --port 3001` |
| Run app (no reload) | `uv run uvicorn app.channel:app --host 0.0.0.0 --port 3001` |
| Frontend dev (with API proxy) | `cd web && npm run dev` → `http://localhost:5173` (proxies `/api` → 3001) |
| Frontend typecheck | `cd web && npm run check` |
| Frontend build (→ `web/dist/`, git-tracked for embedded SPA) | `cd web && npm run build` |
| Tests | `uv run python -m unittest tests.test_release_paths -v` |
| Load test | `uv run python scripts/load_test.py` (requires running server) |
| Fake upstream (for tests) | `uv run python scripts/fake_newapi.py` |

## Architecture

**FastAPI pseudo-channel** sits between clients and one or more OpenAI-compatible upstream providers. Client sends a normal request with `model=auto` (or any registered strategy name); AutoRouter classifies the request, picks a rule, optionally rewrites a few fields (`model`/`max_tokens`/`system`/`thinking`), forwards to the chosen upstream. **Everything else — auth headers, body schema, response bytes — passes through verbatim.**

### Request pipeline (`app/channel.py`)

```
client ──► /v1/chat/completions  ─┐
        /v1/messages             ├──► _route_and_forward()
        /v1/responses            │       │
        /v1/{anything else}       │       ▼
                                  │   router.route()       (kind dispatch)
                                  │       │
                                  │       ▼
                                  │   policy.run_pipeline()  (6-step chain, see below)
                                  │       │
                                  │       ▼
                                  │   _apply_field() per-endpoint field map
                                  │       │
                                  │       ▼
                                  │   _upstream_url(_model_upstream(decision.model), path)
                                  │       │           (multi-provider dispatch)
                                  │       ▼
                                  │   upstream httpx POST   (stream OR buffered)
                                  └─────────────────────────────────── transparent passthrough (other paths)
```

### Strategy kinds (`app/config.py`)

Each strategy is one of three kinds (parsed aliases: `static`→`single`, `heuristic`→`classifier`):

| `kind` | Inputs | Source |
|---|---|---|
| `single` | `rule` (one) | `static` |
| `rule` | `rules[]` (4-tier, idx 0=trivial…3=heavy) | `heuristic` |
| `classifier` | `rules[]` (4-tier) | `ml` (falls back to `heuristic` if bundle missing/broken) |

Unknown strategy name in `body.model` → **passthrough** (no rewrites, original model sent upstream).

### Policy chain (`app/policy.py`, fixed order)

```
confidence_gate → chitchat_only → complaint_upgrade → anti_downgrade
                → capability_gate → large_context_floor
```

`capability_gate` reads `ModelCfg.supports_vision` / `context_window` from the **registry**, not from rules. `None` = unknown → no-op (don't break uncertain models).

### Multi-provider routing

Per-model tag `models.yaml → ModelCfg.upstream` is filled in at pull time (see `/api/models`) and selects which `providers` entry to forward to. Falls back to `providers.default` if untagged. URL join is smart: base ending in `/v1` doesn't double-prepend.

### ML (`app/ml_router.py`, `app/ml/`)

OpenSquilla v4.2 bundle loader. Heavy deps (`lightgbm`, `onnxruntime`, `joblib`, `sklearn`, `tokenizers`, `numpy`) live behind `from .ml.core import InferenceCore` lazy import — server boots fine without them. Status flows: `unconfigured | disabled | ready | deps_missing | bundle_missing | runtime_error`. Warmup is on by default; disable with `ml.warmup_on_load: false` if boot latency matters.

## Critical invariants

1. **Model registry is mandatory.** `validate()` rejects any strategy whose `rule.model` / `rules[].model` isn't a key in `models.yaml`. Add to registry first, then reference. UI enforces the same constraint.
2. **Rule fields are flat** (`model`, `max_tokens`, `system`, `thinking`) — no nested `inference: {…}`. Per-endpoint field mapping is `_apply_field()` in `channel.py`. The router does NOT parse per-protocol headers — it passes them through.
3. **Empty `admin.password` ⇒ auth disabled** (dev mode). Any `/api/*` write goes through. Set a password before exposing port 3001 externally.
4. **`/api/*` requires Basic Auth; `/v1/*` never does.** The 401 response intentionally **omits `WWW-Authenticate`** to avoid browser-native auth dialog — the SPA's `LoginScreen` handles it.
5. **Header pass-through is verbatim** minus the RFC 7230 §6.1 hop-by-hop list. Forwarded request gets hop headers regenerated by httpx; `Host`/`Content-Length`/`Connection`/etc. are stripped.
6. **Config saves are atomic.** YAML written via temp-file + `os.replace` + `fsync`. `validate_updates()` does cross-section validation **before** any file write, so partial bad configs are rejected wholesale.
7. **Model snapshot hides `password`** in `connection.admin` (returns only `enabled`). LoginScreen sends Basic Auth on every API call; UI handles credential lifecycle via `localStorage`.

## File map (when changing…)

| If you change… | Touch |
|---|---|
| Add a new policy step | `app/policy.py` (`POLICY_ORDER` tuple + fn) + ctx fields if you need new inputs |
| Add a new strategy kind | `app/config.py` (`StrategyCfg`, `_parse_strategies`, `_KIND_ALIASES`, `validate()`) + `app/router.py` (`_route_*` + `route()` dispatch) |
| Add a new endpoint field rewrite | `app/channel.py:_apply_field()` |
| Change registry model schema | `app/config.py` (`ModelCfg`, `_parse_models`) + `app/policy.py` (which attrs it reads) + frontend `web/src/api.ts` + relevant tab component |
| Pull upstream models | `app/channel.py:/api/models` (iterates providers, tags each model with `upstream`) |
| ML bundle config | `config/ml.yaml` (`bundle_path`, `enabled`, `confidence_threshold`, `warmup_on_load`) |
| Frontend tab | `web/src/App.svelte` (TABS array) + new file in `web/src/tabs/` |
| Add an API endpoint | Mount in `app/channel.py`. **Order matters:** all `@app.*` routes before the trailing `app.mount("/", StaticFiles(...))` for SPA fallback. |

## Tests

`tests/test_release_paths.py` covers three concerns:
- **Routing input normalization** — `responses` API and Anthropic multi-modal content flatten to `{type, text}` / `{type, image_url}` shape for the router.
- **Session keying** — keyed on auth only, not on message content.
- **Config snapshot fidelity** — unsubmitted `password` preserved, `enabled` flag stripped (it's an output field, not input).
- **Proxy transport** — non-stream and stream pass-through keep raw bodies/status/headers; `/v1/embeddings` fallback forwards raw bytes without JSON parsing.
- **Bulk config save** — atomicity: invalid payload doesn't corrupt existing file.

When adding routing logic, extend this file. The transport-level tests use `httpx.MockTransport` to assert bytes/headers/status round-trip without touching the network.
