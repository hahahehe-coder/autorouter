"""
test_robust_live.py — 端到端实时验证 robust 策略的两个能力:

    1. 失败自动切换(网络错误 / 403 / 429 / 5xx → 下一个模型)
    2. 实时热重载(改 config 后,所有 worker 的 _CFG 立即更新)

启动一个 mock 上游(可控返回 200/500/429/connect-error)+ autorouter 进程,
然后用真实 HTTP 请求验证两件事。
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ============================ 配置 ============================

UPSTREAM_HOST = "127.0.0.1"
# 随机端口,避免前次残留 autorouter 占着
_UPSTREAM_PORT = 17000 + (os.getpid() % 1000) * 2
ROUTER_PORT = _UPSTREAM_PORT + 1
UPSTREAM_PORT = _UPSTREAM_PORT
WORKERS = int(os.getenv("TEST_WORKERS", "2"))

# Windows-friendly log path
LOG_DIR = Path(os.getenv("TEMP", "/tmp"))

# 每个模型当前的"状态":200 / 500 / 429 / 403 / unreachable
FAIL_MODE: dict[str, str | int] = {}
FAIL_LOCK = threading.Lock()


# ============================ mock 上游 ============================

mock_app = FastAPI()


@mock_app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [
        {"id": "test-primary",   "object": "model"},
        {"id": "test-backup-1",  "object": "model"},
        {"id": "test-backup-2",  "object": "model"},
    ]}


def _current_mode(model: str) -> str | int:
    with FAIL_LOCK:
        return FAIL_MODE.get(model, 200)


@mock_app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    model = body.get("model", "?")
    mode = _current_mode(model)
    print(f"  [上游收到] model={model} 当前 mode={mode}")
    if mode == "unreachable":
        # 让上游直接关连接,模拟网络错误
        raise RuntimeError("simulated upstream close")
    if isinstance(mode, int) and mode >= 400:
        return JSONResponse({"error": f"simulated status {mode}"}, status_code=mode)
    return JSONResponse({
        "id": "x", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": f"OK from {model}"}, "finish_reason": "stop"}],
    })


@mock_app.post("/v1/messages")
async def messages(request: Request):
    return await chat(request)


@mock_app.post("/v1/responses")
async def responses(request: Request):
    return await chat(request)


def run_mock():
    uvicorn.run(mock_app, host=UPSTREAM_HOST, port=UPSTREAM_PORT, log_level="warning")


# ============================ 启 / 关 autorouter 子进程 ============================

ROUTER_LOG = Path("/tmp/autorouter_live.log")


def start_router():
    # 先确保端口空闲(避免前次残留 autorouter 还在听这个端口 → 新进程失败后旧进程应答测试)
    import subprocess as sp
    try:
        # netstat 在中文 Windows 上 stdout 是 GBK,直接 decode 可能炸;这里用 errors="ignore"
        out = sp.check_output(
            ["netstat", "-ano"], text=False, stderr=sp.DEVNULL,
        )
        text = out.decode("utf-8", errors="ignore")
        pids = set()
        for line in text.splitlines():
            if ("LISTENING" in line
                    and (f":{UPSTREAM_PORT} " in line or f":{ROUTER_PORT} " in line)):
                parts = line.split()
                if len(parts) >= 5 and parts[-1].isdigit():
                    pids.add(parts[-1])
        for pid in pids:
            sp.run(["taskkill", "//PID", pid, "//F", "//T"],
                   stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        if pids:
            print(f"  清掉 {len(pids)} 个残留进程: {sorted(pids)}")
            time.sleep(3)
    except Exception as e:
        print(f"  (清理端口异常,继续: {e})")

    env = os.environ.copy()
    env["CONFIG_DIR"] = str(ROOT / "config")
    log_path = LOG_DIR / "autorouter_live.log"
    log_f = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        ["uv", "run", "uvicorn", "app.channel:app", "--host", "127.0.0.1",
         "--port", str(ROUTER_PORT), "--workers", str(WORKERS), "--log-level", "info"],
        cwd=str(ROOT), env=env,
        stdout=log_f, stderr=subprocess.STDOUT,
    )
    proc._log_path = log_path
    return proc


def wait_router_ready(deadline: float):
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", ROUTER_PORT), timeout=1):
                # 端口开了;再 /health 一下确认应用 ready
                r = httpx.get(f"http://127.0.0.1:{ROUTER_PORT}/health", timeout=2)
                if r.status_code == 200:
                    return True
        except Exception:
            time.sleep(0.4)
    return False


# ============================ 主测试流程 ============================

def banner(s):
    print(f"\n{'=' * 60}\n  {s}\n{'=' * 60}")


def call_via_router(model_strategy: str, payload: dict) -> httpx.Response:
    """向 autorouter 发请求(走 /v1/chat/completions),model 字段是策略名。"""
    body = {"model": model_strategy, "messages": [{"role": "user", "content": "hi"}], **payload}
    return httpx.post(
        f"http://127.0.0.1:{ROUTER_PORT}/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        json=body, timeout=30,
    )


def assert_eq(label, actual, expected):
    ok = actual == expected
    print(f"  {'✓' if ok else '✗'} {label}: got={actual}  expected={expected}")
    if not ok:
        raise AssertionError(label)


def main():
    banner("1. 启动 mock 上游(127.0.0.1:13901)")
    threading.Thread(target=run_mock, daemon=True).start()
    time.sleep(1.5)

    banner("2. 改 connection.yaml + strategies.yaml + models.yaml 指向本机 mock")
    cfg_dir = ROOT / "config"
    connection_path = cfg_dir / "connection.yaml"
    strategies_path = cfg_dir / "strategies.yaml"
    models_path = cfg_dir / "models.yaml"

    bak = {p: p.read_bytes() for p in (connection_path, strategies_path, models_path, cfg_dir / "policy.yaml")}
    router = None
    log_path = None
    try:
        connection_path.write_text(f"""providers:
  default: mock
  mock:
    base_url: http://{UPSTREAM_HOST}:{UPSTREAM_PORT}
    api_key: ""
admin:
  user: admin
  password: ""
""", encoding="utf-8")
        models_path.write_text("""test-primary:
  supports_vision: false
  context_window: 32000
  upstream: mock
test-backup-1:
  supports_vision: false
  context_window: 32000
  upstream: mock
test-backup-2:
  supports_vision: false
  context_window: 32000
  upstream: mock
""", encoding="utf-8")
        strategies_path.write_text("""stable:
  kind: robust
  models:
  - test-primary
  - test-backup-1
  - test-backup-2
""", encoding="utf-8")
        # 关掉 failover cooldown(测试里我们想立刻看到模型切换效果,不被 600s 冷却干扰)
        (cfg_dir / "policy.yaml").write_text("""failover:
  cooldown_seconds: 0
""", encoding="utf-8")

        banner(f"3. 启动 autorouter (--workers {WORKERS}, port {ROUTER_PORT})")
        router = start_router()
        log_path = router._log_path
        try:
            if not wait_router_ready(time.time() + 20):
                print("autorouter 没起来,日志:")
                print(log_path.read_text(encoding="utf-8", errors="ignore"))
                router.kill(); return
            print("  autorouter ready")
            time.sleep(2)  # 让 worker 子进程都 ready

            banner("4. 失败自动切换测试")
            with FAIL_LOCK:
                FAIL_MODE["test-primary"]   = 500   # 主模型必失败
                FAIL_MODE["test-backup-1"]  = 200   # 第一个备份 OK
                FAIL_MODE["test-backup-2"]  = 200
            r = call_via_router("stable", {})
            print(f"  response status={r.status_code}  body={r.text[:120]}")
            assert_eq("主 500 时应该落到 backup-1",
                      json.loads(r.text)["model"], "test-backup-1")

            with FAIL_LOCK:
                FAIL_MODE["test-primary"]   = 429
                FAIL_MODE["test-backup-1"]  = 503
                FAIL_MODE["test-backup-2"]  = 200
            r = call_via_router("stable", {})
            assert_eq("主 429 + backup-1 503 时落到 backup-2",
                      json.loads(r.text)["model"], "test-backup-2")

            banner("5. 实时热重载:把 robust 模型顺序倒过来,期望请求落到新主模型")
            # 直接 PUT /api/config 改 strategies —— 不重启进程,也不重启 worker
            snapshot = httpx.get(f"http://127.0.0.1:{ROUTER_PORT}/api/config").json()
            snapshot["strategies"]["stable"]["models"] = [
                "test-backup-2", "test-primary", "test-backup-1",
            ]
            put = httpx.put(
                f"http://127.0.0.1:{ROUTER_PORT}/api/config",
                json=snapshot, timeout=10,
            )
            assert_eq("PUT /api/config", put.status_code, 200)
            time.sleep(2)  # 等 SIGHUP 广播 + 所有 worker reload

            # 全部 200,期望落到新主 test-backup-2
            with FAIL_LOCK:
                for m in ("test-primary", "test-backup-1", "test-backup-2"):
                    FAIL_MODE[m] = 200
            # 多次打,确保 round-robin 落到不同 worker 都返回新主
            seen = set()
            for _ in range(10):
                r = call_via_router("stable", {})
                seen.add(json.loads(r.text)["model"])
            print(f"  10 次请求返回的 model 集合: {seen}")
            assert_eq("热重载后,所有 worker 都应该用新主模型", seen, {"test-backup-2"})

            banner("6. 实时热重载:再改回原顺序")
            snapshot = httpx.get(f"http://127.0.0.1:{ROUTER_PORT}/api/config").json()
            print(f"  step6 GET 返回的 models: {snapshot['strategies']['stable']['models']}")
            snapshot["strategies"]["stable"]["models"] = [
                "test-primary", "test-backup-1", "test-backup-2",
            ]
            put2 = httpx.put(f"http://127.0.0.1:{ROUTER_PORT}/api/config", json=snapshot, timeout=10)
            print(f"  step6 PUT status: {put2.status_code}")
            time.sleep(8)  # 给 SIGHUP 充分时间广播到所有 worker
            # 验证 API 现在返回的是新顺序
            after = httpx.get(f"http://127.0.0.1:{ROUTER_PORT}/api/config").json()
            print(f"  step6 PUT 后 GET 的 models: {after['strategies']['stable']['models']}")
            with FAIL_LOCK:
                for m in ("test-primary", "test-backup-1", "test-backup-2"):
                    FAIL_MODE[m] = 200
            # 先打 5 次预热,让任何迟到的 SIGHUP 都生效
            for _ in range(5):
                call_via_router("stable", {})
            seen = []
            for _ in range(40):
                r = call_via_router("stable", {})
                seen.append(json.loads(r.text)["model"])
            from collections import Counter
            print(f"  40 次请求返回的 model 分布: {Counter(seen)}")
            assert_eq("改回后,应该全部走 test-primary", set(seen), {"test-primary"})

            banner("全部通过 ✓")
        finally:
            router.terminate()
            try: router.wait(timeout=5)
            except subprocess.TimeoutExpired: router.kill()
    except AssertionError as e:
        print(f"\n失败: {e}")
        if log_path:
            print(f"\n--- autorouter 日志 ({log_path}) ---")
            try:
                print(log_path.read_text(encoding="utf-8", errors="ignore")[-4000:])
            except Exception as ee:
                print(f"(读日志失败: {ee})")
        sys.exit(1)
    finally:
        for p, b in bak.items():
            p.write_bytes(b)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\n失败: {e}")
        sys.exit(1)