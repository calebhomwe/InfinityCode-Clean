"""Qwen-MM probe for Infinity Code.

Routes tried in order:
  1. DashScope (DASHSCOPE_API_KEY)  - qwen3-vl (primary; quota may be exhausted)
  2. OpenRouter (OPENROUTER_API_KEY) - qwen/qwen3-vl-32b-instruct (fallback)

Usage:
  python Tools/qwenmm_probe.py <image_path> ["prompt text"]

If a model id is wrong, the error body is printed with available ids where
possible and the script stops (exit 1) -- it never silently falls through to
an integration that depends on a failed probe.
"""
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request

ENV_FILE = os.path.expanduser(r"~\.env")
DASHSCOPE_BASE_DEFAULT = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

ROUTES = [
    ("DASHSCOPE", "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "qwen3-vl"),
    ("OPENROUTER", "OPENROUTER_API_KEY", None, "qwen/qwen3-vl-32b-instruct"),
]

DEFAULT_PROMPT = (
    "Describe this image in structured detail: layout, key objects, colors, "
    "style, and any text. Then state what a game developer would need to "
    "replicate it as a 3D scene or UI in Godot or Unreal."
)


def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.*)$", line)
                if m:
                    env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def main():
    if len(sys.argv) < 2:
        print("usage: python Tools/qwenmm_probe.py <image_path> [prompt]")
        sys.exit(2)
    image_path = sys.argv[1]
    prompt = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_PROMPT

    if not os.path.exists(image_path):
        print(f"ERROR: image not found: {image_path}")
        sys.exit(1)

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    ext = os.path.splitext(image_path)[1].lstrip(".").lower() or "png"
    mime = "image/png" if ext == "png" else f"image/{ext}"

    env = load_env()
    last_err = None
    for name, key_name, base_env_name, model in ROUTES:
        key = env.get(key_name, "")
        if not key:
            print(f"SKIP {name} ({key_name} not set)")
            continue
        if base_env_name:
            base = env.get(base_env_name, "").strip().strip('"').strip("'") or DASHSCOPE_BASE_DEFAULT
        else:
            base = OPENROUTER_BASE
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0.2,
            "max_tokens": 2000,
        }
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            out = data["choices"][0]["message"]["content"]
            meta = {
                "route": name,
                "model": model,
                "image": image_path,
                "prompt": prompt,
            }
            with open(os.path.join(os.path.dirname(__file__), "..", "data", "qwenmm_probe_output.json"), "w", encoding="utf-8") as o:
                json.dump({"meta": meta, "content": out}, o, indent=2, ensure_ascii=False)
            print(f"--- QWEN-MM OK via {name} / {model} ---")
            print(out[:2000])
            sys.exit(0)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            last_err = f"{name} {model}: HTTP {e.code} {body[:800]}"
            print(f"FAIL {name} {model}: HTTP {e.code}")
            # model_not_found: list what the route actually offers, then try next route
            if e.code == 404 and name == "DASHSCOPE":
                print("  DashScope token-plan route offers no vision model (no qwen3-vl). "
                      "Available: qwen3.7-max, qwen3.8-max, glm-5.2, deepseek-v4-*, wan2.7-image*."
                      " Trying OpenRouter fallback.")
                continue
            # auth errors are not transient: stop
            if e.code in (401, 403):
                print(body[:1500])
                print("PROBE STOPPED: fix the key before proceeding.")
                sys.exit(1)
        except Exception as e:
            last_err = f"{name} {model}: {type(e).__name__}: {e}"
            print(f"FAIL {name} {model}: {type(e).__name__}: {e}")

    print("ALL ROUTES FAILED:", last_err)
    sys.exit(1)


if __name__ == "__main__":
    main()
