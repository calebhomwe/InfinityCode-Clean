"""Probe which DashScope models still answer (free-quota check). Tiny calls."""
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

key = env.get("DASHSCOPE_API_KEY", "")
base = (env.get("DASHSCOPE_BASE_URL", "")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")

CANDIDATES = [
    "qwen3.8-max", "qwen3-max", "qwen3.7-max", "qwen3.6-max-preview",
    "qwen3-coder-plus", "qwen3-coder-flash", "qwen3-coder",
    "qwen3-vl-plus", "qwen3-vl-flash", "qwen-vl-max",
    "qwen3.6-flash", "qwen3.5-flash", "qwen-flash", "qwen-plus", "qwen-turbo",
]

for m in CANDIDATES:
    body = json.dumps({
        "model": m, "max_tokens": 16, "temperature": 0.2,
        "messages": [{"role": "user", "content": "Reply: OK"}],
    }).encode()
    req = urllib.request.Request(
        base + "/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            d = json.loads(r.read().decode())
        print(f"OK   {m}  tokens={d.get('usage', {}).get('total_tokens')}")
    except urllib.error.HTTPError as e:
        msg = e.read().decode()[:110].replace("\n", " ")
        print(f"FAIL {m}  HTTP {e.code} {msg}")
    except Exception as e:
        print(f"FAIL {m}  {str(e)[:80]}")
