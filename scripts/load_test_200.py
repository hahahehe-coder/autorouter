"""200 并发 — 2× 之前的负载,看 RPS 是否还线性扩"""
import asyncio
import statistics
import time
import uuid
import httpx
from load_test import QUERIES_R0, QUERIES_R1, QUERIES_R2, QUERIES_R3, send

ALL = []
for q in QUERIES_R0: ALL.append((q, 0, "R0"))
for q in QUERIES_R1: ALL.append((q, 1, "R1"))
for q in QUERIES_R2: ALL.append((q, 2, "R2"))
for q in QUERIES_R3: ALL.append((q, 3, "R3"))
ALL = ALL * 2   # 200 req
print(f"Total {len(ALL)} requests")

async def main():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:3001", limits=httpx.Limits(max_connections=500)) as c:
        for _ in range(15):
            await send(c, -1, "warm", 1, "warm")
        t0 = time.perf_counter()
        results = await asyncio.gather(*[send(c, i, q, ei, lbl) for i, (q, ei, lbl) in enumerate(ALL)])
        wall = time.perf_counter() - t0
        ok = sum(1 for r in results if r["ok"])
        lat = sorted(r["elapsed_ms"] for r in results)
        def pct(p):
            i = min(int(round(p/100*len(lat))), len(lat)-1); return lat[i]
        print(f"agg ok={ok}/{len(ALL)} wall={wall:.2f}s RPS={len(ALL)/wall:.1f}")
        print(f"  min={min(lat):.0f}  p50={pct(50):.0f}  p90={pct(90):.0f}  p99={pct(99):.0f}  max={max(lat):.0f}  mean={statistics.mean(lat):.0f}")

asyncio.run(main())
