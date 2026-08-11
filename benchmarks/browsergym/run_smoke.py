"""Create and reset a BrowserGym MiniWoB task inside Docker.

No model is called here. This proves the benchmark environment itself starts
cleanly before an agent action adapter is allowed to run against it.
"""

from __future__ import annotations

import argparse
import json

import gymnasium as gym
import browsergym.miniwob  # noqa: F401 -- registers MiniWoB environments


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="browsergym/miniwob.click-test")
    args = parser.parse_args()
    env = gym.make(args.task)
    try:
        observation, info = env.reset()
        print(json.dumps({
            "ok": True,
            "task": args.task,
            "observation_type": type(observation).__name__,
            "info_keys": sorted(info.keys()),
        }))
    finally:
        env.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
