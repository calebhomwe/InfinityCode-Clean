"""Agent Library — 245 specialist personas bundled from agency-agents (MIT).

The catalog (backend/data/agents.json) is prebuilt from
github.com/msitarzewski/agency-agents. Each agent has YAML-frontmatter metadata
(name, description, emoji, color, vibe, division) plus a markdown body that is
used verbatim as the system prompt when the agent is activated in a chat.

`list()` returns a light view (no prompt bodies) for the browser UI; `get()` /
`prompt_for()` return the full agent for activation.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LIGHT_FIELDS = ("id", "division", "name", "description", "emoji", "color", "vibe")


class AgentLibrary:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._divisions: Dict[str, Any] = {}
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:  # non-fatal: app still runs
            logger.warning("Agent library unavailable (%s): %s", self.path, exc)
            return
        self._divisions = data.get("divisions", {}) or {}
        for a in data.get("agents", []):
            aid = a.get("id")
            if aid:
                self._agents[aid] = a
                self._order.append(aid)
        logger.info("Agent library: %d agents loaded.", len(self._agents))

    @property
    def count(self) -> int:
        return len(self._agents)

    @staticmethod
    def _light(a: Dict[str, Any]) -> Dict[str, Any]:
        return {k: a.get(k) for k in _LIGHT_FIELDS}

    def list(self) -> Dict[str, Any]:
        return {
            "divisions": self._divisions,
            "count": len(self._agents),
            "agents": [self._light(self._agents[i]) for i in self._order],
        }

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self._agents.get(agent_id)

    def prompt_for(self, agent_id: str) -> Optional[str]:
        a = self._agents.get(agent_id)
        return a.get("prompt") if a else None


__all__ = ["AgentLibrary"]
