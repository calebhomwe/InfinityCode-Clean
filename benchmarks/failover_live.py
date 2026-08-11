"""Part 2.2 — LIVE free->paid failover validation.

Chain: [openrouter free, openrouter paid]. A 12-request burst exhausts the
free stage's upstream per-minute quota (observed live 2026-08-10), forcing
the automatic swap to the paid route. Measures: zero dropped requests,
swap latency (first rate-limit failure -> route swap, via log timestamps),
and cost attribution ($0 free answers, priced paid answers).

Spend guard: max_tokens=48, cheap model; expected < $0.01 total.
"""
from __future__ import annotations

import json
import logging
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

N = 20  # >20 req/min quota + 3 transient retries guarantees the swap fires
MODEL = "deepseek/deepseek-chat"  # priced; free stage maps to openrouter/free


class _Stamp(logging.Handler):
    """Capture the two events that bracket the SLA: first rate-limit failure
    and the route-swap that follows it."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.events: list = []

    def emit(self, record):
        msg = record.getMessage()
        t = record.created
        if "Failover: swapped LLM route" in msg:
            self.events.append(("swap", t, msg[:120]))
        elif ("marked DEAD" in msg and ("429" in msg or "rate" in msg.lower())):
            # 429s are route-fatal: the observable failure event is the
            # dead-marking (no 'LLM call failed' warning is logged).
            self.events.append(("fail429", t, msg[:160]))
        elif "LLM call failed" in msg and ("429" in msg or "rate" in msg.lower()):
            self.events.append(("fail429", t, msg[:160]))


def build_free_then_paid_client() -> orc.OpenRouterClient:
    """Full production chain with the free-router stage pinned first:
    free -> dashscope -> openrouter-paid -> deepseek -> moonshot ... so the
    free->paid swap has working destinations even when one paid route is
    broken (2026-08-11: OpenRouter paid answered NOT_ENOUGH_BALANCE)."""
    client = orc.OpenRouterClient()
    free = [r for r in client._routes
            if r["provider"] == "openrouter" and r["free_only"]]
    paid = [r for r in client._routes
            if not (r["provider"] == "openrouter" and r["free_only"])]
    assert free and paid, "need free router + at least one paid route"
    client._routes = free + paid
    client._route_idx = 0
    client.provider = "openrouter"
    client._client = client._client_for(free[0])
    client._walk_timeout = 180.0
    return client


def one_request(client, i):
    prompt = (f"Answer in one sentence: what is {i} + {i}? "
              f"[fo-{i}-{int(time.time())}]")
    t0 = time.monotonic()
    rec = {"i": i}
    try:
        res = client.chat(
            MODEL,
            [{"role": "user", "content": prompt}],
            max_tokens=48,
            max_continuations=0,
            session_id=f"failover-{i}",
        )
        rec.update({"ok": True, "latency_s": round(time.monotonic() - t0, 3),
                    "cost_usd": res.get("cost_usd", 0.0),
                    "text_len": len(res.get("text", "") or "")})
    except Exception as exc:  # noqa: BLE001
        rec.update({"ok": False, "latency_s": round(time.monotonic() - t0, 3),
                    "error": str(exc)[:250]})
    return rec


def main():
    stamp = _Stamp()
    orc.logger.setLevel(logging.DEBUG)  # swap events are INFO
    orc.logger.addHandler(stamp)
    client = build_free_then_paid_client()
    print(f"routes: {orc.routes_health_report(client._routes)}", flush=True)
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=N) as pool:
        results = list(pool.map(lambda i: one_request(client, i), range(N)))
    wall = round(time.monotonic() - t0, 2)

    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    free_calls = [e for e in client.call_log if e["model"] == "openrouter/free"]
    paid_calls = [e for e in client.call_log if e["model"] != "openrouter/free"]
    by_provider: dict = {}
    for e in client.call_log:
        key = "free-router" if e["model"] == "openrouter/free" else e["model"]
        by_provider[key] = by_provider.get(key, 0) + 1
    # SLA: first 429-class failure -> swap must happen <2s later.
    first_fail = next((e for e in stamp.events if e[0] == "fail429"), None)
    first_swap = next((e for e in stamp.events if e[0] == "swap"), None)
    swap_gap = (round(first_swap[1] - first_fail[1], 3)
                if first_fail and first_swap else None)
    report = {
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "requests": N,
        "wall_s": wall,
        "answered": len(ok),
        "dropped": len(bad),
        "failover_observed": bool(first_swap),
        "swap_gap_s_after_first_429": swap_gap,
        "free_served": len(free_calls),
        "paid_served": len(paid_calls),
        "served_by_model": by_provider,
        "free_cost_usd": round(sum(e["cost_usd"] for e in free_calls), 6),
        "paid_cost_usd": round(sum(e["cost_usd"] for e in paid_calls), 6),
        "total_cost_usd": round(client.total_cost_usd, 6),
        "final_provider": client.provider,
        "route_states": orc.routes_health_report(client._routes),
        "samples": results,
    }
    out = ROOT / "data" / "eval_reports" / "failover_live_v4_2026-08-11.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "samples"},
                     indent=2))
    print(f"saved -> {out}")


if __name__ == "__main__":
    main()
