"""Training flywheel — accepted trajectories become SFT data (P10).

Only quests that shipped with fidelity at or above the threshold count as
"accepted"; everything else is noise. The collector reads the journal, shapes
each accepted run into a chat-format trajectory, and exports HF-style JSONL
that any free/local fine-tune stack can consume.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class TrajectoryCollector:
    def __init__(self, journal, out_dir, fidelity_threshold: float = 0.7) -> None:
        self.journal = journal
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.fidelity_threshold = float(fidelity_threshold)

    # --- fidelity discovery ------------------------------------------------ #

    def _fidelity_of(self, task_id: str) -> Optional[float]:
        """Highest reviewer fidelity score recorded for a task, or None."""
        best: Optional[float] = None
        for s in self.journal.steps_for(task_id):
            if s.get("kind") != "review":
                continue
            try:
                payload = json.loads(s.get("result_json") or "{}")
            except (ValueError, TypeError):
                continue
            fid = payload.get("fidelity")
            if isinstance(fid, (int, float)):
                if best is None or float(fid) > best:
                    best = float(fid)
        return best

    def accepted_tasks(self) -> List[Tuple[Dict[str, Any], float]]:
        """Completed tasks whose fidelity clears the threshold."""
        out: List[Tuple[Dict[str, Any], float]] = []
        for t in self.journal.list_tasks(limit=500):
            if t.get("status") != "completed":
                continue
            fid = self._fidelity_of(t["id"])
            if fid is not None and fid >= self.fidelity_threshold:
                out.append((t, fid))
        return out

    # --- trajectory shaping ------------------------------------------------ #

    def trajectory_for(self, task: Dict[str, Any],
                       fidelity: float) -> Dict[str, Any]:
        """Shape one accepted run into a chat-format trajectory."""
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": "Goal: " + str(task.get("goal", ""))},
        ]
        for s in self.journal.steps_for(task["id"]):
            kind = s.get("kind")
            try:
                args = json.loads(s.get("args_json") or "{}")
            except (ValueError, TypeError):
                args = {}
            if kind == "tool":
                messages.append({"role": "assistant", "content": json.dumps(
                    {"action": s.get("tool", ""), "args": args}, default=str)})
                try:
                    result = json.loads(s.get("result_json") or "{}")
                except (ValueError, TypeError):
                    result = {}
                messages.append({"role": "user", "content":
                                 "RESULT: " + json.dumps(result, default=str)[:800]})
            elif kind == "finish":
                messages.append({"role": "assistant", "content": json.dumps(
                    {"action": "finish", "args": args}, default=str)})
        return {"task_id": task["id"], "goal": str(task.get("goal", "")),
                "fidelity": fidelity, "messages": messages}

    # --- dataset + export -------------------------------------------------- #

    def build_dataset(self) -> List[Dict[str, Any]]:
        return [self.trajectory_for(t, fid) for t, fid in self.accepted_tasks()]

    def export_dataset(self, path: Optional[str] = None) -> int:
        out_path = Path(path) if path else (self.out_dir / "dataset.jsonl")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with open(out_path, "w", encoding="utf-8") as f:
            for traj in self.build_dataset():
                f.write(json.dumps(traj, default=str) + "\n")
                n += 1
        return n

    def stats(self) -> Dict[str, Any]:
        accepted = self.accepted_tasks()
        fids = [fid for _, fid in accepted]
        total_msgs = sum(len(self.trajectory_for(t, fid)["messages"])
                         for t, fid in accepted)
        return {
            "accepted_tasks": len(accepted),
            "avg_fidelity": round(sum(fids) / len(fids), 3) if fids else 0.0,
            "total_messages": total_msgs,
            "threshold": self.fidelity_threshold,
            "out_dir": str(self.out_dir),
        }


__all__ = ["TrajectoryCollector"]
