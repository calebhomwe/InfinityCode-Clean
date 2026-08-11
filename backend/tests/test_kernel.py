"""KernelSession tests — the Prime-Agent-style persistent kernel prototype.

Covers the four properties that make it a context-preservation win:
1. State persists across calls (variables live in the kernel, not the prompt).
2. Only the final answer crosses back; big data stays in the child.
3. Exceptions return as results without killing the session.
4. snapshot()/restore() survives a dead process (and a timeout kill).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.core.kernel import KernelError, KernelSession  # noqa: E402


@pytest.fixture()
def kernel(tmp_path):
    k = KernelSession(workdir=tmp_path, timeout=10, result_cap=4000)
    yield k
    k.close()


def test_state_persists_across_calls(kernel):
    """Variables assigned in one block are visible in the next — the kernel
    holds the working memory, not the conversation history."""
    res = kernel.execute("data = [i * i for i in range(10)]")
    assert res.ok, res.error
    assert kernel.execute("len(data)").result == "10"
    assert kernel.execute("sum(data)").result == "285"


def test_last_expression_value_is_captured(kernel):
    """IPython-style: the value of the last expression is the answer."""
    assert kernel.execute("6 * 7").result == "42"


def test_large_data_stays_in_child(kernel):
    """A 500K payload read into the kernel costs the context ~40 chars."""
    res = kernel.execute("big = 'A' * 500_000")
    assert res.ok, res.error
    assert len(res.result) < 200, "large assignment leaked into the answer"
    # Query on demand — the whole point of kernel-side data.
    assert kernel.execute("len(big)").result == "500000"
    assert kernel.execute("big.count('A')").result == "500000"


def test_stdout_is_captured_and_capped(kernel):
    res = kernel.execute("print('hello from kernel')\n41 + 1")
    assert res.ok
    assert "hello from kernel" in res.stdout
    assert res.result == "42"
    noisy = kernel.execute("print('x' * 100_000)")
    assert noisy.ok
    assert len(noisy.stdout) <= 4200, "noisy stdout must be capped"


def test_exception_returns_error_and_session_survives(kernel):
    res = kernel.execute("1 / 0")
    assert not res.ok
    assert "ZeroDivisionError" in (res.error or "")
    # The kernel is still usable after a failed block.
    assert kernel.execute("2 + 2").result == "4"


def test_sandbox_rejects_forbidden_import(kernel):
    res = kernel.execute("import os")
    assert not res.ok
    assert "forbidden" in (res.error or "").lower()


def test_snapshot_and_restore_roundtrip(kernel, tmp_path):
    kernel.execute("x = 41")
    snap = kernel.snapshot(tmp_path / "mem.pkl")
    assert snap.exists()
    kernel.close()

    fresh = KernelSession(workdir=tmp_path, timeout=10)
    try:
        fresh.restore(snap)
        assert fresh.execute("x + 1").result == "42"
    finally:
        fresh.close()


def test_timeout_kills_stuck_call(kernel):
    kernel.close()
    k = KernelSession(workdir=None, timeout=10, auto_recover=False)
    try:
        res = k.execute("import time; time.sleep(30)", timeout=1)
        assert res.timed_out is True
        # auto_recover=False: a fresh call must fail loudly, not hang.
        with pytest.raises(KernelError):
            k.execute("2 + 2")
    finally:
        k.close()


def test_restore_after_kill_recovers_memory(tmp_path):
    """The crash-resilience story: session killed mid-task, snapshot survives."""
    k = KernelSession(workdir=tmp_path, timeout=5)
    k.execute("progress = {'files': 3, 'phase': 'halfway'}")
    snap = k.snapshot(tmp_path / "mem.pkl")
    res = k.execute("import time; time.sleep(30)", timeout=1)
    assert res.timed_out is True
    k.close()

    fresh = KernelSession(workdir=tmp_path, timeout=5)
    try:
        fresh.restore(snap)
        assert fresh.execute("progress['phase']").result == "'halfway'"
    finally:
        fresh.close()


def test_context_window_style_usage(tmp_path):
    """The Prime-Agent demo: a 500KB 'log' lives in the kernel; the model
    only ever sees the handful of lines it asked for."""
    log = tmp_path / "big.log"
    log.write_text(
        "\n".join(
            f"2026-08-10 INFO line {i} ok" if i % 100 else f"2026-08-10 ERROR line {i} boom"
            for i in range(50_000)
        ),
        encoding="utf-8",
    )
    k = KernelSession(workdir=tmp_path, timeout=15)
    try:
        assert k.execute("log = open('big.log', encoding='utf-8').read()").ok
        assert k.execute("len(log)").result == str(len(log.read_text(encoding="utf-8")))
        res = k.execute(
            "import re\n"
            "[l for l in log.splitlines() if 'ERROR' in l][:5]"
        )
        assert res.ok
        assert "ERROR" in res.result
        assert len(res.result) < 2000, "only the queried slice should come back"
    finally:
        k.close()
