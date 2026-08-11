"""Tiny real chat probe against backend/moonshot.key (kimi-k2.6). ~20 tokens."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
key = (ROOT / "backend" / "moonshot.key").read_text(encoding="utf-8").strip()

body = json.dumps({
    "model": "kimi-k2.6",
    "temperature": 1.0,
    "max_tokens": 60,
    "messages": [
        {"role": "system", "content": "Reply with exactly: KIMI K2.6 ONLINE"},
        {"role": "user", "content": "ping"},
    ],
}).encode()

req = urllib.request.Request(
    "https://api.moonshot.ai/v1/chat/completions",
    data=body,
    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode())
    msg = d["choices"][0]["message"]
    print("STATUS: OK")
    print("content:", (msg.get("content") or "").strip()[:200])
    print("usage:", d.get("usage"))
except urllib.error.HTTPError as e:
    print(f"STATUS: HTTP {e.code}")
    print(e.read().decode()[:300])
except Exception as e:
    print("STATUS: ERR", str(e)[:300])
