"""Probe OpenRouter with REAL tiny chat calls (auth endpoint lied last time)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

env = {}
for p in (Path.home() / ".env",):
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

key = env.get("OPENROUTER_API_KEY", "")
print(f"key_present={bool(key)}")

CANDIDATES = [
    "deepseek/deepseek-v4-flash",
    "moonshotai/kimi-k2.6",
    "z-ai/glm-4.7-flash",
    "qwen/qwen3.8-max",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
]
for m in CANDIDATES:
    body = json.dumps({
        "model": m, "max_tokens": 16, "temperature": 0.2,
        "messages": [{"role": "user", "content": "Reply: OK"}],
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "HTTP-Referer": "https://genesis.game",
                 "X-Title": "GENESIS"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode())
        print(f"OK   {m} tokens={d.get('usage', {}).get('total_tokens')}")
    except urllib.error.HTTPError as e:
        print(f"FAIL {m} HTTP {e.code} {e.read().decode()[:110]}")
    except Exception as e:
        print(f"FAIL {m} {str(e)[:80]}")
