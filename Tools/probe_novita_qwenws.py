"""Probe Novita (model list + cheap chat) and QWEN_WS_KEY2..5 (qwen3.8-max)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

env: dict[str, str] = {}
p = Path("D:/genesis/infra/.env")
for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

# --- Novita -------------------------------------------------------------
nov = env.get("NOVITA_API_KEY", "")
print(f"novita key present: {bool(nov)}")
if nov:
    for base in ("https://api.novita.ai/v3/openai",
                 "https://api.novita.ai/openai",
                 "https://api.novita.ai/v1"):
        try:
            req = urllib.request.Request(
                base + "/models",
                headers={"Authorization": f"Bearer {nov}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode())
            ids = [m.get("id", "") for m in d.get("data", [])]
            want = [i for i in ids if any(w in i.lower() for w in
                    ("kimi", "glm", "deepseek", "qwen"))]
            print(f"novita base OK: {base} models={len(ids)}")
            print("  interesting:", sorted(want)[:20])
            break
        except urllib.error.HTTPError as e:
            print(f"novita {base} HTTP {e.code}")
        except Exception as e:
            print(f"novita {base} {str(e)[:60]}")

# --- Qwen workspace keys 2..5 -------------------------------------------
base_intl = env.get("QWEN_WS_BASE_INTL", "").rstrip("/")
base_csv = env.get("QWEN_WS_BASE_CSV", "").rstrip("/")
for i in ("", "2", "3", "4", "5"):
    key = env.get(f"QWEN_WS_KEY{i}", "")
    if not key:
        continue
    for base in filter(None, [base_intl, base_csv]):
        body = json.dumps({
            "model": "qwen3.8-max", "max_tokens": 16, "temperature": 0.2,
            "messages": [{"role": "user", "content": "Reply: OK"}],
        }).encode()
        req = urllib.request.Request(
            base + "/chat/completions", data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.loads(r.read().decode())
            print(f"OK   QWEN_WS_KEY{i} @ {base.split('//')[0]}//... "
                  f"tokens={d.get('usage', {}).get('total_tokens')}")
            break
        except urllib.error.HTTPError as e:
            print(f"FAIL QWEN_WS_KEY{i} @ ...{base[-12:]} HTTP {e.code} "
                  f"{e.read().decode()[:70]}")
        except Exception as e:
            print(f"FAIL QWEN_WS_KEY{i} {str(e)[:60]}")
