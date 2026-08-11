"""KernelSession v0.2 hardening tests.

Targets the pinned v0.2 API contract (see _swarm_stage/goal.md):

- Host-side shell shim: ``!command`` and ``%%bash`` blocks in execute(),
  gated by allow_shell (default off), plus the explicit shell() method.
- Auto-snapshot + auto-recover: a timed-out/dead kernel is transparently
  respawned from the newest auto-snapshot on the next execute().
- stats() counters (calls, chars_returned, spawns, restarts, snapshots,
  uptime_s).
- Tool-call protocol (_ToolCall exception -> KernelResult.tool_call) and
  set_tool_result().
- Robustness: unicode/CRLF round-trips, session isolation, thread-safety of
  the queue protocol, no double execution of side effects, output capping,
  snapshots that skip unpicklable state.

Each test builds its own session (no shared state between tests). Timeouts are
kept tight (1s sleep-and-kill) so the whole suite stays well under ~30s.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.core.kernel import KernelError, KernelSession  # noqa: E402


# --- shell shim ------------------------------------------------------------ #


def test_shell_shim_single_command(tmp_path):
    """`!command` routes to the HOST-side shell, gated by allow_shell=True."""
    k = KernelSession(workdir=tmp_path, timeout=10, allow_shell=True)
    try:
        res = k.execute("!echo hello-kernel")
        assert res.ok, res.error
        assert "hello-kernel" in (res.stdout or "") + (res.result or "")
    finally:
        k.close()


def test_shell_disabled_by_default(tmp_path):
    """allow_shell defaults to False: the shim refuses with a clear message."""
    k = KernelSession(workdir=tmp_path, timeout=10)
    try:
        res = k.execute("!echo x")
        assert not res.ok
        msg = (res.error or "").lower()
        assert "shell" in msg, f"error should mention shell: {res.error!r}"
        assert "disabled" in msg, f"error should mention disabled: {res.error!r}"
    finally:
        k.close()


def test_shell_bash_block(tmp_path):
    """`%%bash\\n...` multi-line blocks route to the host shell and capture
    every line of stdout."""
    k = KernelSession(workdir=tmp_path, timeout=10, allow_shell=True)
    try:
        res = k.execute("%%bash\necho line1\necho line2")
        assert res.ok, res.error
        out = (res.stdout or "") + (res.result or "")
        assert "line1" in out, f"line1 missing from output: {out!r}"
        assert "line2" in out, f"line2 missing from output: {out!r}"
    finally:
        k.close()


def test_shell_explicit_method(tmp_path):
    """shell() is the explicit host-side entry point, cwd=workdir."""
    k = KernelSession(workdir=tmp_path, timeout=10, allow_shell=True)
    try:
        res = k.shell("echo direct-call")
        assert res.ok, res.error
        assert "direct-call" in (res.stdout or "") + (res.result or "")
    finally:
        k.close()


# --- auto-recovery ---------------------------------------------------------- #


def test_auto_recover_after_timeout(tmp_path):
    """auto_recover=True + snapshot_every=1: a timed-out kernel is respawned
    from the newest auto-snapshot; the recovering call reports restarted=True."""
    k = KernelSession(workdir=tmp_path, timeout=10, snapshot_every=1, auto_recover=True)
    try:
        r1 = k.execute("x = 7")
        assert r1.ok, r1.error  # successful call -> auto-snapshot written

        r2 = k.execute("import time; time.sleep(30)", timeout=1)
        assert r2.timed_out is True

        r3 = k.execute("x + 1")
        assert r3.ok, r3.error
        assert r3.result == "8"
        assert r3.restarted is True
    finally:
        k.close()


def test_no_auto_recover_when_disabled(tmp_path):
    """auto_recover=False: after a timeout-kill the session stays dead and the
    next execute() raises KernelError instead of silently respawning."""
    k = KernelSession(workdir=tmp_path, timeout=10, auto_recover=False)
    try:
        res = k.execute("import time; time.sleep(30)", timeout=1)
        assert res.timed_out is True
        with pytest.raises(KernelError):
            k.execute("2 + 2")
    finally:
        k.close()


# --- stats ------------------------------------------------------------------- #


def test_stats_fields(tmp_path):
    """stats() exposes the full v0.2 counter set and reflects real work."""
    k = KernelSession(workdir=tmp_path, timeout=10)
    try:
        k.execute("a = 1")
        k.execute("b = 2")
        assert k.execute("a + b").result == "3"

        st = k.stats()
        for key in ("calls", "chars_returned", "spawns", "restarts", "snapshots", "uptime_s"):
            assert key in st, f"stats() missing key {key!r}"
        assert st["calls"] >= 3, "calls must count every execute()"
        assert st["chars_returned"] > 0, "results were returned, chars must be > 0"
        assert st["spawns"] >= 1
        assert st["restarts"] == 0, "no kernel ever died in this test"
        assert st["snapshots"] >= 0
        assert isinstance(st["uptime_s"], (int, float))
        assert st["uptime_s"] >= 0
    finally:
        k.close()


# --- robustness ---------------------------------------------------------------- #


def test_unicode_roundtrip(tmp_path):
    """Multi-byte and astral-plane characters survive the JSON-line protocol."""
    k = KernelSession(workdir=tmp_path, timeout=10)
    try:
        r1 = k.execute("s = 'héllo 世界 🎵'")
        assert r1.ok, r1.error
        # h é l l o ' ' 世 界 ' ' 🎵 == 10 code points
        r2 = k.execute("len(s)")
        assert r2.ok, r2.error
        assert r2.result == "10"
        r3 = k.execute("s")
        assert r3.ok, r3.error
        assert "héllo 世界 🎵" in r3.result
    finally:
        k.close()


def test_crlf_code_block(tmp_path):
    """CRLF line endings must not break parsing/execution."""
    k = KernelSession(workdir=tmp_path, timeout=10)
    try:
        res = k.execute("a = 1\r\nb = 2\r\na + b")
        assert res.ok, res.error
        assert res.result == "3"
    finally:
        k.close()


def test_concurrent_sessions_are_isolated(tmp_path):
    """Two sessions in different workdirs keep fully independent namespaces."""
    workdir_a = tmp_path / "a"
    workdir_b = tmp_path / "b"
    workdir_a.mkdir()
    workdir_b.mkdir()
    ka = KernelSession(workdir=workdir_a, timeout=10)
    kb = KernelSession(workdir=workdir_b, timeout=10)
    try:
        assert ka.execute("x = 1").ok
        assert kb.execute("x = 2").ok
        assert ka.execute("x").result == "1"
        assert kb.execute("x").result == "2"
    finally:
        ka.close()
        kb.close()


def test_threaded_execute_on_one_session(tmp_path):
    """Concurrent execute() calls on ONE session must serialize safely through
    the queue protocol and each thread gets ITS OWN answer."""
    k = KernelSession(workdir=tmp_path, timeout=10)
    outcomes = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        try:
            res = k.execute(f"41 + {n}")
            with lock:
                outcomes.append((n, res, None))
        except Exception as exc:  # surfaced via assertions below
            with lock:
                outcomes.append((n, None, exc))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert len(outcomes) == 4, f"expected 4 thread outcomes, got {len(outcomes)}"
    by_n = {n: (res, exc) for n, res, exc in outcomes}
    for n in range(4):
        res, exc = by_n[n]
        assert exc is None, f"thread {n} raised: {exc!r}"
        assert res.ok, f"thread {n}: {res.error}"
        assert res.result == str(41 + n), f"thread {n} got the wrong answer"
    k.close()


# --- tool-call protocol -------------------------------------------------------- #


def test_tool_call_surfaces(tmp_path):
    """A _ToolCall exception raised in the child maps onto result.tool_call
    instead of surfacing as an error."""
    k = KernelSession(workdir=tmp_path, timeout=10)
    try:
        code = (
            "class _ToolCall(Exception):\n"
            "    def __init__(self, name, args):\n"
            "        self.name = name\n"
            "        self._tool_args = args\n"
            "    @property\n"
            "    def args(self):\n"
            "        return self._tool_args\n"
            "raise _ToolCall('my_tool', {'k': 1})\n"
        )
        res = k.execute(code)
        assert res.tool_call == {"name": "my_tool", "args": {"k": 1}}
    finally:
        k.close()


def test_set_tool_result_readable(tmp_path):
    """set_tool_result() pushes _last_tool_result into the child namespace so a
    later cell can read the tool's response."""
    k = KernelSession(workdir=tmp_path, timeout=10)
    try:
        assert k.execute("_last_tool_result = 'seed'").ok
        set_res = k.set_tool_result('{"ok": true}')
        assert set_res.ok, set_res.error
        read = k.execute("_last_tool_result")
        assert read.ok, read.error
        assert "ok" in read.result
    finally:
        k.close()


def test_set_tool_result_binds_result_alias(tmp_path):
    """`result = tool(...)` never binds (the _ToolCall raise aborts the
    assignment), so set_tool_result also binds `result` - the name models
    actually reference on the next turn (run-6 NameError refetch loop)."""
    k = KernelSession(workdir=tmp_path, timeout=10)
    try:
        set_res = k.set_tool_result('{"status": 200}')
        assert set_res.ok, set_res.error
        read = k.execute("result")
        assert read.ok, read.error
        assert "200" in read.result
        # Both names point at the same payload.
        both = k.execute("result == _last_tool_result")
        assert both.ok, both.error
        assert "True" in both.result
    finally:
        k.close()


# --- snapshots & capping -------------------------------------------------------- #


def test_snapshot_skips_unpicklable(tmp_path):
    """Unpicklable state (e.g. a threading.Lock) is skipped, not fatal: the
    snapshot still writes and a fresh session can restore and execute."""
    k = KernelSession(workdir=tmp_path, timeout=10)
    fresh = KernelSession(workdir=tmp_path, timeout=10)
    try:
        r = k.execute("import threading; t = threading.Lock()")
        assert r.ok, r.error
        snap = k.snapshot(tmp_path / "mem-unpicklable.pkl")
        assert snap.exists()
        fresh.restore(snap)
        assert fresh.execute("41 + 1").result == "42"
    finally:
        fresh.close()
        k.close()


def test_double_execution_side_effect_once(tmp_path):
    """A block with side effects runs exactly once — no head/re-expr double run."""
    k = KernelSession(workdir=tmp_path, timeout=10)
    try:
        res = k.execute("print('ONCE')")
        assert res.ok, res.error
        assert (res.stdout or "").count("ONCE") == 1
    finally:
        k.close()


def test_result_cap_enforced(tmp_path):
    """result_cap truncates oversized results and marks them as truncated."""
    k = KernelSession(workdir=tmp_path, timeout=10, result_cap=100)
    try:
        res = k.execute("'x' * 5000")
        assert res.ok, res.error
        assert len(res.result) <= 150, f"result not capped: {len(res.result)} chars"
        assert "truncated" in res.result
    finally:
        k.close()
