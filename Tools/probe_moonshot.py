"""Probe Moonshot direct API with backend/moonshot.key. Prints no key values."""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
key_file = ROOT / "backend" / "moonshot.key"
key = key_file.read_text(encoding="utf-8").strip() if key_file.is_file() else ""
print(f"[moonshot] key_present={bool(key)} len={len(key)}")
if not key:
    raise SystemExit(0)


def get(url: str, timeout: int = 25) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)[:200]}


st, data = get("https://api.moonshot.ai/v1/models")
if st == 200:
    ids = sorted(m.get("id", "") for m in data.get("data", []))
    k26 = [i for i in ids if "k2.6" in i or "k2.5" in i or "k3" in i]
    print(f"[moonshot] auth OK, models={len(ids)}")
    print("[moonshot] k2.5/k2.6/k3 slugs:", k26 if k26 else ids[:20])
else:
    print(f"[moonshot] models status={st} body={str(data)[:200]}")

# Balance / user info endpoints (Moonshot exposes /v1/users/me on some accounts)
for path in ("https://api.moonshot.ai/v1/users/me",):
    st2, d2 = get(path)
    print(f"[moonshot] {path.split('/v1/')[1]} status={st2} body={str(d2)[:200]}")
