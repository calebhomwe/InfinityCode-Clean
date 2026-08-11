"""KernelTaskEngine — kernel-mode longtask: the builder writes Python, not JSON.

The Prime-Agent-style variant of LongTaskEngine. Instead of parsing JSON action
blocks out of the reply, the builder model writes ONE Python code block per
turn. The block runs in a persistent KernelSession subprocess (workdir=repo,
allow_shell=True, auto_recover=True), so computed data stays in the kernel
between turns and only final answers cross back into the model context. Host
tools (read_file, write_file, ...) are callables the kernel code raises
`_ToolCall` for; the host dispatches them through the same jailed toolset as
the parent and feeds the result back into the kernel as `_last_tool_result`.

The plan/finish markers are accepted under BOTH spellings pinned by the swarm
goal — the marker names `__plan__`/`__finish__` and the tool-list names
`update_plan`/`finish` — so the system prompt, the loop and the worker test
suite agree. Bare (unfenced) replies count as code only when they parse as
Python or are a `!cmd`/`%%bash` shell block; anything else is a protocol
violation that triggers the one automatic re-ask.

All parent gates are reused wholesale: _review_gate, _judge_gate, _record_cost,
_record_artifact, _end, _emit, compact_history, _diff_stat. Budget, cancel and
wall-clock semantics are identical to the parent; the only differences are the
model protocol (Python code), the per-run kernel session, and the snapshot
cadence (<repo>/.kernel/seq-<n>.pkl).
"""
from __future__ import annotations

import ast
import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from backend.core.longtask.engine import (
        LongTaskEngine, Budget, SYSTEM, _MAX_REVIEW_ROUNDS, _MAX_TURN_FACTOR,
        _snapshot_files)
except ImportError:  # running with backend/ as the working directory
    from core.longtask.engine import (  # type: ignore[no-redef]
        LongTaskEngine, Budget, SYSTEM, _MAX_REVIEW_ROUNDS, _MAX_TURN_FACTOR,
        _snapshot_files)

try:
    from backend.core.longtask.journal import LongTaskJournal
except ImportError:  # running with backend/ as the working directory
    from core.longtask.journal import LongTaskJournal  # type: ignore[no-redef]

try:
    from backend.core.longtask.tools import PathJail, ToolError
except ImportError:  # running with backend/ as the working directory
    from core.longtask.tools import PathJail, ToolError  # type: ignore[no-redef]

try:
    from backend.core.kernel import KernelResult, KernelSession
except ImportError:  # running with backend/ as the working directory
    from core.kernel import KernelResult, KernelSession  # type: ignore[no-redef]

# Host tools the kernel code may raise _ToolCall for. Same names + args as the
# parent's action menu; dispatched through the shared jailed toolset.
_HOST_TOOLS = frozenset({
    "read_file", "list_dir", "grep", "glob",
    "write_file", "edit_file", "run_command", "web_fetch",
})

# Fenced code extraction: ```python ... ``` or bare ``` ... ```.
_CODE_FENCE = re.compile(r"```(?:python)?\s*(.*?)```", re.S)

_KERNEL_TOOL_LIST = (
    "  read_file(path) -> file text\n"
    "  list_dir(path='.') -> [{name, type}]\n"
    "  grep(pattern, path='.') -> [\"path:line: text\"]\n"
    "  glob(pattern) -> [repo-relative paths]\n"
    "  write_file(path, content) -> status string\n"
    "  edit_file(path, old, new, replace_all=False) -> status string\n"
    "  run_command(command, timeout=120) -> {exit_code, stdout, stderr}\n"
    "  web_fetch(url, max_chars=20000) -> {url, status, content, truncated}; keep max_chars small (3000-6000) and parse in the kernel\n"
    "  update_plan(plan) -> journaled markdown checklist\n"
    "  finish(summary) -> end the task"
)

# Same identity + repo discipline as the parent's SYSTEM; the protocol is the
# kernel one (Python code, not JSON actions).
KERNEL_SYSTEM = SYSTEM.split("Each reply:", 1)[0] + (
    "Each reply: a short thought (optional) then ONE Python code block "
    "(```python ... ``` fenced, or bare text). The block runs in a persistent "
    "kernel: every variable and piece of data you compute STAYS in the kernel "
    "across turns, so read files into kernel memory once and query them with "
    "Python instead of re-reading. Host tools are callables that hand control "
    "back to the host when called: call them directly, e.g. "
    "write_file(path='src/main.py', content=code), finish(summary). You may "
    "also raise _ToolCall('<name>', {...}) explicitly. The host runs the tool "
    "inside the repo jail and feeds the result back into the kernel as "
    "_last_tool_result - also bound as result - a JSON string for your NEXT "
    "turn. Tool calls END the current block: call one tool per block, then "
    "parse the result in your following block, e.g. block 1: "
    "web_fetch(url, max_chars=4000); block 2: import json; "
    "data = json.loads(result) # 'result' holds the fetch. "
    "Available tools:\n" + _KERNEL_TOOL_LIST + "\n"
    "Shell commands run on the host (never in the kernel) via a leading "
    "!command or %%bash block. There is NO per-tool approval in kernel mode: "
    "write and run tools execute immediately, so stay inside the repo and be "
    "deliberate (the same discipline as run_command). Your first turn should "
    "call update_plan with a markdown checklist. Never invent file contents "
    "you have not read. When the goal is met, call finish(summary)."
)


def _extract_code(text: str) -> Optional[str]:
    """Pull the ONE code block from a builder reply.

    A fenced ```python/``` block wins. A bare reply counts as code only when it
    parses as Python or is a `!cmd`/`%%bash` shell block; prose replies are a
    protocol violation (no code) and trigger the one automatic re-ask.
    """
    m = _CODE_FENCE.search(text or "")
    if m:
        code = m.group(1).strip()
        if code:
            return code
    code = (text or "").strip()
    if not code:
        return None
    if code.startswith("!") or code.startswith("%%bash"):
        return code
    try:
        ast.parse(code)
    except SyntaxError:
        return None
    return code


def _execute_kernel(kernel: KernelSession, code: str, timeout: int) -> KernelResult:
    """Run one kernel cell; a dead session surfaces as a kernel-error result
    (self-correctable by the builder) instead of killing the run."""
    try:
        return kernel.execute(code, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - dead kernel = kernel error
        return KernelResult(ok=False, result="", stdout="", stderr="",
                            error=f"kernel session unavailable: {exc}")


class KernelTaskEngine(LongTaskEngine):
    """Long-horizon engine where the builder model writes Python code.

    Protocol per turn: chat -> ONE code block -> persistent KernelSession ->
    either a kernel result (journaled as kind="kernel") or a _ToolCall handoff
    (dispatched through the parent's jailed _exec and journaled as kind="tool").
    Kernel errors are fed back to the builder for self-correction; only
    protocol errors (no code block twice in a row) fail the task.
    """

    def __init__(self, builder: Any, journal: LongTaskJournal,
                 cost_tracker: Any = None, reviewer: Any = None,
                 event_cb: Optional[Callable[[str, str, Dict], None]] = None,
                 references_dir: Optional[Path] = None,
                 judge: Any = None, harness: Any = None,
                 kernel_timeout: int = 30, snapshot_every: int = 10) -> None:
        super().__init__(builder=builder, journal=journal,
                         cost_tracker=cost_tracker, reviewer=reviewer,
                         event_cb=event_cb, references_dir=references_dir,
                         judge=judge, harness=harness)
        # Per-execution kernel timeout (seconds) and explicit snapshot cadence:
        # every `snapshot_every` successful kernel execs writes
        # <repo>/.kernel/seq-<n>.pkl (0 disables periodic snapshots).
        self.kernel_timeout = kernel_timeout
        self.snapshot_every = snapshot_every
        # Lazily created inside run(): one kernel session per run.
        self.kernel: Optional[KernelSession] = None

    def run(self, goal: str, repo_path: str, budget: Optional[Budget] = None,
            autonomy: str = "full", model_builder: str = "builder",
            model_reviewer: str = "reviewer",
            lessons: Optional[List[str]] = None,
            task_id: Optional[str] = None,
            spec_mode: bool = False,
            style_hints: Optional[List[str]] = None) -> Dict[str, Any]:
        self._style_hints = list(style_hints or [])
        budget = budget or Budget()
        jail = PathJail(Path(repo_path))
        # Async launches pre-create the journal row (so the caller gets an id
        # back instantly) and pass it in via task_id.
        if task_id is None:
            task_id = self.journal.create_task(
                goal=goal, repo_path=str(jail.root), model_builder=model_builder,
                model_reviewer=model_reviewer if self.reviewer else "",
                budget=asdict(budget), autonomy=autonomy)
        self._emit(task_id, "plan_update", {"plan": ""})

        seq = 0
        review_rounds = 0
        judge_rounds = 0
        spent_aud = 0.0
        kernel_execs = 0  # successful kernel execs -> snapshot cadence
        start = time.time()
        # Rescue-at-protocol-death (same as the parent): file changes made
        # before the builder dies on protocol still count as completion.
        files_at_start = _snapshot_files(jail.root)
        system = KERNEL_SYSTEM + f"\n\nREPO: {jail.root}\nGOAL: {goal}"
        if lessons:
            system += ("\n\nLESSONS LEARNED FROM PAST RUNS:\n- " +
                       "\n- ".join(str(l)[:200] for l in lessons[:5]))
        history: List[Dict[str, str]] = [{"role": "system", "content": system}]

        # One kernel per run. The kernel's own auto-snapshots are off
        # (snapshot_every=0): the engine owns the cadence below so the
        # seq-<n>.pkl numbering stays under contract. auto_recover still
        # respawns a dead child (fresh when no auto-snapshot is restorable).
        self.kernel = KernelSession(workdir=str(jail.root), allow_shell=True,
                                    auto_recover=True, snapshot_every=0)
        # Tool callables: inject wrapper functions so the model can literally
        # call finish(summary), write_file(path=..., content=...), etc. Each
        # wrapper raises _ToolCall; the loop below dispatches the handoff.
        # Degrades gracefully: if the preamble fails the model can still raise
        # _ToolCall explicitly (the prompt documents both spellings).
        _PREAMBLE = (
            "def _hc(name, args): raise _ToolCall(name, args)\n"
            "def read_file(path): _hc('read_file', {'path': path})\n"
            "def list_dir(path='.'): _hc('list_dir', {'path': path})\n"
            "def grep(pattern, path='.'): _hc('grep', {'pattern': pattern, 'path': path})\n"
            "def glob(pattern): _hc('glob', {'pattern': pattern})\n"
            "def write_file(path, content): _hc('write_file', {'path': path, 'content': content})\n"
            "def edit_file(path, old, new, replace_all=False): _hc('edit_file', {'path': path, 'old': old, 'new': new, 'replace_all': replace_all})\n"
            "def run_command(command, timeout=120): _hc('run_command', {'command': command, 'timeout': timeout})\n"
            "def web_fetch(url, max_chars=20000): _hc('web_fetch', {'url': url, 'max_chars': max_chars})\n"
            "def update_plan(plan): _hc('update_plan', {'plan': plan})\n"
            "def finish(summary): _hc('finish', {'summary': summary})\n"
        )
        try:
            self.kernel.execute(_PREAMBLE)
        except Exception:  # noqa: BLE001 - a failed preamble degrades to explicit raises
            pass

        def snap_path(n: int) -> Path:
            return Path(jail.root) / ".kernel" / f"seq-{n}.pkl"

        def snapshot_best_effort(n: int) -> bool:
            """Write a best-effort kernel snapshot; never fatal."""
            try:
                self.kernel.snapshot(snap_path(n))
                return True
            except Exception:  # noqa: BLE001 - snapshots never break the run
                return False

        try:
            for _turn in range(budget.max_steps * _MAX_TURN_FACTOR):
                if self._cancel.is_set():
                    return self._end(task_id, "cancelled", "cancelled by user",
                                     spent_aud)
                if time.time() - start > budget.max_wall_min * 60:
                    return self._end(task_id, "budget_exceeded",
                                     "wall-clock limit", spent_aud)

                try:
                    reply = self._chat(history)
                except Exception as exc:  # noqa: BLE001
                    return self._end(task_id, "error", f"builder failed: {exc}",
                                     spent_aud)
                spent_aud += self._record_cost(reply["cost_usd"])
                if spent_aud > budget.max_cost_aud:
                    return self._end(task_id, "budget_exceeded", "cost limit",
                                     spent_aud)
                history.append({"role": "assistant", "content": reply["text"]})

                code = _extract_code(reply["text"])
                if code is None:
                    # One automatic re-ask before failing the task (parent
                    # protocol-error flow, kernel flavor).
                    history.append({"role": "user",
                                    "content": "Reply with Python code only."})
                    try:
                        reply = self._chat(history)
                        spent_aud += self._record_cost(reply["cost_usd"])
                        if spent_aud > budget.max_cost_aud:
                            return self._end(task_id, "budget_exceeded",
                                             "cost limit", spent_aud)
                        code = _extract_code(reply["text"])
                        history.append({"role": "assistant",
                                        "content": reply["text"]})
                    except Exception as exc:  # noqa: BLE001
                        return self._end(task_id, "error",
                                         f"builder failed: {exc}", spent_aud)
                    if code is None:
                        seq += 1
                        self.journal.append_step(
                            task_id, seq, "protocol_error",
                            result={"error": "no python code block in "
                                    "builder reply"})
                        changed = [n for n, t in
                                   _snapshot_files(jail.root).items()
                                   if files_at_start.get(n) != t]
                        if changed:
                            return self._end(
                                task_id, "completed",
                                "builder stopped following protocol but "
                                "produced artifacts: "
                                + ", ".join(sorted(changed)[:5]),
                                spent_aud)
                        return self._end(task_id, "error",
                                         "builder not following protocol",
                                         spent_aud)

                res = _execute_kernel(self.kernel, code, self.kernel_timeout)
                results_txt: List[str] = []
                terminated: Optional[Dict[str, Any]] = None

                if res.tool_call is None:
                    # Pure kernel computation (or a shell block). Kernel
                    # errors are fed back so the builder can self-correct;
                    # they never fail the task.
                    seq += 1
                    if seq > budget.max_steps:
                        return self._end(task_id, "budget_exceeded",
                                         "step limit", spent_aud)
                    if res.ok:
                        payload: Dict[str, Any] = {"answer": res.result[:2000]}
                        results_txt.append(f"kernel -> {res.result[:2000]}")
                    else:
                        payload = {"answer": f"kernel error: {res.error}"[:2000]}
                        results_txt.append(f"kernel error: {res.error}"[:2000])
                    if res.stdout:
                        payload["stdout"] = res.stdout[:500]
                        results_txt.append(res.stdout[:500])
                    if res.stderr:
                        payload["stderr"] = res.stderr[:500]
                    self.journal.append_step(
                        task_id, seq, "kernel", tool="kernel_exec",
                        result=payload, model=model_builder)
                    self._emit(task_id, "step",
                               {"seq": seq, "tool": "kernel_exec",
                                "result": payload})
                    if res.ok:
                        kernel_execs += 1
                        if (self.snapshot_every > 0
                                and kernel_execs % self.snapshot_every == 0):
                            ok = snapshot_best_effort(kernel_execs)
                            self.journal.append_step(
                                task_id, seq, "kernel", tool="kernel_snapshot",
                                args={"path": str(snap_path(kernel_execs))},
                                result={"ok": ok})
                else:
                    # _ToolCall handoff: the kernel raised a host tool.
                    name = str(res.tool_call.get("name") or "")
                    args = res.tool_call.get("args") or {}
                    if name in ("__plan__", "update_plan"):
                        seq += 1
                        if seq > budget.max_steps:
                            return self._end(task_id, "budget_exceeded",
                                             "step limit", spent_aud)
                        plan = str(args.get("plan", ""))
                        self.journal.append_step(
                            task_id, seq, "plan", tool="update_plan",
                            args=args, result={"ok": True},
                            model=model_builder)
                        self._emit(task_id, "plan_update", {"plan": plan})
                        results_txt.append("plan updated")
                        if spec_mode and not self._approve.is_set():
                            # Park until the human signs off on the plan.
                            self.journal.set_status(task_id, "awaiting_plan")
                            self._emit(task_id, "awaiting_plan", {"plan": plan})
                            while not self._approve.wait(0.25):
                                if self._cancel.is_set():
                                    return self._end(task_id, "cancelled",
                                                     "cancelled by user",
                                                     spent_aud)
                                if (time.time() - start
                                        > budget.max_wall_min * 60):
                                    return self._end(
                                        task_id, "budget_exceeded",
                                        "plan not approved in time", spent_aud)
                            self.journal.set_status(task_id, "running")
                            self._emit(task_id, "plan_approved", {})
                            results_txt.append("plan approved by user - proceed")
                    elif spec_mode and not self._approve.is_set():
                        # Nothing runs before the human signs the plan
                        # (parent rule, kernel flavor).
                        results_txt.append(
                            "SPEC MODE: no action is allowed before the plan "
                            "is approved. Call update_plan(plan) and wait "
                            "for approval.")
                    elif name in ("__finish__", "finish"):
                        seq += 1
                        if seq > budget.max_steps:
                            return self._end(task_id, "budget_exceeded",
                                             "step limit", spent_aud)
                        summary = str(args.get("summary", ""))
                        self.journal.append_step(
                            task_id, seq, "finish", tool="finish",
                            args=args, result={"ok": True},
                            model=model_builder)
                        feedback: Optional[str] = None
                        if (self.reviewer is not None
                                and review_rounds < _MAX_REVIEW_ROUNDS):
                            review_rounds += 1
                            seq += 1
                            feedback = self._review_gate(
                                task_id, seq, jail, goal, summary,
                                model_reviewer)
                        if feedback is None and self.judge is not None:
                            if judge_rounds < self._judge_cap():
                                judge_rounds += 1
                                seq += 1
                                feedback = self._judge_gate(
                                    task_id, seq, jail, goal, summary,
                                    model_reviewer)
                            elif self._last_judge_failed:
                                return self._end(
                                    task_id, "failed",
                                    "judge rejected after "
                                    f"{self._judge_cap()} submissions",
                                    spent_aud)
                        if feedback is None:
                            self._last_judge_failed = False
                            terminated = self._end(task_id, "completed",
                                                   summary, spent_aud)
                        else:
                            results_txt.append(feedback)
                    elif name in _HOST_TOOLS:
                        seq += 1
                        if seq > budget.max_steps:
                            return self._end(task_id, "budget_exceeded",
                                             "step limit", spent_aud)
                        t0 = time.time()
                        try:
                            res_tool = self._exec(
                                jail, {"action": name, "args": args})
                            kind = "tool"
                        except (ToolError, KeyError) as exc:
                            res_tool = {"error": str(exc)}
                            kind = "tool_error"
                        self.journal.append_step(
                            task_id, seq, kind, tool=name, args=args,
                            result=res_tool, model=model_builder,
                            duration_ms=int((time.time() - t0) * 1000))
                        if (name in ("write_file", "edit_file")
                                and isinstance(res_tool, dict)
                                and "error" not in res_tool):
                            self._record_artifact(task_id, name, args)
                        self._emit(task_id, "step",
                                   {"seq": seq, "tool": name,
                                    "result": res_tool})
                        try:
                            self.kernel.set_tool_result(json.dumps(res_tool))
                        except Exception:  # noqa: BLE001
                            # A dead kernel just respawns on the next execute;
                            # the model still sees the result in results_txt.
                            pass
                        results_txt.append(f"{name} -> {str(res_tool)[:2000]}")
                    else:
                        seq += 1
                        if seq > budget.max_steps:
                            return self._end(task_id, "budget_exceeded",
                                             "step limit", spent_aud)
                        self.journal.append_step(
                            task_id, seq, "tool_error", tool=name, args=args,
                            result={"error": f"unknown tool: {name}"},
                            model=model_builder)
                        results_txt.append(
                            f"unknown tool: {name}. Valid tools: update_plan, "
                            f"finish, {', '.join(sorted(_HOST_TOOLS))}.")

                if terminated is not None:
                    return terminated
                history.append({"role": "user",
                                "content": "\n".join(results_txt)[:4000]})
                history = self.compact_history(history)

            return self._end(task_id, "budget_exceeded", "turn limit",
                             spent_aud)
        finally:
            # One final best-effort snapshot so the task-end state survives
            # (cadence already covered the multiples of snapshot_every).
            if (self.kernel is not None and kernel_execs > 0
                    and self.snapshot_every > 0
                    and kernel_execs % self.snapshot_every != 0):
                snapshot_best_effort(kernel_execs)
            # Reap the per-run kernel subprocess.
            try:
                self.kernel.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = ["KernelTaskEngine"]
