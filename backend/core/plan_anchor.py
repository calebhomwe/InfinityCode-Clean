"""Plan-anchored long-horizon protocol for LoopEngine.

Maintains a living plan.md that the model re-reads and updates each iteration.
This prevents long-horizon tasks from drifting after many turns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class PlanAnchor:
    """A simple markdown plan that the loop re-reads and updates each turn."""

    goal: str
    work_dir: Path
    subtasks: List[Dict[str, Any]] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        self.work_dir = Path(self.work_dir).resolve()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.work_dir / "plan.md"
        if not self.path.is_file():
            self.save()
        else:
            self.load()

    @property
    def plan_path(self) -> Path:
        return self.path

    def add_subtask(self, title: str) -> None:
        self.subtasks.append({"title": title, "done": False})
        self.save()

    def mark_done(self, title: str) -> bool:
        for st in self.subtasks:
            if st["title"] == title:
                st["done"] = True
                self.save()
                return True
        return False

    def update_from_response(self, text: str) -> None:
        """Parse an updated plan section from the model response and apply it."""
        # Look for a fenced ```plan block or a "PLAN:" section.
        plan_block = re.search(r"```plan\s*([\s\S]*?)```", text, re.IGNORECASE)
        if not plan_block:
            plan_block = re.search(r"(?i)PLAN UPDATE:\s*([\s\S]*?)(?:\n\n|\Z)", text)
        if not plan_block:
            return
        raw = plan_block.group(1).strip()
        new_subtasks: List[Dict[str, Any]] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Checkbox style: - [x] title  or  - [ ] title
            m = re.match(r"[-*]\s*\[([ xX])\]\s*(.+)", line)
            if m:
                done = m.group(1).strip().lower() == "x"
                new_subtasks.append({"title": m.group(2).strip(), "done": done})
            else:
                # Plain bullet
                new_subtasks.append({"title": line.lstrip("-* ").strip(), "done": False})
        if new_subtasks:
            # Preserve done status for matching titles.
            done_map = {st["title"]: st["done"] for st in self.subtasks}
            self.subtasks = [
                {"title": st["title"], "done": done_map.get(st["title"], st["done"])}
                for st in new_subtasks
            ]
            self.save()

    def to_prompt(self) -> str:
        lines = [
            "# Plan",
            f"Goal: {self.goal}",
            "",
            "## Subtasks",
        ]
        for st in self.subtasks:
            checkbox = "[x]" if st["done"] else "[ ]"
            lines.append(f"- {checkbox} {st['title']}")
        if self.notes:
            lines.extend(["", "## Notes", self.notes])
        lines.extend(["", "When you respond, include an updated plan in a ```plan block."])
        return "\n".join(lines)

    def save(self) -> None:
        self.path.write_text(self.to_prompt(), encoding="utf-8")

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return
        self.subtasks = []
        self.notes = ""
        in_notes = False
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Goal:"):
                self.goal = line[5:].strip()
                continue
            m = re.match(r"[-*]\s*\[([ xX])\]\s*(.+)", line)
            if m:
                self.subtasks.append({"title": m.group(2).strip(), "done": m.group(1).strip().lower() == "x"})
                in_notes = False
                continue
            if line == "## Notes":
                in_notes = True
                continue
            if in_notes:
                self.notes += line + "\n"

    def summary(self) -> Dict[str, Any]:
        total = len(self.subtasks)
        done = sum(1 for st in self.subtasks if st["done"])
        return {
            "goal": self.goal,
            "path": str(self.path),
            "total": total,
            "done": done,
            "remaining": total - done,
            "subtasks": list(self.subtasks),
        }


__all__ = ["PlanAnchor"]
