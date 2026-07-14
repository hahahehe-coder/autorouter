"""
并发压测 v2 — 4 档均衡 query(各 25 条)走 heuristic_test(kind=rule)。
每条 query 都按 heuristic 确定性能锁定到 R0/R1/R2/R3:
  R0 trivial: ≤240 chars, 纯闲聊/短问题  →  MiniMax-M3
  R1 medium:  240-1200 chars, 无代码  →  MiniMax-M3
  R2 code:    含 ``` 代码块  →  kimi-for-coding
  R3 heavy:   ≥12000 chars OR ≥3 代码块  →  glm-5.2

报告:延迟分布 / RPS(吞吐)/ 每档精准路由命中率 / 模型分布
"""
import asyncio
import statistics
import time
import uuid
import httpx


QUERIES_R0 = [
    "你好", "hello", "thanks", "hi", "今天天气如何?",
    "你是谁", "介绍自己", "ok", "好的", "thank you",
    "嗨", "早上好", "再見", "thanks a lot", "thanks so much",
    "你好啊", "how are you", "what's up",
    "好的呢", "ok yo",
    "好的哈", "嗯", "是的", "收到", "sure",
]

QUERIES_R1 = [
    "请帮我比较 HTTP 和 HTTPS 的主要区别,以及为什么需要 HTTPS",
    "explain the concept of recursion in programming with a clear example",
    "为什么 Python 的 GIL 影响多线程性能?有没有办法绕过?",
    "compare SQL and NoSQL databases in terms of consistency and scalability",
    "what is the difference between TCP and UDP? when to use each?",
    "请用通俗的语言解释一下什么是机器学习中的过拟合",
    "describe how HTTPS handshake works step by step in plain language",
    "总结一下微服务架构和单体架构各自的优缺点",
    "explain how a hash table handles collisions with examples",
    "why does Python use indentation for block structure instead of braces?",
    "compare REST and GraphQL APIs in terms of flexibility and learning curve",
    "describe the difference between process and thread with clear examples",
    "讲一下数据库的索引为什么能加速查询,代价又是什么",
    "explain big O notation with practical examples and common pitfalls",
    "what is the difference between authentication and authorization?",
    "帮我通俗地解释一下什么是 CDN,以及它为什么能加速访问",
    "讲一下 TCP 三次握手和四次挥手的具体过程",
    "explain what a websocket is and why it differs from regular HTTP",
    "what is the role of DNS in the internet, simplified for beginners",
    "简单讲讲 HTTPS 怎么在传输层保证数据不被篡改",
    "用大白话解释一下 HTTP 状态码 301 和 302 的区别",
    "explain what an API gateway does in a microservices architecture",
    "用简单的话说说什么是消息队列,以及为什么要用它",
    "describe the difference between docker and virtual machines briefly",
    "讲讲浏览器输入 URL 之后到看到网页,中间发生了什么",
]

QUERIES_R2 = [
    "帮我优化这段代码:\n```python\ndef sort(arr):\n    pass\n```",
    "fix this bug:\n```python\nfor i in range(10)\n    print(i)\n```",
    "帮我写一个快排:\n```\ndef sort(arr):\n    pass\n```",
    "write a function:\n```python\ndef add(a, b):\n    return a + b\n```",
    "解释这段代码:\n```js\nconst x = (a, b) => a + b;\n```",
    "translate this to python:\n```ruby\n[1,2,3].map { |i| i*2 }\n```",
    "debug the error:\n```python\nx = 1/0\n```",
    "improve performance:\n```sql\nSELECT * FROM t WHERE id=1;\n```",
    "refactor this:\n```js\nfunction f(){return 1+2}\n```",
    "write a regex:\n```python\nimport re\n```",
    "explain this snippet:\n```c\nint main(){return 0;}\n```",
    "帮我修复编译错误:\n```go\npackage main\nfunc main(){}\n```",
    "complete this function:\n```python\ndef fib(n):\n    # TODO\n```",
    "add types to:\n```ts\nfunction add(a,b){return a+b}\n```",
    "find memory leak:\n```java\nList<String> list = new ArrayList<>();\n```",
    "写一个二分查找:\n```\ndef bsearch(arr, t):\n    pass\n```",
    "convert to async:\n```js\nfetch(url).then(r=>r.json())\n```",
    "explain decorators:\n```python\n@decorator\ndef f(): pass\n```",
    "write a class:\n```python\nclass User:\n    def __init__(self):\n```",
    "add tests:\n```python\ndef add(a,b): return a+b\n```",
    "review this code:\n```go\nfunc main() { fmt.Println(\"hi\") }\n```",
    "explain the bug:\n```rust\nfn main(){let x: i32=\"hi\";}\n```",
    "translate to Rust:\n```python\ndef f(x): return x*2\n```",
    "fix syntax error:\n```ts\nconst x: number = \"hi\"\n```",
    "complete the snippet:\n```java\npublic class Main { public static void main(String[] a) {} }\n```",
]

QUERIES_R3 = ["x" * n for n in (
    15000, 18000, 13000, 20000, 16000, 12500, 22000, 17000, 14000, 19500,
    15000, 17000, 13000, 21000, 16000, 12500, 18000, 14500, 19000, 15500,
    13500, 17500, 16500, 14000, 18500,
)]

assert len(QUERIES_R0) == 25
assert len(QUERIES_R1) == 25
assert len(QUERIES_R2) == 25
assert len(QUERIES_R3) == 25

# 打 label
ALL = []
for q in QUERIES_R0: ALL.append((q, 0, "R0"))
for q in QUERIES_R1: ALL.append((q, 1, "R1"))
for q in QUERIES_R2: ALL.append((q, 2, "R2"))
for q in QUERIES_R3: ALL.append((q, 3, "R3"))
assert len(ALL) == 100

async def send(client: httpx.AsyncClient, idx: int, content: str, expected_idx: int, label: str) -> dict:
    t0 = time.perf_counter()
    # 唯一 Authorization 让 session_key 各不相同 → anti_downgrade no-op
    auth = f"Bearer test-{uuid.uuid4()}"
    try:
        r = await client.post(
            "/v1/chat/completions",
            json={"model": "heuristic_test", "messages": [{"role": "user", "content": content}]},
            headers={"Authorization": auth},
            timeout=20.0,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000
        actual_model = "?"
        try:
            body = r.json()
            actual_model = body.get("model", "?")
        except Exception:
            pass
        return {
            "i": idx, "expected": expected_idx, "label": label,
            "ok": True, "status": r.status_code,
            "actual_model": actual_model,
            "elapsed_ms": elapsed_ms,
        }
    except Exception:
        return {"i": idx, "expected": expected_idx, "label": label,
                "ok": False, "status": -1, "actual_model": "?", "elapsed_ms": (time.perf_counter() - t0) * 1000}


async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:3001", limits=httpx.Limits(max_connections=300)) as c:
        r = await c.get("/health")
        print(f"Sanity /health: status={r.status_code}\n")

        print("Warming up (10 sequential)...")
        await asyncio.gather(*[send(c, -1, "warm", 1, "warm") for _ in range(10)])

        N = len(ALL)
        print(f"\nSending {N} requests (4 tiers × 25, kind=rule, ML off)...\n")

        t_start = time.perf_counter()
        results = await asyncio.gather(*[send(c, i, q, ei, lbl) for i, (q, ei, lbl) in enumerate(ALL)])
        wall = time.perf_counter() - t_start

        ok = sum(1 for r in results if r["ok"])
        err = N - ok
        latencies = sorted([r["elapsed_ms"] for r in results])

        def pct(p):
            if not latencies: return 0
            i = min(int(round(p / 100 * len(latencies))), len(latencies) - 1)
            return latencies[i]

        per_tier = {0: [], 1: [], 2: [], 3: []}
        for r in results:
            per_tier.setdefault(r["expected"], []).append(r)

        tier_label = {0: "R0 期望 → M3", 1: "R1 期望 → M3", 2: "R2 期望 → kimi", 3: "R3 期望 → glm-5.2"}

        print("=" * 64)
        print(f"AGGREGATE  ({N} req,  {wall:.2f}s wall,  {N/wall:.1f} req/s)")
        print("=" * 64)
        print(f"成功 {ok}   失败 {err}")
        print()
        print("=== 路由精准度(按预期档统计实际模型)===")
        all_match = 0
        for t in (0, 1, 2, 3):
            rs = per_tier[t]
            models = {}
            for r in rs:
                models[r["actual_model"]] = models.get(r["actual_model"], 0) + 1
            expected_model = {0: "MiniMax-M3", 1: "MiniMax-M3", 2: "kimi-for-coding", 3: "glm-5.2"}[t]
            match = models.get(expected_model, 0)
            all_match += match
            print(f"  {tier_label[t]:<28} ({len(rs)} req): {dict(sorted(models.items()))}  match {match}/{len(rs)}")
        print(f"  路由准确率: {all_match}/{N} = {all_match/N*100:.0f}%")
        print()
        print("=== 延迟(ms) ===")
        print(f"  min        = {min(latencies):>7.1f}")
        print(f"  p50        = {pct(50):>7.1f}")
        print(f"  p90        = {pct(90):>7.1f}")
        print(f"  p95        = {pct(95):>7.1f}")
        print(f"  p99        = {pct(99):>7.1f}")
        print(f"  max        = {max(latencies):>7.1f}")
        print(f"  mean       = {statistics.mean(latencies):>7.1f}")
        print()
        print("=== 吞吐(单 worker,单 ML bundle)===")
        print(f"  RPS          = {N/wall:>6.1f} req/s")
        print(f"  并发等级     = 100 in-flight @ {wall:.1f}s wall")


if __name__ == "__main__":
    asyncio.run(main())