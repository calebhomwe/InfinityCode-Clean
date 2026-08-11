"""Offline tests for the A/B benchmark runner (Tools/ab_kernel_vs_actions.py).

Drives the script's pure core (`run_ab`) with FakeBuilders so no real LLM or
kernel subprocess is needed for the actions mode. Kernel mode runs through the
real KernelTaskEngine (per the pinned contract) with tiny budgets; if
kernel_engine.py is not importable yet (Worker 1 in flight), the kernel rows
are recorded as status "unavailable" and the kernel-specific assertions are
skipped.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent


def _find_backend() -> Path | None:
    """Locate the infinity-code backend/ package.

    Merged layout: backend/tests/test_ab_benchmark.py -> parents[1] = backend.
    Staging layout: _swarm_stage2/test_ab_benchmark.py -> no sibling backend;
    falls back to INFINITY_CODE_ROOT.
    """
    here = Path(__file__).resolve()
    for cand in (here.parents[1], here.parents[1] / "backend"):
        if (cand / "core" / "longtask" / "engine.py").is_file():
            return cand
    root = os.environ.get("INFINITY_CODE_ROOT", "")
    if root:
        cand = Path(root) / "backend"
        if (cand / "core" / "longtask" / "engine.py").is_file():
            return cand
    return None


_BACKEND = _find_backend()
if _BACKEND is not None and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

try:  # noqa: E402
    import ab_kernel_vs_actions as ab
except ImportError:  # merged layout: the script lives in the repo Tools/ dir
    assert _BACKEND is not None, "could not locate infinity-code backend/"
    tools = _BACKEND.parent / "Tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    import ab_kernel_vs_actions as ab  # type: ignore[no-redef]

KERNEL_OK = ab.KernelTaskEngine is not None


class FakeBuilder:
    """Scripted builder: each chat() pops the next reply (the same pattern as
    backend/tests/test_longtask_engine.py's FakeBuilder)."""

    def __init__(self, replies: List[str], cost: float = 0.001) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.cost = cost
        self.seen: List[List[Dict[str, str]]] = []

    def chat(self, messages, max_tokens: int = 3000, **kw: Any):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        if not self.replies:
            return {"text": '{"action":"finish","args":{"summary":"out of script"}}',
                    "cost_usd": self.cost}
        return {"text": self.replies.pop(0), "cost_usd": self.cost}


def actions_script() -> List[str]:
    """JSON-action mode script ending in finish."""
    return [
        '{"action":"update_plan","args":{"plan":"- [ ] add hello.py"}}',
        '{"action":"write_file","args":{"path":"hello.py","content":"print(\'hi\')\\n"}}',
        '{"action":"run_command","args":{"command":"python hello.py"}}',
        '{"action":"finish","args":{"summary":"added hello.py, runs clean"}}',
    ]


def kernel_script() -> List[str]:
    """Kernel-mode script: python blocks per the pinned KernelTaskEngine
    protocol (plan -> compute -> write_file via tool handoff -> finish)."""
    return [
        'update_plan("- [ ] compute 6*7")\n',
        "total = 6 * 7\n",
        'write_file(path="n.txt", content=str(total))\n',
        'finish("done")\n',
    ]


def builder_factory(mode: str) -> FakeBuilder:
    """Fresh scripted builder per mode."""
    if mode == "kernel":
        return FakeBuilder(kernel_script())
    return FakeBuilder(actions_script())


class BoomBuilder:
    """Builder that always raises — simulates a provider outage."""

    def chat(self, messages, max_tokens: int = 3000, **kw: Any):
        raise RuntimeError("simulated provider outage")


def boom_factory(mode: str) -> BoomBuilder:  # noqa: ARG001
    return BoomBuilder()


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "base.txt").write_text("x")
    return r


def test_run_ab_two_rows(repo, tmp_path):  # noqa: ARG001
    rows = ab.run_ab(builder_factory, ["compute 6*7"], repo, max_steps=8)
    assert len(rows) == 2
    assert {r["mode"] for r in rows} == {"actions", "kernel"}
    for r in rows:
        assert r["goal"] == "compute 6*7"
        # actions mode is fully offline and always completes; the kernel row
        # completes once Worker 1's engine is deployed (else "unavailable").
        if r["mode"] == "actions" or KERNEL_OK:
            assert r["status"] == "completed", r


def test_rows_metrics_sane(repo, tmp_path):  # noqa: ARG001
    rows = ab.run_ab(builder_factory, ["compute 6*7"], repo, max_steps=8)
    assert len(rows) == 2
    for r in rows:
        # an unavailable engine mode never talks to the builder (0 calls);
        # every mode that actually ran must have made at least one call.
        if r["mode"] == "actions" or KERNEL_OK:
            assert isinstance(r["calls"], int) and r["calls"] >= 1
        else:
            assert r["calls"] == 0
        assert isinstance(r["tokens_in"], int) and r["tokens_in"] >= 0
        assert isinstance(r["cost_usd"], float) and r["cost_usd"] >= 0
        assert isinstance(r["wall_s"], float) and r["wall_s"] >= 0
        assert isinstance(r["steps"], int) and r["steps"] >= 0


def test_failing_builder_records_error_not_raise(repo, tmp_path):  # noqa: ARG001
    rows = ab.run_ab(boom_factory, ["x"], repo, max_steps=3)
    assert len(rows) == 2
    for r in rows:
        if r["mode"] == "kernel" and not KERNEL_OK:
            # engine not deployed yet: the row records unavailable, not error
            assert r["status"] == "unavailable"
        else:
            assert r["status"] == "error"
        assert r["calls"] >= 0


def test_render_table_contains_both_modes(repo, tmp_path):  # noqa: ARG001
    rows = ab.run_ab(builder_factory, ["compute 6*7"], repo, max_steps=8)
    out = ab.render_table(rows)
    assert "actions" in out
    assert "kernel" in out
    assert "goal" in out and "cost_usd" in out


def test_totals_per_mode(repo, tmp_path):  # noqa: ARG001
    rows = ab.run_ab(builder_factory, ["compute 6*7", "write readme"],
                     repo, max_steps=8)
    ts = ab.totals(rows)
    assert set(ts) == {"actions", "kernel"}
    for mode, t in ts.items():
        assert t["runs"] == 2
        assert t["calls"] == sum(r["calls"] for r in rows if r["mode"] == mode)
        assert t["cost_usd"] == sum(r["cost_usd"] for r in rows if r["mode"] == mode)
    rendered = ab.render_totals(ts)
    assert "actions" in rendered and "kernel" in rendered
