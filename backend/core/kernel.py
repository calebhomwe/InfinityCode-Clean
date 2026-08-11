"""KernelSession — a persistent Python execution kernel for the agent loop.

The Prime-Agent-style bet: instead of a menu of JSON tools (read_file, grep,
run_command ...) whose outputs are dumped into the model context, the model
writes Python and the DATA STAYS IN THE KERNEL. Only final answers cross back
into the conversation. Big files are read into kernel memory once and queried
on demand, so a 500KB log costs the context window a few hundred tokens, not
100K.

Mechanics (zero new dependencies, works from the PyInstaller-frozen build):
- A real Python subprocess (resolve_python, -I isolated) runs a tiny JSON-line
  REPL: each request is one code block executed in the SAME namespace, so
  variables persist between turns like an IPython kernel.
- Parent-side AST validation reuses sandbox.CodeValidator before anything is
  sent to the child.
- The last expression's value is captured (IPython-style `_`), stdout/stderr
  are redirected and capped so tool noise can never balloon the context.
- snapshot() pickles the child namespace to disk and restore() reloads it into
  a fresh child — session memory survives a killed process (or a timeout).

v0.2 hardening (swarm stage):
- `!command` / `%%bash\\n...` blocks in execute() route to a HOST-side shell
  (subprocess.run in the workdir), gated by allow_shell (default off).
- shell(command) exposes the same host-side execution explicitly.
- Auto-snapshot: every `snapshot_every` successful execute() writes
  <workdir>/.kernel/auto-<seq>.pkl; a dead session (timeout kill or process
  exit) with auto_recover=True respawns on the next execute() and restores the
  newest auto-snapshot, marking the result restarted=True.
- Tool-call protocol: kernel code can `raise _ToolCall(name, args)` and the
  worker replies with {"tool_call": {"name":..., "args":...}} instead of an
  error; set_tool_result(text) pushes the result back as `_last_tool_result`.

This is the v0 prototype for the longtask A/B: kernel-mode run vs the JSON
action-menu path. Do NOT wire into missions until the A/B is measured.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import pickle
import queue
import subprocess
import sys
import threading
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .exec_utils import resolve_python
from .sandbox import CodeValidator

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS: int = 30
DEFAULT_RESULT_CAP: int = 4000
MAX_REQUEST_CHARS: int = 200_000

# Same forbidden surface as exec_utils.run_python; eval/exec/compile banned by
# the validator itself. The child has no network stack, no subprocess, and
# cannot see the host env (all of it would break the context-preservation
# guarantee anyway).
_FORBIDDEN_IMPORTS = {"os", "shutil", "socket", "http", "urllib", "ctypes", "subprocess"}

_WORKER = r"""
import ast, contextlib, io, json, pickle, sys, traceback, types

class _ToolCall(Exception):
    # Raised by kernel code to hand control back for a host-side tool call.
    # Carries the tool name and its arguments; the worker replies with
    # {"tool_call": {"name": ..., "args": ...}} instead of an error result so
    # the parent can route the request and feed the answer back via
    # set_tool_result (as `_last_tool_result`).
    # NB: BaseException.args is a C slot that tuple()-converts non-tuples, so
    # the args dict is backed by our own attribute behind a property.
    def __init__(self, name, args=None):
        Exception.__init__(self, name)
        self.name = name
        self._tool_args = args if args is not None else {}

    @property
    def args(self):
        return self._tool_args

    @args.setter
    def args(self, value):
        self._tool_args = value

def _cap(s, limit):
    if not s:
        return ""
    s = str(s)
    if len(s) > limit:
        return s[:limit] + f"\n...[truncated {len(s) - limit} chars]"
    return s

def _execute(code, ns, limit):
    out, err = io.StringIO(), io.StringIO()
    result = None
    try:
        tree = ast.parse(code)
        last = tree.body[-1] if tree.body else None
        is_expr = isinstance(last, ast.Expr)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if is_expr:
                head = tree.body[:-1]
                if head:
                    exec(compile(ast.Module(body=head, type_ignores=[]), "<kernel>", "exec"), ns)
                result = eval(compile(ast.Expression(last.value), "<kernel>", "eval"), ns)
            else:
                exec(compile(tree, "<kernel>", "exec"), ns)
                result = ns.get("_")
        ns["_"] = result
        return {"ok": True, "result": _cap(repr(result), limit),
                "stdout": _cap(out.getvalue(), limit), "stderr": _cap(err.getvalue(), limit),
                "error": None}
    except _ToolCall as tc:
        return {"ok": False, "result": "", "stdout": _cap(out.getvalue(), limit),
                "stderr": _cap(err.getvalue(), limit), "error": None,
                "tool_call": {"name": tc.name, "args": tc.args}}
    except Exception as exc:
        # Name-based fallback: kernel_skills and user code may define their own
        # _ToolCall (shadowing the injected one), so catch by type name and
        # extract args from whatever shape the class uses.
        if type(exc).__name__ == "_ToolCall":
            args = getattr(exc, "kwargs", None)
            if args is None:
                args = getattr(exc, "_tool_args", None)
            if args is None:
                raw = getattr(exc, "args", None)
                args = raw[1] if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], dict) else raw
            return {"ok": False, "result": "", "stdout": _cap(out.getvalue(), limit),
                    "stderr": _cap(err.getvalue(), limit), "error": None,
                    "tool_call": {"name": getattr(exc, "name", None), "args": args}}
        return {"ok": False, "result": _cap(repr(result), limit),
                "stdout": _cap(out.getvalue(), limit), "stderr": _cap(err.getvalue(), limit),
                "error": f"{type(exc).__name__}: {exc}"}

def _picklable(ns):
    out = {}
    for k, v in ns.items():
        if k.startswith("__") or isinstance(v, types.ModuleType):
            continue
        try:
            pickle.dumps(v)
            out[k] = v
        except Exception:
            pass  # skip unpicklable state rather than failing the snapshot
    return out

def main():
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ns = {"__name__": "__kernel__", "_ToolCall": _ToolCall}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            if msg.get("snapshot"):
                with open(msg["path"], "wb") as fh:
                    pickle.dump(_picklable(ns), fh)
                reply = {"ok": True, "result": msg["path"], "stdout": "", "stderr": "", "error": None}
            elif msg.get("restore"):
                with open(msg["path"], "rb") as fh:
                    ns.update(pickle.load(fh))
                reply = {"ok": True, "result": "restored", "stdout": "", "stderr": "", "error": None}
            else:
                reply = _execute(msg.get("code", ""), ns, int(msg.get("limit", 4000)))
        except Exception as exc:
            reply = {"ok": False, "result": "", "stdout": "", "stderr": "",
                     "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps({"id": msg.get("id"), "reply": reply}) + "\n")
        sys.stdout.flush()

main()
"""


def _cap(text: str, limit: int) -> str:
    """Cap a captured stream, mirroring the worker's truncation policy."""
    if not text:
        return ""
    if len(text) > limit:
        return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"
    return text


def _extract_shell_command(code: str) -> Optional[str]:
    """Return the host-side command when `code` is a `!cmd` / `%%bash` block.

    `!` and `%%` are never valid Python prefixes, so this can only fire on
    intent; anything else falls through to the normal kernel path.
    """
    stripped = code.strip()
    if not stripped:
        return None
    if stripped.startswith("!"):
        return stripped[1:].strip()
    if stripped.startswith("%%bash"):
        return stripped[len("%%bash"):].strip()
    return None


class KernelError(RuntimeError):
    """Raised when the session is unusable (dead after a timeout, no Python)."""


@dataclass
class KernelResult:
    ok: bool
    result: str = ""
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    timed_out: bool = False
    restarted: bool = False
    tool_call: Optional[Dict[str, Any]] = None

    @property
    def answer(self) -> str:
        """The only text that should ever enter the model context."""
        return self.result if self.ok else f"ERROR: {self.error}"


class KernelSession:
    """A stateful Python session in a subprocess, spoken to over JSON lines."""

    def __init__(
        self,
        workdir: Optional[Any] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        result_cap: int = DEFAULT_RESULT_CAP,
        snapshot_every: int = 10,
        auto_recover: bool = True,
        allow_shell: bool = False,
    ) -> None:
        self.workdir = str(Path(workdir).resolve()) if workdir else None
        self.timeout = timeout
        self.result_cap = result_cap
        self.snapshot_every = snapshot_every
        self.auto_recover = auto_recover
        self.allow_shell = allow_shell
        self._proc: Optional[subprocess.Popen] = None
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._dead = False
        self._stderr_tail: list = []
        self._next_id = 0
        # Serializes request/reply over the pipes: the child executes one
        # block at a time, so concurrent callers must queue behind the lock
        # (a bare queue.get() could hand thread A thread B's reply).
        self._lock = threading.RLock()
        # stats counters (see stats())
        self._calls = 0
        self._chars_returned = 0
        self._spawns = 0
        self._restarts = 0
        self._snapshots = 0
        self._exec_successes = 0
        self._auto_seq = 1
        self._first_spawn: Optional[float] = None

    # --- lifecycle ------------------------------------------------------- #

    def _spawn(self, restore_path: Optional[str] = None) -> None:
        python_exe = resolve_python()
        if python_exe is None:
            raise KernelError(
                "No Python interpreter available; install Python or set INFINITY_PYTHON."
            )
        b64 = base64.b64encode(_WORKER.encode("utf-8")).decode("ascii")
        cmd = [
            python_exe,
            "-I",
            "-c",
            "exec(__import__('base64').b64decode(%r).decode())" % b64,
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=self.workdir,
            )
        except OSError as exc:
            raise KernelError(f"failed to start kernel subprocess: {exc}") from exc
        self._dead = False
        self._stderr_tail = []
        # Fresh queue per child: stale EOF sentinels from a killed process must
        # never leak into a restarted session's replies.
        self._queue = queue.Queue()
        self._spawns += 1
        if self._first_spawn is None:
            self._first_spawn = time.monotonic()
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        if restore_path is not None:
            res = self._request({"restore": True, "path": restore_path})
            if not res.ok:
                raise KernelError(f"restore failed: {res.error}")

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._queue.put((msg.get("id"), msg.get("reply")))
        # EOF. Only flag the session dead if this loop belonged to the CURRENT
        # child; a late EOF from a killed process must not poison a fresh spawn.
        if self._proc is proc:
            self._dead = True
            self._queue.put((None, None))  # EOF sentinel

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            self._stderr_tail.append(line)
            if len(self._stderr_tail) > 50:
                self._stderr_tail.pop(0)

    def _request(
        self,
        payload: Dict[str, Any],
        timeout: Optional[int] = None,
        restarted: bool = False,
    ) -> KernelResult:
        if self._proc is None or self._proc.stdin is None:
            raise KernelError("kernel not started")
        if self._dead:
            raise KernelError("kernel session is dead; start a new one or restore a snapshot")
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            payload["id"] = req_id
            try:
                self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._dead = True
                raise KernelError(f"kernel pipe closed: {exc}") from exc
            deadline = timeout if timeout is not None else self.timeout
            try:
                got_id, reply = self._queue.get(timeout=deadline)
            except queue.Empty:
                self._terminate()
                return KernelResult(ok=False, error="kernel call timed out", timed_out=True)
        if got_id is None:  # EOF sentinel — child died without a reply
            raise KernelError("kernel process exited unexpectedly")
        r = reply or {}
        return KernelResult(
            ok=bool(r.get("ok")),
            result=str(r.get("result") or ""),
            stdout=str(r.get("stdout") or ""),
            stderr=str(r.get("stderr") or ""),
            error=r.get("error"),
            restarted=restarted,
            tool_call=r.get("tool_call"),
        )

    def _terminate(self) -> None:
        self._dead = True
        if self._proc is not None:
            try:
                self._proc.terminate()
            except OSError:
                pass

    def _ensure_alive(self) -> bool:
        """Make sure a live child is ready; auto-recover from a dead one.

        Returns True when this call had to restart the child (the result
        should then carry restarted=True).
        """
        if self._proc is not None and not self._dead:
            return False
        if self._proc is not None and not self.auto_recover:
            raise KernelError("kernel session is dead; start a new one or restore a snapshot")
        if self._proc is None:
            self._spawn()
            return False
        # Dead child + auto_recover: respawn and best-effort restore.
        self._restarts += 1
        self.close()
        snap = self._newest_auto_snapshot()
        if snap is not None:
            try:
                self._spawn(restore_path=str(snap))
                logger.info("auto-recovered kernel from %s", snap)
                return True
            except KernelError as exc:
                logger.warning("auto-recover restore from %s failed (%s); spawning fresh", snap, exc)
                self.close()
        self._spawn()
        return True

    def _newest_auto_snapshot(self) -> Optional[Path]:
        if self.workdir is None:
            return None
        snap_dir = Path(self.workdir) / ".kernel"
        if not snap_dir.is_dir():
            return None
        best: Optional[Path] = None
        best_seq = -1
        for candidate in snap_dir.glob("auto-*.pkl"):
            try:
                seq = int(candidate.stem.split("-")[1])
            except (IndexError, ValueError):
                continue
            if seq > best_seq:
                best_seq, best = seq, candidate
        return best

    def _auto_snapshot(self) -> None:
        """Snapshot to <workdir>/.kernel/auto-<seq>.pkl; never fails the call."""
        if self.workdir is None:
            return
        seq = self._auto_seq
        self._auto_seq += 1
        snap = Path(self.workdir) / ".kernel" / f"auto-{seq}.pkl"
        try:
            self.snapshot(snap)
            logger.info("auto-snapshot written: %s", snap)
        except KernelError as exc:
            logger.warning("auto-snapshot failed: %s", exc)

    def _run_shell_command(self, command: str, timeout: Optional[int]) -> KernelResult:
        """Host-side shell execution (cwd=workdir), output capped by result_cap."""
        if not self.allow_shell:
            return KernelResult(ok=False, error="shell is disabled (allow_shell=False)")
        if not command:
            return KernelResult(ok=False, error="empty shell command")
        deadline = timeout if timeout is not None else self.timeout
        # Windows cmd.exe only executes the first line of a multi-line string,
        # so a %%bash block must be joined with '&' to run every line.
        if os.name == "nt" and "\n" in command:
            command = command.replace("\r\n", "\n").replace("\n", " & ")
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=deadline,
            )
        except subprocess.TimeoutExpired:
            return KernelResult(
                ok=False,
                error=f"shell command timed out after {deadline}s",
                timed_out=True,
            )
        except OSError as exc:
            return KernelResult(ok=False, error=f"could not launch shell: {exc}")
        ok = completed.returncode == 0
        return KernelResult(
            ok=ok,
            result="",
            stdout=_cap(completed.stdout or "", self.result_cap),
            stderr=_cap(completed.stderr or "", self.result_cap),
            error=None if ok else f"exit code {completed.returncode}",
        )

    def _finish(self, res: KernelResult) -> KernelResult:
        """Book-keep a result about to cross back to the caller."""
        self._chars_returned += len(res.result) + len(res.stdout)
        return res

    # --- public API ------------------------------------------------------ #

    def execute(self, code: str, timeout: Optional[int] = None) -> KernelResult:
        """Run one code block in the persistent namespace; return only the
        final answer plus capped streams.

        A leading `!command` / `%%bash\\n...` block routes to the HOST-side
        shell instead of the child (see shell()); the kernel is untouched.
        """
        self._calls += 1
        if len(code) > MAX_REQUEST_CHARS:
            return self._finish(
                KernelResult(ok=False, error=f"code block too large (>{MAX_REQUEST_CHARS} chars)")
            )
        command = _extract_shell_command(code)
        if command is not None:
            return self._finish(self._run_shell_command(command, timeout))
        valid, msg = CodeValidator(forbidden_imports=_FORBIDDEN_IMPORTS).validate(code)
        if not valid:
            return self._finish(KernelResult(ok=False, error=msg))
        with self._lock:
            restarted = self._ensure_alive()
            res = self._request({"code": code, "limit": self.result_cap}, timeout=timeout, restarted=restarted)
        if res.ok:
            self._exec_successes += 1
            if (
                self.snapshot_every > 0
                and self.workdir is not None
                and self._exec_successes % self.snapshot_every == 0
            ):
                self._auto_snapshot()
        return self._finish(res)

    def shell(self, command: str, timeout: Optional[int] = None) -> KernelResult:
        """Run a shell command on the HOST side (cwd=workdir), never the child.

        Gated by allow_shell (default False). stdout/stderr are captured and
        capped by result_cap; timeouts are reported on the result, not raised.
        """
        return self._finish(self._run_shell_command(command, timeout))

    def set_tool_result(self, text: str) -> KernelResult:
        """Push a tool result into the kernel as `_last_tool_result`.

        The child executes `_last_tool_result = <json-literal>` in the shared
        namespace so later cells can read what the host-side tool returned.
        `result` is bound to the same value: models naturally write
        `result = some_tool(...)`, but the _ToolCall raise aborts that
        assignment - the alias makes the natural pattern work next turn.
        """
        literal = json.dumps(str(text))
        code = f"_last_tool_result = {literal}\nresult = _last_tool_result"
        valid, msg = CodeValidator(forbidden_imports=_FORBIDDEN_IMPORTS).validate(code)
        if not valid:
            return self._finish(KernelResult(ok=False, error=msg))
        with self._lock:
            restarted = self._ensure_alive()
            res = self._request({"code": code, "limit": self.result_cap}, restarted=restarted)
        return self._finish(res)

    def snapshot(self, path: Optional[Any] = None) -> Path:
        """Persist kernel memory to disk so a killed session can be restored."""
        if self._proc is None or self._dead:
            raise KernelError("cannot snapshot a dead kernel")
        snap = Path(path) if path else Path(self.workdir or ".") / f"kernel-snapshot-{self._next_id}.pkl"
        snap.parent.mkdir(parents=True, exist_ok=True)
        res = self._request({"snapshot": True, "path": str(snap)})
        if not res.ok:
            raise KernelError(f"snapshot failed: {res.error}")
        self._snapshots += 1
        return snap

    def restore(self, path: Any) -> None:
        """Start a fresh session and reload kernel memory from a snapshot."""
        self.close()
        self._spawn(restore_path=str(path))

    def close(self) -> None:
        self._terminate()
        if self._proc is not None:
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def stats(self) -> dict:
        """Session counters: calls, chars_returned, spawns, restarts,
        snapshots, uptime_s (since the first spawn)."""
        uptime = 0.0
        if self._first_spawn is not None:
            uptime = round(time.monotonic() - self._first_spawn, 3)
        return {
            "calls": self._calls,
            "chars_returned": self._chars_returned,
            "spawns": self._spawns,
            "restarts": self._restarts,
            "snapshots": self._snapshots,
            "uptime_s": uptime,
        }


__all__ = ["KernelSession", "KernelResult", "KernelError", "DEFAULT_TIMEOUT_SECONDS", "DEFAULT_RESULT_CAP"]
