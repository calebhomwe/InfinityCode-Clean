"""Probe direct DeepSeek API + any other direct keys in infra .env (masked)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

env: dict[str, str] = {}
p = Path("D:/genesis/infra/.env")
if p.is_file():
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

print("key names in infra .env:", sorted(env.keys()))

ds = env.get("DEEPSEEK_API_KEY", "")
print(f"deepseek key present: {bool(ds)}")
if ds:
    for m in ("deepseek-v4-flash", "deepseek-v4-pro"):
        body = json.dumps({
            "model": m, "max_tokens": 16, "temperature": 0.2,
            "messages": [{"role": "user", "content": "Reply: OK"}],
        }).encode()
        req = urllib.request.Request(
            "https://api.deepseek.com/chat/completions", data=body,
            headers={"Authorization": f"Bearer {ds}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read().decode())
            print(f"OK   {m} tokens={d.get('usage', {}).get('total_tokens')}")
        except urllib.error.HTTPError as e:
            print(f"FAIL {m} HTTP {e.code} {e.read().decode()[:110]}")
        except Exception as e:
            print(f"FAIL {m} {str(e)[:80]}")
