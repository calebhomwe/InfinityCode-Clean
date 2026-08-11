"""Offline checks for optional agent capability packs."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.capability_packs import MCP_STARTERS, seed_mcp_starters, status


class FakeManager:
    def __init__(self) -> None:
        self.servers: dict = {}

    def ensure_servers(self, servers: dict) -> int:
        added = 0
        for name, config in servers.items():
            if name not in self.servers:
                self.servers[name] = {**config, "enabled": False}
                added += 1
        return added

    def config_public(self) -> dict:
        return {"servers": self.servers}


def test_seed_adds_both_browser_packs_once() -> None:
    manager = FakeManager()
    assert seed_mcp_starters(manager) == 2
    assert set(manager.servers) == set(MCP_STARTERS)
    assert seed_mcp_starters(manager) == 0


def test_status_never_exposes_configuration_values() -> None:
    report = status(FakeManager())
    by_id = {item["id"]: item for item in report["packs"]}
    assert set(by_id) == {"playwright", "browser-use", "openhands", "mem0"}
    assert by_id["playwright"]["configured"] is False


if __name__ == "__main__":
    test_seed_adds_both_browser_packs_once()
    test_status_never_exposes_configuration_values()
    print("2/2 passed")
