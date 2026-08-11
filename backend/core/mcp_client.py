"""MCP (Model Context Protocol) client manager.

Lets Infinity Code connect to MCP servers — the same servers Claude Desktop /
Claude Code use — and expose their tools to the chat + Executive Assistant.
Config uses the standard `mcpServers` map (command/args/env for stdio, or url
for HTTP). Sessions are long-lived: a dedicated background asyncio loop owns
the connections, and tool calls are dispatched onto it from the sync request
path via run_coroutine_threadsafe.

Tools are surfaced with the conventional `mcp__<server>__<tool>` name so they
sit alongside the built-in toolbelt in the model's function list.
"""

from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Imported at module top (not lazily) so a frozen-build packaging failure shows
# up in the smoke test rather than silently at first MCP connect.
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("infinity.mcp")

# Circuit breaker for flaky MCP servers: after N consecutive call failures
# the circuit opens for a cooldown and calls fail fast (a 90s timeout per
# call would otherwise stall every autonomous step).
def _breaker_threshold() -> int:
    try:
        return max(1, int(os.environ.get("INFINITY_MCP_BREAKER_THRESHOLD", "5") or 5))
    except ValueError:
        return 5

def _breaker_cooldown_s() -> float:
    try:
        return max(0.0, float(os.environ.get("INFINITY_MCP_BREAKER_COOLDOWN_S", "30") or 30))
    except ValueError:
        return 30.0

_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")

# Anything whose NAME smells like a credential is dropped, plus these exact keys.
# MCP servers do not need the app's LLM/provider keys.
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")
_EXPLICIT_DROP = {
    "OPENROUTER_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY", "DEEPSEEK_API_KEY",
    "FAL_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN", "GITHUB_TOKEN",
    "ELEVENLABS_KEY", "NOVITA_KEY", "DASHSCOPE_API_KEY", "ALIBABA_API_KEY",
}


def _safe_env_for_mcp(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Scrub secrets from the environment before handing it to an MCP server."""
    env: Dict[str, str] = {}
    for name, value in os.environ.items():
        upper = name.upper()
        if upper in _EXPLICIT_DROP:
            continue
        if any(marker in upper for marker in _SECRET_MARKERS):
            continue
        env[name] = value
    if extra:
        env.update(extra)
    return env


def _safe(part: str) -> str:
    """Sanitize a name segment. If it needs truncating (OpenRouter tool names
    cap at 64 chars), append a short content hash so two long names that share
    a prefix can't silently collide onto the same `mcp__server__tool` id."""
    cleaned = _NAME_RE.sub("_", part)
    if len(cleaned) <= 40:
        return cleaned
    digest = hashlib.sha1(part.encode("utf-8")).hexdigest()[:6]
    return cleaned[:33] + "_" + digest


class _Server:
    def __init__(self, name: str, cfg: Dict[str, Any]) -> None:
        self.name = name
        self.cfg = cfg
        self.enabled: bool = cfg.get("enabled", True)
        self.session: Any = None
        self.tools: List[Any] = []
        self.error: Optional[str] = None
        self._stop: Optional[asyncio.Event] = None


# Fresh-install default MCP fleet: the Qwen MM toolset (uvx-managed).
_DEFAULT_MCP_SERVERS: Dict[str, Dict[str, Any]] = {
    "qwen-mm": {
        "enabled": True,
        "command": "uvx",
        "args": [
            "--from",
            "qwen-mm-plugins[core] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main",
            "qwen-mm-plugins-core",
        ],
        "env": {"PYTHONUTF8": "1"},
    },
    "qwen-mm-video-edit": {
        "enabled": True,
        "command": "uvx",
        "args": [
            "--from",
            "qwen-mm-plugins[video-edit] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main",
            "qwen-mm-plugins-video-edit",
        ],
        "env": {"PYTHONUTF8": "1"},
    },
    "qwen-mm-video-memory": {
        "enabled": True,
        "command": "uvx",
        "args": [
            "--from",
            "qwen-mm-plugins[video-memory] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@main",
            "qwen-mm-plugins-video-memory",
        ],
        "env": {"PYTHONUTF8": "1"},
    },
}

class MCPManager:
    """Owns MCP server connections on a private event loop thread."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = Path(config_path)
        self.servers: Dict[str, _Server] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        # Circuit breaker state per server name: {name: {fails, open_until}}.
        self._breaker: Dict[str, Dict[str, float]] = {}
        self._breaker_lock = threading.Lock()
        self._load_config()

    # --- config ------------------------------------------------------------ #
    def _load_config(self) -> None:
        try:
            if self.config_path.is_file():
                # utf-8-sig tolerates a BOM from hand-edited / Windows-written files.
                data = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
                servers = data.get("mcpServers", data)
                if isinstance(servers, dict):
                    self.servers = {
                        name: _Server(name, cfg)
                        for name, cfg in servers.items()
                        if isinstance(cfg, dict)
                    }
            else:
                # Fresh installs: seed the Qwen MM vision toolset so
                # read_image / read_video / ocr / vision_chat / ... are
                # available out of the box. uvx fetches the package on
                # first launch; PYTHONUTF8 keeps stdio encoding-safe.
                self.servers = {
                    name: _Server(name, cfg)
                    for name, cfg in _DEFAULT_MCP_SERVERS.items()
                }
                try:
                    self.config_path.parent.mkdir(parents=True, exist_ok=True)
                    self.config_path.write_text(
                        json.dumps({"mcpServers": _DEFAULT_MCP_SERVERS}, indent=2),
                        encoding="utf-8",
                    )
                except OSError as exc:
                    logger.warning("Could not write default MCP config: %s", exc)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load MCP config: %s", exc)

    def config_public(self) -> Dict[str, Any]:
        return {
            "servers": {
                name: {
                    "command": s.cfg.get("command", ""),
                    "args": s.cfg.get("args", []),
                    "url": s.cfg.get("url", ""),
                    "enabled": s.enabled,
                }
                for name, s in self.servers.items()
            }
        }

    def save_config(self, servers: Dict[str, Any]) -> None:
        clean: Dict[str, Any] = {}
        for name, cfg in servers.items():
            if not isinstance(cfg, dict):
                continue
            entry: Dict[str, Any] = {"enabled": bool(cfg.get("enabled", True))}
            if cfg.get("url"):
                entry["url"] = str(cfg["url"])
            else:
                entry["command"] = str(cfg.get("command", ""))
                entry["args"] = [str(a) for a in cfg.get("args", []) if isinstance(a, (str, int))]
                if cfg.get("env"):
                    entry["env"] = {str(k): str(v) for k, v in cfg["env"].items()}
                else:
                    # Preserve existing env (secrets aren't sent back by the UI,
                    # so a plain enable/disable toggle must not wipe them).
                    existing = self.servers.get(name)
                    if existing and existing.cfg.get("env"):
                        entry["env"] = dict(existing.cfg["env"])
            clean[name] = entry
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(
                json.dumps({"mcpServers": clean}, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.error("Could not save MCP config: %s", exc)
        self.servers = {n: _Server(n, c) for n, c in clean.items()}

    def import_servers(self, servers: Dict[str, Any]) -> int:
        """Merge servers from another mcpServers map (e.g. ~/.mcp.json).

        Returns the number actually ADDED (not the input size). New servers are
        imported disabled so nothing auto-spawns until the user opts in."""
        current = self.config_public()["servers"]
        added = 0
        for name, cfg in servers.items():
            if isinstance(cfg, dict) and name not in current:
                current[name] = {**cfg, "enabled": False}
                added += 1
        self.save_config(current)
        return added

    def ensure_servers(self, servers: Dict[str, Dict[str, Any]]) -> int:
        """Add first-party starter entries without overwriting user settings.

        Starter servers are intentionally disabled until the user enables them.
        That keeps browser credentials and external side effects opt-in while
        still making a capability discoverable in the normal MCP toolbelt.
        """
        current = self.config_public()["servers"]
        added = 0
        for name, config in servers.items():
            if name not in current:
                current[name] = {**config, "enabled": False}
                added += 1
        if added:
            self.save_config(current)
        return added

    # --- lifecycle --------------------------------------------------------- #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mcp-loop")
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run_loop(self) -> None:
        # Windows needs the Proactor loop for subprocess transports.
        if os.name == "nt":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        for name, server in self.servers.items():
            if server.enabled and (server.cfg.get("command") or server.cfg.get("url")):
                self._loop.create_task(self._serve(server))
        self._loop.run_forever()

    async def _serve(self, server: _Server) -> None:
        """Keep one server connected until stop is set.

        One failure must not kill the connection permanently: on exception the
        error is recorded and the connect is retried with bounded backoff
        (1s doubling to a 30s cap). Backoff resets after a session has been
        established. When the stop flag is set the loop exits promptly — the
        backoff sleep itself is stop-aware, so shutdown never waits on it.
        """
        server._stop = asyncio.Event()
        stop = server._stop
        backoff = 1.0
        while not stop.is_set():
            try:
                if server.cfg.get("url"):
                    from mcp.client.streamable_http import streamablehttp_client

                    async with streamablehttp_client(server.cfg["url"]) as (r, w, _):
                        async with ClientSession(r, w) as session:
                            await self._run_session(server, session)
                else:
                    env = _safe_env_for_mcp(server.cfg.get("env") or {})
                    params = StdioServerParameters(
                        command=server.cfg["command"],
                        args=[str(a) for a in server.cfg.get("args", [])],
                        env=env,
                    )
                    async with stdio_client(params) as (r, w):
                        async with ClientSession(r, w) as session:
                            await self._run_session(server, session)
                # _run_session only returns once stop is set (clean shutdown)
                # or the session ended on its own. A session that was up at
                # least once means the config works, so reset the backoff.
                backoff = 1.0
                if stop.is_set():
                    break
                server.session = None
                server.tools = []
                server.error = "session ended unexpectedly; reconnecting"
                logger.warning(
                    "MCP server %s session ended; reconnecting", server.name
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - surface, keep others alive
                server.error = str(exc)[:200]
                server.session = None
                server.tools = []
                logger.warning(
                    "MCP server %s failed: %s (retrying in %.0fs)",
                    server.name, exc, backoff,
                )
                # Bounded backoff sleep that wakes immediately if stop is set.
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2.0, 30.0)
        server.session = None

    async def _run_session(self, server: _Server, session: Any) -> None:
        await asyncio.wait_for(session.initialize(), timeout=30)
        listed = await session.list_tools()
        server.session = session
        server.tools = list(listed.tools)
        server.error = None
        logger.info("MCP server %s connected: %d tools", server.name, len(server.tools))
        assert server._stop is not None
        await server._stop.wait()

    def reconnect(self) -> None:
        """Restart the loop thread with the current config."""
        self.shutdown()
        self._load_config()
        self._ready.clear()
        self.start()

    def shutdown(self) -> None:
        loop = self._loop
        if loop is None:
            return
        # Signal each server's supervisor to exit, THEN stop the loop after a
        # short grace period so the stdio_client / ClientSession context
        # managers actually run __aexit__ and terminate their subprocesses
        # (stopping the loop immediately would orphan them, holding ports).
        for s in self.servers.values():
            if s._stop is not None:
                loop.call_soon_threadsafe(s._stop.set)
        loop.call_soon_threadsafe(lambda: loop.call_later(1.0, loop.stop))
        if self._thread:
            self._thread.join(timeout=6)
        for s in self.servers.values():
            s.session = None
        self._loop = None
        self._thread = None

    # --- tools ------------------------------------------------------------- #
    def tool_schemas(self) -> List[Dict[str, Any]]:
        schemas: List[Dict[str, Any]] = []
        for server in self.servers.values():
            if server.session is None:
                continue
            for tool in server.tools:
                params = getattr(tool, "inputSchema", None) or {
                    "type": "object",
                    "properties": {},
                }
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": f"mcp__{_safe(server.name)}__{_safe(tool.name)}",
                            "description": (getattr(tool, "description", "") or tool.name)[:1000],
                            "parameters": params,
                        },
                    }
                )
        return schemas

    def tool_names(self) -> List[str]:
        return [s["function"]["name"] for s in self.tool_schemas()]

    def _resolve(self, fq_name: str) -> Optional[tuple]:
        # fq_name = mcp__<serverSafe>__<toolSafe>; match against real names.
        for server in self.servers.values():
            if server.session is None:
                continue
            for tool in server.tools:
                if fq_name == f"mcp__{_safe(server.name)}__{_safe(tool.name)}":
                    return server, tool.name
        return None

    def _breaker_record(self, name: str, ok: bool) -> None:
        with self._breaker_lock:
            st = self._breaker.setdefault(name, {"fails": 0.0, "open_until": 0.0})
            if ok:
                st["fails"] = 0.0
                st["open_until"] = 0.0
            else:
                st["fails"] += 1
                if st["fails"] >= _breaker_threshold():
                    st["open_until"] = time.monotonic() + _breaker_cooldown_s()

    def _breaker_open(self, name: str) -> bool:
        with self._breaker_lock:
            st = self._breaker.get(name)
            return bool(st and time.monotonic() < st["open_until"])

    def call(self, fq_name: str, arguments: Dict[str, Any]) -> str:
        resolved = self._resolve(fq_name)
        if resolved is None:
            return f"MCP tool {fq_name} is not connected."
        server, tool_name = resolved
        if self._breaker_open(server.name):
            self._breaker_record(server.name, False)
            return (
                f"MCP call {fq_name} skipped: circuit breaker open for server "
                f"{server.name} (too many consecutive failures)."
            )
        loop = self._loop
        if loop is None or server.session is None:
            return "MCP is not running."
        try:
            fut = asyncio.run_coroutine_threadsafe(
                server.session.call_tool(tool_name, arguments or {}), loop
            )
            result = fut.result(timeout=90)
        except Exception as exc:  # noqa: BLE001
            self._breaker_record(server.name, False)
            return f"MCP call {fq_name} failed: {str(exc)[:200]}"
        self._breaker_record(server.name, True)
        parts: List[str] = []
        for item in getattr(result, "content", []) or []:
            text = getattr(item, "text", None)
            if text:
                parts.append(text)
        return ("\n".join(parts) or "(no content)")[:6000]

    def status(self) -> Dict[str, Any]:
        return {
            "servers": [
                {
                    "name": s.name,
                    "enabled": s.enabled,
                    "connected": s.session is not None,
                    "tool_count": len(s.tools),
                    "tools": [t.name for t in s.tools][:40],
                    "error": s.error,
                }
                for s in self.servers.values()
            ]
        }


__all__ = ["MCPManager"]
