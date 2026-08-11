"""One-shot key probe: which model endpoints are live for the premium swarm.

Prints model ids and auth status ONLY. Never prints key values.
Usage: .venv\\Scripts\\python.exe Tools\\probe_keys.py
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()


def load_env(path: Path) -> dict:
    out = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


env = {**load_env(HOME / ".env"), **load_env(ROOT / ".env")}


def get(url: str, key: str | None, timeout: int = 25) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if key:
        req.add_header("Authorization", f"Bearer {key}")
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


# 1. moonshot.key presence
mk = ROOT / "backend" / "moonshot.key"
print(f"[moonshot.key] exists={mk.is_file()} size={mk.stat().st_size if mk.is_file() else 0}")

# 2. OpenRouter: auth/credits + model slugs
or_key = env.get("OPENROUTER_API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")
print(f"[openrouter] key_present={bool(or_key)}")
if or_key:
    st, data = get("https://openrouter.ai/api/v1/auth/key", or_key)
    d = data.get("data", {})
    print(f"[openrouter] auth status={st} usage=${d.get('usage')} limit={d.get('limit')} is_free={d.get('is_free_key')}")
    st, data = get("https://openrouter.ai/api/v1/models", or_key)
    ids = [m.get("id", "") for m in data.get("data", [])]
    kimi = sorted(i for i in ids if "kimi" in i.lower() or "moonshot" in i.lower())
    qwen = sorted(i for i in ids if "qwen3.8" in i.lower() or "qwen3.7" in i.lower() or "qwen3-max" in i.lower())
    print(f"[openrouter] total_models={len(ids)}")
    print("[openrouter] kimi slugs:", kimi if kimi else "NONE")
    print("[openrouter] qwen3.7/3.8 slugs:", qwen if qwen else "NONE")

# 3. DashScope (Qwen direct)
ds_key = env.get("DASHSCOPE_API_KEY", "") or os.environ.get("DASHSCOPE_API_KEY", "")
ds_base = (env.get("DASHSCOPE_BASE_URL", "") or os.environ.get("DASHSCOPE_BASE_URL", "")).rstrip("/")
print(f"[dashscope] key_present={bool(ds_key)} base={ds_base or '(unset)'}")
if ds_key:
    # OpenAI-compatible listing; try common roots without echoing secrets
    candidates = []
    if ds_base:
        candidates.append(ds_base if ds_base.endswith("/models") else ds_base + "/models")
    candidates.append("https://dashscope.aliyuncs.com/compatible-mode/v1/models")
    seen = False
    for url in dict.fromkeys(candidates):
        st, data = get(url, ds_key)
        if st == 200 and isinstance(data.get("data"), list):
            ids = [m.get("id", "") for m in data["data"]]
            q38 = [i for i in ids if "3.8" in i or "3-max" in i]
            vl = [i for i in ids if "vl" in i][:8]
            print(f"[dashscope] {url.split('//')[0]}//*** status=200 models={len(ids)}")
            print("[dashscope] qwen3.8/3-max slugs:", sorted(q38)[:12] if q38 else "NONE in listing")
            print("[dashscope] vl slugs sample:", sorted(vl))
            seen = True
            break
        else:
            print(f"[dashscope] probe {url.split('//')[0]}//*** status={st} err={str(data)[:120]}")
    if not seen:
        print("[dashscope] no listing endpoint answered; will need a tiny chat probe later")
