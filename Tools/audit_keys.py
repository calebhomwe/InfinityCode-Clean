"""Live key audit: print the failover chain and probe every route."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.tools import openrouter_client as orc  # noqa: E402

print("INFINITY_LLM_PROVIDER env:",
      repr(__import__("os").environ.get("INFINITY_LLM_PROVIDER")))

routes = orc.collect_routes()
print("chain order (as collected):")
for i, r in enumerate(routes):
    print(f"  {i} {r['provider']:<10} {r['source']:<12} "
          f"{'free' if r['free_only'] else 'paid'}  {r['key'][:6]}..{r['key'][-4:]}")

PROBE = {"dashscope": "qwen-turbo", "deepseek": "deepseek-v4-flash",
         "moonshot": "kimi-k2.6"}
print("probing each route with a tiny call:")
for r in routes:
    model = PROBE.get(r["provider"],
                      "deepseek/deepseek-chat:free" if r["free_only"]
                      else "deepseek/deepseek-v4-flash")
    try:
        orc.OpenAI(api_key=r["key"], base_url=r["base_url"],
                   timeout=12).chat.completions.create(
            model=model, messages=[{"role": "user", "content": "ping"}],
            max_tokens=4)
        state = "LIVE"
    except Exception as exc:  # noqa: BLE001
        state = f"DEAD ({str(exc)[:80]})" if orc.is_route_fatal(exc) \
            else f"transient ({str(exc)[:60]})"
    print(f"  {r['provider']:<10} {r['source']:<12} -> {state}")
