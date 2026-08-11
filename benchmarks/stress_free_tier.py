"""Part 2.1 — Live free-tier stress: 50 concurrent requests through the
harness's own OpenRouterClient, restricted to the openrouter/free route.

Measures: latency p50/p95/p99, 429 rate-limit hits, error taxonomy, dropped
requests, total cost (must be $0 on the free route). Varying prompt lengths:
short / medium / long.
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(r"C:\Users\caleb\infinity-code\backend")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from dotenv import load_dotenv  # noqa: E402

for _p in (ROOT.parent / ".env", ROOT / ".env"):
    if _p.is_file():
        load_dotenv(_p, override=False)

from tools import openrouter_client as orc  # noqa: E402

N = 50
SHORT = "Reply with one short sentence about the sky."
MEDIUM = ("Summarize in two sentences: " + ("The harness routes LLM calls "
          "through a failover chain with health tracking. ") * 8)
LONG = ("Answer concisely: " + ("Autonomous agents plan, execute tools, "
        "observe results, and iterate without human input while managing "
        "context windows and costs. ") * 40)
PROMPTS = [SHORT, MEDIUM, LONG]


def build_free_only_client() -> orc.OpenRouterClient:
    client = orc.OpenRouterClient()
    free = [r for r in client._routes
            if r["provider"] == "openrouter" and r["free_only"]]
    assert free, "no openrouter free route found — is OPENROUTER_API_KEY set?"
    client._routes = free
    client._route_idx = 0
    client.provider = "openrouter"
    client._client = client._client_for(free[0])
    # Generous per-request cap; the walk has exactly one route anyway.
    client._walk_timeout = 180.0
    return client


def one_request(client: orc.OpenRouterClient, i: int) -> dict:
    BUCKET.take()
    # Unique suffix per request defeats the disk response cache.
    prompt = PROMPTS[i % 3] + f" [req-{i}-{int(time.time())}]"
    t0 = time.monotonic()
    rec = {"i": i, "prompt_class": ("short", "medium", "long")[i % 3]}
    try:
        res = client.chat(
            "openrouter/free",
            [{"role": "user", "content": prompt}],
            max_tokens=64,
            max_continuations=0,
            session_id=f"stress-{i}",
        )
        rec.update({
            "ok": True,
            "latency_s": round(time.monotonic() - t0, 3),
            "text_len": len(res.get("text", "") or ""),
            "cost_usd": res.get("cost_usd", 0.0),
            "cached": bool(res.get("cached", False)),
        })
    except Exception as exc:  # noqa: BLE001 - taxonomy below
        msg = str(exc)
        kind = "429" if ("429" in msg or "rate" in msg.lower()) else (
            "timeout" if "timeout" in msg.lower() else "error")
        rec.update({
            "ok": False,
            "latency_s": round(time.monotonic() - t0, 3),
            "kind": kind,
            "error": msg[:300],
        })
    return rec


def pct(values, q):
    if not values:
        return None
    values = sorted(values)
    k = max(0, min(len(values) - 1, int(round(q * (len(values) - 1)))))
    return round(values[k], 3)


class _Bucket:
    """Token bucket: THROTTLE_RPS=0 -> unlimited (pure blast)."""

    def __init__(self, rps: float, burst: int = 2):
        import threading as _t
        self.rps = rps
        self.tokens = float(burst)
        self.cap = float(burst)
        self.ts = time.monotonic()
        self.lock = _t.Lock()

    def take(self):
        if self.rps <= 0:
            return
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.cap, self.tokens + (now - self.ts) * self.rps)
                self.ts = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
            time.sleep(0.2)


BUCKET = _Bucket(float(os.environ.get("THROTTLE_RPS", "0")))


def main() -> None:
    client = build_free_only_client()
    print(f"routes: {orc.routes_health_report(client._routes)}", flush=True)
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=N) as pool:
        results = list(pool.map(lambda i: one_request(client, i), range(N)))
    wall = round(time.monotonic() - t0, 2)

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    lats = [r["latency_s"] for r in ok]
    report = {
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "concurrency": N,
        "wall_s": wall,
        "success": len(ok),
        "failed": len(bad),
        "dropped": len([r for r in bad if r.get("kind") != "429"]),
        "rate_limited_429": len([r for r in bad if r.get("kind") == "429"]),
        "latency_p50_s": pct(lats, 0.50),
        "latency_p95_s": pct(lats, 0.95),
        "latency_p99_s": pct(lats, 0.99),
        "latency_max_s": max(lats) if lats else None,
        "total_cost_usd": round(sum(r.get("cost_usd", 0.0) for r in ok), 6),
        "errors": {r.get("kind", "error"): len([x for x in bad
                    if x.get("kind") == r.get("kind")]) for r in bad},
        "samples": sorted(results, key=lambda r: r["i"]),
    }
    suffix = os.environ.get("REPORT_SUFFIX", "")
    out = ROOT / "data" / "eval_reports" / f"stress_free_tier_2026-08-10{suffix}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "samples"},
                     indent=2))
    print(f"saved -> {out}")


if __name__ == "__main__":
    main()
