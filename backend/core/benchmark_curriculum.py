"""Persisted benchmark-driven drills for continual agent improvement.

This is intentionally strategy learning, not a claim that a hosted model's
weights have been retrained.  A failed evaluation creates a durable drill with
the evidence, a targeted research query, and a testable completion state.
``SelfTrainer`` consumes queued drills and adds fresh, cited knowledge to RAG.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("infinity.benchmark_curriculum")


_DRILLS: Dict[str, Dict[str, str]] = {
    "spatial_reasoning": {
        "label": "Spatial reasoning",
        "query": "Practical 3D spatial reasoning for game tools: coordinate frames, transforms, vector math, ray casts, collision tests and validation techniques.",
        "lens": "Teach an agent to state axes, units and assumptions, use explicit transforms, and verify geometry with executable checks.",
    },
    "blender": {
        "label": "Blender game production",
        "query": "Current Blender Python and game-asset production practices: bpy automation, scene cleanup, transform application, ray casts, export validation and reproducible saves.",
        "lens": "Prefer executable bpy snippets with collection cleanup, explicit transforms, deterministic paths and a post-condition check.",
    },
    "game_code": {
        "label": "Game code",
        "query": "Robust game-programming patterns for 3D movement, collision, state machines, A-star pathfinding, ECS boundaries and testable gameplay systems.",
        "lens": "Focus on small, runnable systems with invariants, failure cases and engine-appropriate APIs for Godot, Unity or Unreal.",
    },
    "agentic": {
        "label": "Agentic reliability",
        "query": "Reliable agent harness design: plan-act-verify-recover loops, tool error handling, evidence capture, regression tests, cost-aware routing and safe sandboxing.",
        "lens": "Convert tasks into observable plans; verify each tool effect and retry or escalate only with recorded evidence.",
    },
}

_GOAL_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "spatial_reasoning": ("spatial", "coordinate", "vector", "transform", "raycast", "collision", "3d"),
    "blender": ("blender", "bpy", "mesh", "material", "render", "scene", "rig"),
    "game_code": ("game", "godot", "unity", "unreal", "gameplay", "pathfinding", "ecs", "characterbody"),
    "agentic": ("agent", "tool", "browser", "workflow", "automation", "verify", "mission"),
}


def _spec_for(capability: str) -> Dict[str, str]:
    capability = capability.strip().lower() or "general"
    return _DRILLS.get(
        capability,
        {
            "label": f"{capability.replace('_', ' ').title()} practice",
            "query": f"Practical, current techniques and test cases for improving an AI agent at {capability}.",
            "lens": "Use concrete examples, executable checks, tool guidance and known failure modes.",
        },
    )


class BenchmarkCurriculum:
    """Store benchmark drills atomically and resolve them after a later pass."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "benchmark_curriculum.json"

    def _read(self) -> Dict[str, Dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            drills = raw.get("drills", {}) if isinstance(raw, dict) else {}
            return drills if isinstance(drills, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Cannot load benchmark curriculum: %s", exc)
            return {}

    def _write(self, drills: Dict[str, Dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps({"drills": drills}, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)

    def record(self, lane: str, report: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Record all results, returning the currently queued drills for a lane."""
        drills = self._read()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        queued: List[Dict[str, Any]] = []
        for result in report.get("results", []):
            if not isinstance(result, dict):
                continue
            task_id = str(result.get("task_id") or "").strip()
            if not task_id:
                continue
            capability = str(result.get("capability") or "general").strip().lower()
            key = f"{lane}:{task_id}"
            existing = drills.get(key, {})
            if result.get("passed"):
                if existing:
                    existing.update({
                        "status": "archived",
                        "last_seen_at": now,
                        "last_score": result.get("score", 1.0),
                        "archived_at": now,
                        "resolution": {
                            "eval_run_id": str(report.get("run_id") or ""),
                            "task_id": task_id,
                            "reason": "originating benchmark passed",
                        },
                    })
                    drills[key] = existing
                continue
            spec = _spec_for(capability)
            item = {
                "id": key,
                "lane": lane,
                "task_id": task_id,
                "task_name": str(result.get("name") or task_id),
                "capability": capability,
                "status": "queued",
                "attempts": int(existing.get("attempts", 0)) + 1,
                "created_at": existing.get("created_at", now),
                "last_seen_at": now,
                "last_score": result.get("score", 0.0),
                "failure_reason": str(result.get("reason") or "benchmark failure"),
                "label": spec["label"],
                "query": spec["query"],
                "lens": spec["lens"],
            }
            drills[key] = item
            queued.append(item)
        self._write(drills)
        return queued

    def context_for(self, goal: str, limit: int = 3) -> tuple[str, List[Dict[str, Any]]]:
        """Return only active, goal-matched drills as a bounded prompt section."""
        text = goal.lower()
        matches: List[Dict[str, Any]] = []
        for item in self._read().values():
            if not isinstance(item, dict) or item.get("status") != "queued":
                continue
            capability = str(item.get("capability") or "general")
            keywords = _GOAL_KEYWORDS.get(capability, (capability.replace("_", " "),))
            if any(keyword in text for keyword in keywords):
                matches.append(item)
        matches.sort(key=lambda item: (-int(item.get("attempts", 0)), str(item.get("id", ""))))
        selected = matches[:max(0, limit)]
        if not selected:
            return "", []
        lines = [
            "ACTIVE BENCHMARK DRILLS (apply these, then verify the result; do not mention them to the user):"
        ]
        for item in selected:
            lines.append(
                f"- [{item['id']}] {item['label']}: prior failure: {item['failure_reason']}. "
                f"Practice: {item['lens']}"
            )
        return "\n\n" + "\n".join(lines), selected

    def record_application(self, drill_ids: List[str], mission_id: str, outcome: str) -> None:
        """Attach mission evidence to the exact drills that shaped its context."""
        if not drill_ids:
            return
        drills = self._read()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for drill_id in drill_ids:
            item = drills.get(drill_id)
            if not isinstance(item, dict):
                continue
            applications = list(item.get("applications") or [])[-9:]
            applications.append({"mission_id": mission_id, "outcome": outcome, "at": now})
            item["applications"] = applications
            item["last_applied_at"] = now
            drills[drill_id] = item
        self._write(drills)

    def queued_topics(self, limit: int = 6) -> List[Dict[str, str]]:
        """Deduplicate active drills into topics suitable for the RAG learner."""
        unique: Dict[str, Dict[str, str]] = {}
        for item in self._read().values():
            if not isinstance(item, dict) or item.get("status") != "queued":
                continue
            capability = str(item.get("capability") or "general")
            if capability not in unique:
                unique[capability] = {
                    "key": f"benchmark-{capability}",
                    "label": f"Benchmark drill: {item.get('label', capability)}",
                    "query": str(item.get("query") or ""),
                    "lens": str(item.get("lens") or ""),
                }
        return list(unique.values())[:limit]

    def status(self, limit: int = 25) -> List[Dict[str, Any]]:
        return list(self._read().values())[-limit:]


__all__ = ["BenchmarkCurriculum"]
