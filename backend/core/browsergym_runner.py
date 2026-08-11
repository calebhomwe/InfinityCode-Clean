"""Explicit, Docker-isolated BrowserGym environment smoke runner.

This deliberately runs the MiniWoB environment only; it does not claim to
score Infinity's agent until an action adapter is connected. Docker keeps the
browser and its dependencies outside the desktop app process.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List


class BrowserGymRunner:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or Path(__file__).resolve().parents[2])
        self.compose_file = self.root / "benchmarks" / "browsergym" / "compose.yaml"

    def status(self) -> Dict[str, Any]:
        docker = shutil.which("docker")
        return {
            "available": bool(docker and self.compose_file.is_file()),
            "docker": docker or None,
            "compose_file": str(self.compose_file),
            "scope": "MiniWoB environment smoke only; agent action adapter is not enabled.",
        }

    def command(self, task: str = "browsergym/miniwob.click-test") -> List[str]:
        return [
            "docker", "compose", "-f", str(self.compose_file), "run", "--rm",
            "browsergym", "--task", task,
        ]

    def run_smoke(self, task: str = "browsergym/miniwob.click-test") -> Dict[str, Any]:
        info = self.status()
        if not info["available"]:
            return {"ok": False, **info, "error": "Docker or BrowserGym compose file is unavailable."}
        command = self.command(task)
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, **info, "command": command, "error": str(exc)}
        return {
            "ok": completed.returncode == 0,
            **info,
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }


__all__ = ["BrowserGymRunner"]
