"""Infinity CLI — thin terminal client over the Infinity Code FastAPI backend.

Reuses the existing engine (routing, caching, ascension, swarm); this is a
terminal face only. Talks to http://127.0.0.1:8000, same origin the desktop
app uses, with the same session-token auth (GET /api/v1/auth/token).

Commands:
    infinity status                backend health, providers (masked), model count
    infinity models                list chat models
    infinity run "prompt"          one-shot: create a chat, stream one reply, print it
    infinity run "prompt" --bfb    same, but BFB-lane model routing
    infinity health                dependency health (routes, keys, MCP, data dir)
    infinity debug on|off|status   runtime verbose debug mode
    infinity routes-reset          clear route-health (unstick DEAD routes)
    infinity cache-clear           drop the LLM response cache

No new dependencies (stdlib urllib). Exit codes: 0 ok, 1 backend unreachable,
2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

ORIGIN = "http://127.0.0.1:8000"
API = ORIGIN + "/api/v1"

_token: Optional[str] = None


def fetch_token() -> Optional[str]:
    global _token
    if _token:
        return _token
    try:
        with urllib.request.urlopen(API + "/auth/token", timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _token = str(data.get("token") or "")
        return _token or None
    except Exception:  # noqa: BLE001
        return None


def request(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
    token = fetch_token()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(ORIGIN + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_status() -> int:
    try:
        with urllib.request.urlopen(API + "/health", timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"backend UNREACHABLE at {ORIGIN}: {exc}")
        return 1
    print(f"backend: {health.get('status', '?')} v{health.get('version', '?')}")
    token = fetch_token()
    print("auth: " + ("ok" if token else "no session token (backend may require none)"))
    try:
        info = request("GET", "/api/v1/providers")
        configured = info.get("configured", {})
        on = [k for k, v in configured.items() if v]
        print("providers configured: " + (", ".join(on) if on else "none"))
    except Exception as exc:  # noqa: BLE001
        print(f"providers: unavailable ({exc})")
    return 0


def cmd_models() -> int:
    try:
        data = request("GET", "/api/v1/chat/models")
    except Exception as exc:  # noqa: BLE001
        print(f"models: unavailable ({exc})")
        return 1
    models = data if isinstance(data, list) else data.get("models", data.get("ids", []))
    for m in models:
        print(m if isinstance(m, str) else json.dumps(m))
    return 0


def cmd_health() -> int:
    try:
        data = request("GET", "/api/v1/health/dependencies")
    except Exception as exc:  # noqa: BLE001
        print(f"dependencies: unavailable ({exc})")
        return 1
    print(f"status: {data.get('status')}")
    for r in data.get("provider_routes", []):
        print(f"  route {r.get('provider')}/{r.get('stage')}: {r.get('state')}")
    print("openrouter key: " + ("set" if data.get("openrouter_key") else "MISSING"))
    for s in data.get("mcp", []):
        state = "connected" if s.get("connected") else "DOWN"
        print(f"  mcp {s.get('name')}: {state} ({s.get('tool_count')} tools)")
    print("data dir writable: " + str(bool(data.get("data_dir_writable"))))
    return 0


def cmd_debug(mode: str) -> int:
    try:
        if mode == "status":
            data = request("GET", "/api/v1/debug/state")
        else:
            data = request("POST", "/api/v1/debug/verbose",
                           {"enabled": mode == "on"})
    except Exception as exc:  # noqa: BLE001
        print(f"debug: unavailable ({exc})")
        return 1
    print(f"debug: {'ON' if data.get('debug') else 'off'} "
          f"(level {data.get('level')})")
    return 0


def cmd_routes_reset() -> int:
    try:
        data = request("POST", "/api/v1/routes/reset")
    except Exception as exc:  # noqa: BLE001
        print(f"routes-reset: unavailable ({exc})")
        return 1
    print(f"route health cleared: {data.get('cleared')} entries")
    return 0


def cmd_cache_clear() -> int:
    try:
        data = request("POST", "/api/v1/cache/clear")
    except Exception as exc:  # noqa: BLE001
        print(f"cache-clear: unavailable ({exc})")
        return 1
    print(f"llm cache cleared: {data.get('cleared')} entries")
    return 0


def cmd_run(prompt: str, bfb: bool) -> int:
    try:
        chat = request("POST", "/api/v1/chats", {"title": prompt[:40]})
    except Exception as exc:  # noqa: BLE001
        print(f"create chat failed: {exc}")
        return 1
    chat_id = chat.get("id") or chat.get("chat_id")
    if not chat_id:
        print("create chat: unexpected response", json.dumps(chat)[:300])
        return 1

    payload: Dict[str, Any] = {"content": prompt}
    if bfb:
        payload["model"] = "deepseek/deepseek-v4-flash"

    token = fetch_token()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        f"{API}/chats/{chat_id}/stream",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    out: List[str] = []
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if not body:
                    continue
                try:
                    evt = json.loads(body)
                except json.JSONDecodeError:
                    continue
                for key in ("delta", "text", "content", "token"):
                    val = evt.get(key)
                    if isinstance(val, str) and val:
                        out.append(val)
                        break
                if evt.get("done") or evt.get("error"):
                    if evt.get("error"):
                        print("stream error:", evt["error"], file=sys.stderr)
                    break
    except Exception as exc:  # noqa: BLE001
        print(f"stream failed: {exc}")
        return 1
    sys.stdout.write("".join(out) + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="infinity", description="Infinity Code terminal client")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="backend health, auth, providers")
    sub.add_parser("models", help="list chat models")

    run = sub.add_parser("run", help="one-shot prompt via chat stream")
    run.add_argument("prompt", help="the prompt to send")
    run.add_argument("--bfb", action="store_true", help="BFB-lane model routing")

    sub.add_parser("health", help="dependency health report")
    dbg = sub.add_parser("debug", help="runtime verbose debug mode")
    dbg.add_argument("mode", choices=["on", "off", "status"])
    sub.add_parser("routes-reset", help="clear route-health (unstick DEAD routes)")
    sub.add_parser("cache-clear", help="drop the LLM response cache")

    args = parser.parse_args()
    if args.command == "status":
        return cmd_status()
    if args.command == "models":
        return cmd_models()
    if args.command == "run":
        return cmd_run(args.prompt, args.bfb)
    if args.command == "health":
        return cmd_health()
    if args.command == "debug":
        return cmd_debug(args.mode)
    if args.command == "routes-reset":
        return cmd_routes_reset()
    if args.command == "cache-clear":
        return cmd_cache_clear()
    return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
