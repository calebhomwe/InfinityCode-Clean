"""Probe every OPENROUTER_API_KEY found in known env stores (values masked)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

STORES = [
    Path.home() / ".env",
    Path("D:/genesis/infra/.env"),
    Path.home() / "infinity-code" / ".env",
    Path.home() / ".qwen" / ".env",
]

keys: dict[str, str] = {}
for p in STORES:
    if not p.is_file():
        continue
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line.startswith("OPENROUTER_API_KEY="):
            v = line.split("=", 1)[1].strip().strip('"').strip("'")
            if v:
                keys.setdefault(f"{p}::{v[:6]}...{v[-4:]}", v)

print(f"distinct openrouter keys found: {len(keys)}")
for label, key in keys.items():
    body = json.dumps({
        "model": "deepseek/deepseek-v4-flash", "max_tokens": 16,
        "temperature": 0.2,
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
        print(f"OK   {label} tokens={d.get('usage', {}).get('total_tokens')}")
    except urllib.error.HTTPError as e:
        print(f"FAIL {label} HTTP {e.code} {e.read().decode()[:90]}")
    except Exception as e:
        print(f"FAIL {label} {str(e)[:80]}")
