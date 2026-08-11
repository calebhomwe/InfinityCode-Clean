"""Regression checks for the composer access-mode contract."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.tools_registry import gates_for_approval_mode, risk_of


def test_ask_gates_external_and_local_risks() -> None:
    gates = gates_for_approval_mode("ask")
    assert {"web", "fs", "screen", "write", "mcp", "paid"} <= gates
    assert risk_of("web_search") == "web"
    assert risk_of("see_image") == "fs"


def test_smart_and_full_modes_are_distinct() -> None:
    assert "web" not in gates_for_approval_mode("smart")
    assert "write" in gates_for_approval_mode("smart")
    assert gates_for_approval_mode("full") == frozenset()
    assert gates_for_approval_mode("not-a-mode") == gates_for_approval_mode("smart")


if __name__ == "__main__":
    test_ask_gates_external_and_local_risks()
    test_smart_and_full_modes_are_distinct()
    print("2/2 passed")
