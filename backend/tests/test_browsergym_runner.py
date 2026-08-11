"""The BrowserGym scaffold must stay opt-in and Docker-isolated."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.browsergym_runner import BrowserGymRunner


def test_runner_builds_explicit_compose_command() -> None:
    with tempfile.TemporaryDirectory() as directory:
        runner = BrowserGymRunner(Path(directory))
        command = runner.command("browsergym/miniwob.click-test")
        assert command[:3] == ["docker", "compose", "-f"]
        assert command[-2:] == ["--task", "browsergym/miniwob.click-test"]
        assert runner.status()["available"] is False


if __name__ == "__main__":
    test_runner_builds_explicit_compose_command()
    print("1/1 passed")
