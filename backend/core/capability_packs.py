"""Optional capability packs that integrate cleanly with Infinity's toolbelt.

The pack registry is deliberately dependency-light. It registers safe, disabled
MCP entries for browser automation, and reports the availability of optional
local services without starting extra processes or inheriting secrets.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from typing import Any, Dict


MCP_STARTERS: Dict[str, Dict[str, Any]] = {
    "playwright": {
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest", "--isolated"],
    },
    "browser-use": {
        "command": "uvx",
        "args": ["--from", "browser-use[cli]", "browser-use", "--mcp"],
        "env": {"BROWSER_USE_HEADLESS": "true"},
    },
}


def seed_mcp_starters(manager: Any) -> int:
    """Register browser tool entries once, preserving every user edit."""
    return int(manager.ensure_servers(MCP_STARTERS))


def status(manager: Any) -> Dict[str, Any]:
    """Return a no-secret status report for optional agent integrations."""
    configured = manager.config_public().get("servers", {}) if manager else {}
    return {
        "packs": [
            {
                "id": "playwright",
                "label": "Playwright MCP",
                "kind": "browser_mcp",
                "configured": "playwright" in configured,
                "enabled": bool(configured.get("playwright", {}).get("enabled", False)),
                "ready": shutil.which("npx") is not None,
                "note": "Structured browser testing and UI verification.",
            },
            {
                "id": "browser-use",
                "label": "Browser Use",
                "kind": "browser_mcp",
                "configured": "browser-use" in configured,
                "enabled": bool(configured.get("browser-use", {}).get("enabled", False)),
                "ready": shutil.which("uvx") is not None,
                "note": "Agentic browser sessions through MCP; requires its own model credentials.",
            },
            {
                "id": "openhands",
                "label": "OpenHands",
                "kind": "external_agent_runtime",
                "configured": bool(os.environ.get("OPENHANDS_BASE_URL")),
                "enabled": bool(os.environ.get("OPENHANDS_ENABLED")),
                "ready": shutil.which("openhands") is not None or bool(os.environ.get("OPENHANDS_BASE_URL")),
                "note": "Optional isolated coding-agent runtime; connect it with OPENHANDS_BASE_URL.",
            },
            {
                "id": "mem0",
                "label": "Mem0",
                "kind": "memory_provider",
                "configured": bool(os.environ.get("MEM0_API_KEY")),
                "enabled": bool(os.environ.get("MEM0_ENABLED")),
                "ready": importlib.util.find_spec("mem0") is not None,
                "note": "Optional long-term memory provider; native private memory remains the default.",
            },
        ]
    }


__all__ = ["MCP_STARTERS", "seed_mcp_starters", "status"]
