"""LongTaskEngine — plan -> act -> journal -> review -> finish/budget.

P2 hardening on top of the P1 core loop:
- cost guardrails: every builder/reviewer reply is metered in AUD and trips
  the task when max_cost_aud is exceeded (and feeds CostTracker);
- history compaction: long runs can never balloon the prompt — old turns are
  folded into one digest line;
- review-and-continue gate: a second model (local FABLE by default = free)
  can reject a finish and send the builder back in with the issues.
Git checkpoints and pause/resume land in P3
(see docs/superpowers/specs/2026-08-05-long-horizon-coder-design.md).
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import judge as _judge
from . import vision as _vision
from .journal import LongTaskJournal
from .protocol import ProtocolError, parse_actions
from .tools import (PathJail, ToolError, t_edit_file, t_glob, t_grep,
                    t_list_dir, t_read_file, t_run_command, t_web_fetch,
                    t_write_file)

try:
    from backend.core.router import USD_PER_AUD
except ImportError:  # running with backend/ as the working directory
    from core.router import USD_PER_AUD  # type: ignore[no-redef]

try:
    from backend.core.design_module import DESIGN_REQUEST_RE, DESIGN_SYSTEM_PROMPT
except ImportError:  # running with backend/ as the working directory
    from core.design_module import DESIGN_REQUEST_RE, DESIGN_SYSTEM_PROMPT  # type: ignore[no-redef]

# Hard safety valve: total builder turns can never exceed 4x the step budget.
_MAX_TURN_FACTOR = 4

# History compaction knobs: keep the system prompt and the last _KEEP_TAIL
# messages, fold everything in between into one digest line.
_COMPACTION_THRESHOLD = 24
_KEEP_TAIL = 10
# Size budget (fuzz 2026-08-11): count-based compaction alone cannot stop a
# context overflow -- one 200KB read_file result in the tail blows the model
# window. Every message is capped and the whole history is clipped to a
# character budget (~tokens/4) before it goes to the builder.
_MAX_MSG_CHARS = 4000
_MAX_HISTORY_CHARS = 48_000


def _snapshot_files(root: Path) -> Dict[str, float]:
    """relpath -> mtime for every file under root (hidden dirs like .kernel
    skipped, walk capped). Rescue-at-protocol-death evidence, never
    load-bearing."""
    out: Dict[str, float] = {}
    for pth in root.rglob("*"):
        if any(part.startswith(".") for part in pth.parts):
            continue
        if pth.is_file():
            out[str(pth.relative_to(root))] = pth.stat().st_mtime
        if len(out) >= 20000:
            break
    return out

# The reviewer gets at most this many chances to send the builder back.
_MAX_REVIEW_ROUNDS = 2


@dataclass
class Budget:
    max_steps: int = 40
    max_cost_aud: float = 2.0
    max_wall_min: int = 120


SYSTEM = (
    "You are Infinity Code's long-horizon coding agent. You work ONLY inside "
    "the given repo. Each reply: a short thought (optional) then one or more "
    'JSON action blocks: {"action": <name>, "args": {...}}. Actions: '
    "read_file{path}, list_dir{path}, grep{pattern,path}, glob{pattern}, "
    "write_file{path,content}, edit_file{path,old,new,replace_all}, "
    "run_command{command}, web_fetch{url,max_chars}, "
    "verify_screenshot{candidate,reference}, "
    "update_plan{plan}, finish{summary}. "
    "Your first action should be update_plan with a markdown checklist. "
    "Never invent file contents you have not read. Call finish when done."
)

_REVIEW_PROMPT_HEAD = (
    "You are a strict code reviewer for a long-horizon coding task. "
    "Judge whether the work meets the goal. Reply with ONLY JSON: "
    '{"approve": true/false, "issues": ["...", "..."]}. '
    "Approve when the goal is met; reject only with concrete, actionable "
    "issues."
)


def _review_prompt(goal: str, summary: str, diff: str) -> str:
    # Built by concatenation on purpose: the JSON example above contains
    # literal braces that str.format would choke on.
    return (_REVIEW_PROMPT_HEAD + "\n\nGOAL: " + goal +
            "\n\nFINAL SUMMARY: " + summary + "\n\nDIFF STAT:\n" + diff)


def _usd_to_aud(cost_usd: float) -> float:
    return float(cost_usd) / USD_PER_AUD if USD_PER_AUD else 0.0


class LongTaskEngine:
    def __init__(self, builder: Any, journal: LongTaskJournal,
                 cost_tracker: Any = None, reviewer: Any = None,
                 event_cb: Optional[Callable[[str, str, Dict], None]] = None,
                 references_dir: Optional[Path] = None,
                 judge: Any = None,
                 harness: Any = None) -> None:
        self.builder = builder
        self.journal = journal
        self.cost_tracker = cost_tracker
        self.reviewer = reviewer
        self.event_cb = event_cb  # P3: WS bridge; P1: optional callback
        self.references_dir = references_dir  # P7: locked UI references
        self.judge = judge  # P9: rubric judge on a forked sandbox
        self._last_judge_failed = False
        self.harness = harness  # cross-cutting versioned harness config
        self._cancel = threading.Event()
        # Spec mode parks the run at the first plan until approve_plan().
        self._approve = threading.Event()
        self._tool_approve = threading.Event()
        self._tool_reject = threading.Event()
        self._tool_reject_reason = ""

    def approve_plan(self) -> None:
        """Release a spec-mode run parked at its plan."""
        self._approve.set()

    def approve_tool(self) -> None:
        """Release an ask-mode run parked at a write/run action."""
        self._tool_approve.set()

    def reject_tool(self, reason: str = "") -> None:
        """Reject a parked ask-mode action. The loop skips execution and
        feeds the denial back to the builder so it can adjust course."""
        self._tool_reject_reason = reason or ""
        self._tool_reject.set()

    def _judge_cap(self) -> int:
        """Judge submission cap from the harness config, else the default."""
        h = getattr(self, "harness", None)
        cap = getattr(getattr(h, "judge", None), "submission_cap", None)
        return int(cap) if isinstance(cap, int) and cap > 0 else _judge.JUDGE_CAP

    def request_cancel(self) -> None:
        """Ask the loop to stop before the next builder turn. Cancel
        is cooperative: the in-flight LLM call finishes first."""
        self._cancel.set()

    # --- helpers --------------------------------------------------------- #

    def _emit(self, task_id: str, kind: str, payload: Dict[str, Any]) -> None:
        if self.event_cb:
            try:
                self.event_cb(task_id, kind, payload)
            except Exception:  # noqa: BLE001
                pass

    def _chat(self, history: List[Dict[str, str]],
              max_tokens: int = 3000) -> Dict[str, Any]:
        reply = self.builder.chat(history, max_tokens=max_tokens)
        if isinstance(reply, dict):
            return {"text": str(reply.get("text", "")),
                    "cost_usd": float(reply.get("cost_usd", 0.0))}
        return {"text": str(reply), "cost_usd": 0.0}

    def _record_cost(self, cost_usd: float) -> float:
        """Meter one LLM reply in AUD; also feed the app-wide cost tracker."""
        aud = _usd_to_aud(cost_usd)
        if aud > 0 and self.cost_tracker is not None:
            try:
                self.cost_tracker.record_actual(aud)
            except Exception:  # noqa: BLE001 - metering never kills a task
                pass
        return aud

    @staticmethod
    def compact_history(history: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Bound the prompt size: keep the system message and the last
        _KEEP_TAIL messages; fold the middle into one digest line.

        Size-bounded too, not just count-bounded (fuzz 2026-08-11): each
        message is capped at _MAX_MSG_CHARS and the whole history at
        _MAX_HISTORY_CHARS, so a giant tool result can never overflow the
        model context."""

        def _cap(m: Dict[str, str]) -> Dict[str, str]:
            c = str(m.get("content", ""))
            if len(c) > _MAX_MSG_CHARS:
                c = (c[:_MAX_MSG_CHARS]
                     + f"\n[... truncated {len(c) - _MAX_MSG_CHARS} chars]")
            return {"role": m["role"], "content": c}

        if len(history) > _COMPACTION_THRESHOLD:
            head, tail = history[:1], history[-_KEEP_TAIL:]
            middle = history[1:-_KEEP_TAIL]
            digest = "; ".join(str(m.get("content", ""))[:60]
                               for m in middle[:12])
            history = head + [{"role": "user",
                               "content": f"Earlier steps (condensed): {digest}"}] + tail
        history = [_cap(m) for m in history]

        total = sum(len(m["content"]) for m in history)
        if total > _MAX_HISTORY_CHARS and len(history) > 2:
            keep = [history[0]]            # system prompt always survives
            budget = _MAX_HISTORY_CHARS - len(history[0]["content"])
            for m in reversed(history[1:]):  # most recent first
                budget -= len(m["content"])
                if budget < 0:
                    break
                keep.append(m)
            keep.reverse()
            if len(keep) <= 1:             # pathological: keep last msg only
                keep = [history[0], history[-1]]
            history = keep
        return history

    def _exec(self, jail: PathJail, action: Dict[str, Any]) -> Dict[str, Any]:
        name, a = action["action"], action.get("args") or {}
        if name == "read_file":
            return {"content": t_read_file(jail, a["path"])}
        if name == "list_dir":
            return {"entries": t_list_dir(jail, a.get("path", "."))}
        if name == "grep":
            return {"hits": t_grep(jail, a["pattern"], a.get("path", "."))}
        if name == "glob":
            return {"matches": t_glob(jail, a["pattern"])}
        if name == "write_file":
            return {"ok": True,
                    "detail": t_write_file(jail, a["path"], a["content"])}
        if name == "edit_file":
            return {"ok": True, "detail": t_edit_file(
                jail, a["path"], a["old"], a["new"], bool(a.get("replace_all")))}
        if name == "run_command":
            return t_run_command(jail, a["command"], int(a.get("timeout", 120)))
        if name == "web_fetch":
            if a.get("max_chars"):
                return t_web_fetch(jail, a["url"], int(a["max_chars"]))
            return t_web_fetch(jail, a["url"])
        if name == "verify_screenshot":
            return self._verify_screenshot(jail, a)
        raise ToolError(f"unhandled action: {name}")

    def _verify_screenshot(self, jail: PathJail,
                           a: Dict[str, Any]) -> Dict[str, Any]:
        """Judge a candidate image against a locked reference (P7).
        candidate is a repo-relative image path; reference is matched
        against the locked references directory by stem or filename."""
        if not self.references_dir:
            raise ToolError("no references_dir configured for vision")
        cand = jail.resolve(a.get("candidate", ""))
        if not cand.is_file():
            raise ToolError(f"candidate image not found: {a.get('candidate')}")
        want = str(a.get("reference", "")).strip()
        ref_path = None
        for p in Path(self.references_dir).glob("*"):
            if p.stem == want or p.name == want:
                ref_path = p
                break
        if ref_path is None:
            raise ToolError(f"unknown locked reference: {want}")
        threshold = float(a.get("threshold", 0.85))
        return _vision.verify_ui(cand, ref_path, threshold=threshold)

    def _record_artifact(self, task_id: str, name: str,
                         args: Dict[str, Any]) -> None:
        """Journal what the builder just wrote, with a cheap compile gate.
        Failure-proof: artifacts are evidence, never load-bearing."""
        try:
            path = str(args.get("path", ""))
            if name == "write_file":
                body = str(args.get("content", ""))
            else:
                body = str(args.get("new", ""))
            gates: Dict[str, Any] = {}
            if name == "write_file" and path.endswith(".py"):
                try:
                    compile(body, path, "exec")
                    gates["compile"] = "pass"
                except SyntaxError as exc:
                    gates["compile"] = "fail"
                    gates["detail"] = str(exc)[:200]
            self.journal.add_artifact(task_id, "code", path, body, gates)
            self._emit(task_id, "artifact", {"path": path, "gates": gates})
        except Exception:  # noqa: BLE001 - evidence never breaks the run
            pass

    def _end(self, task_id: str, status: str, result: str,
             spent_aud: float = 0.0) -> Dict[str, Any]:
        self.journal.set_status(task_id, status, result, cost_aud=spent_aud)
        self._emit(task_id, "done", {"status": status, "result": result})
        return {"task_id": task_id, "status": status, "result": result}

    # --- review gate ------------------------------------------------------ #

    def _diff_stat(self, jail: PathJail) -> str:
        try:
            res = t_run_command(jail, "git diff --stat HEAD", timeout=15)
            return str(res.get("stdout", ""))[:2000]
        except Exception:  # noqa: BLE001 - no git repo = no diff, still review
            return ""

    def _review_gate(self, task_id: str, seq: int, jail: PathJail, goal: str,
                     summary: str, model_reviewer: str) -> Optional[str]:
        """Ask the reviewer whether finish may stand. Returns None to approve,
        or a feedback string to feed back to the builder."""
        t0 = time.time()
        content = _review_prompt(goal, summary, self._diff_stat(jail))
        if getattr(self, "_style_hints", None):
            content += (
                "\n\nSTYLE CONTRACT (locked references the user liked):\n- "
                + "\n- ".join(h[:200] for h in self._style_hints[:3])
                + "\nAlso include \"fidelity\": <0.0-1.0> in your JSON "
                  "verdict, estimating how closely the work honors the "
                  "style contract."
            )
        try:
            reply = self.reviewer.chat([
                {"role": "user", "content": content},
            ], max_tokens=800)
        except Exception as exc:  # noqa: BLE001 - a dead reviewer never blocks
            self.journal.append_step(
                task_id, seq, "review", tool="review",
                result={"error": str(exc)[:200]}, model=model_reviewer)
            return None
        text = reply.get("text", "") if isinstance(reply, dict) else str(reply)
        cost_usd = float(reply.get("cost_usd", 0.0)) if isinstance(reply, dict) else 0.0
        try:
            verdict = json.loads(text[text.find("{"):text.rfind("}") + 1])
            approve = bool(verdict.get("approve", True))
            issues = [str(i)[:200] for i in (verdict.get("issues") or [])][:5]
            fidelity = verdict.get("fidelity")
            try:
                fidelity = (round(float(fidelity), 3)
                            if fidelity is not None else None)
            except (TypeError, ValueError):
                fidelity = None
        except Exception:  # noqa: BLE001 - unreadable verdict = approve
            approve, issues, fidelity = True, [], None
        res_payload: Dict[str, Any] = {"approve": approve, "issues": issues}
        if fidelity is not None:
            res_payload["fidelity"] = fidelity
        self.journal.append_step(
            task_id, seq, "review", tool="review",
            args={"goal": goal[:200]},
            result=res_payload,
            model=model_reviewer, cost_aud=_usd_to_aud(cost_usd),
            duration_ms=int((time.time() - t0) * 1000))
        self._emit(task_id, "review", res_payload)
        if approve:
            return None
        return "REVIEWER REJECTED the finish. Fix these issues, then finish again:\n- " + \
               "\n- ".join(issues) if issues else \
               "REVIEWER REJECTED the finish. Re-check the goal and try again."

    def _judge_gate(self, task_id: str, seq: int, jail: PathJail, goal: str,
                    summary: str, model_judge: str) -> Optional[str]:
        """P9: rubric judge on a forked sandbox. Returns None to pass, or
        feedback to send the builder back. Hidden checks can force a fail."""
        t0 = time.time()
        try:
            fork = _judge.fork_sandbox(jail.root)
        except Exception as exc:  # noqa: BLE001 - no fork = no judgement
            self.journal.append_step(
                task_id, seq, "judge", tool="judge",
                result={"error": f"fork failed: {str(exc)[:160]}"},
                model=model_judge)
            self._last_judge_failed = False
            return None
        try:
            scorepad = _judge.run_rubric_judge(fork, goal, self.judge.chat)
        except Exception as exc:  # noqa: BLE001 - a dead judge never blocks
            self.journal.append_step(
                task_id, seq, "judge", tool="judge",
                result={"error": str(exc)[:200]}, model=model_judge)
            self._last_judge_failed = False
            return None
        self._last_judge_failed = not bool(scorepad.get("pass"))
        self.journal.append_step(
            task_id, seq, "judge", tool="judge",
            args={"goal": goal[:200]}, result=scorepad,
            model=model_judge, cost_aud=0.0,
            duration_ms=int((time.time() - t0) * 1000))
        self._emit(task_id, "judge", scorepad)
        if scorepad.get("pass"):
            return None
        issues = scorepad.get("issues") or []
        head = ("JUDGE REJECTED on the rubric (score "
                + str(scorepad.get("total")) + "). Fix:\n- ")
        return head + "\n- ".join(issues) if issues else \
            "JUDGE REJECTED on the rubric. Re-check the goal and try again."

    # --- the loop ---------------------------------------------------------- #

    def run(self, goal: str, repo_path: str, budget: Optional[Budget] = None,
            autonomy: str = "ask", model_builder: str = "builder",
            model_reviewer: str = "reviewer",
            lessons: Optional[List[str]] = None,
            task_id: Optional[str] = None,
            spec_mode: bool = False,
            style_hints: Optional[List[str]] = None) -> Dict[str, Any]:
        self._style_hints = list(style_hints or [])
        budget = budget or Budget()
        jail = PathJail(Path(repo_path))
        # Async launches pre-create the journal row (so the caller gets an
        # id back instantly) and pass it in via task_id.
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
        start = time.time()
        # Rescue-at-protocol-death: if the builder dies on protocol AFTER
        # producing file changes, the run ends completed, not error.
        files_at_start = _snapshot_files(jail.root)
        system = SYSTEM + f"\n\nREPO: {jail.root}\nGOAL: {goal}"
        # Design module: the BFB builder is DeepSeek V4 Flash - give it
        # the design playbook on UI-shaped goals so it ships tasteful UI.
        if DESIGN_REQUEST_RE.search(goal):
            system += "\n\n" + DESIGN_SYSTEM_PROMPT
        if lessons:
            system += ("\n\nLESSONS LEARNED FROM PAST RUNS:\n- " +
                       "\n- ".join(str(l)[:200] for l in lessons[:5]))
        history: List[Dict[str, str]] = [{"role": "system", "content": system}]

        for _turn in range(budget.max_steps * _MAX_TURN_FACTOR):
            if self._cancel.is_set():
                return self._end(task_id, "cancelled", "cancelled by user",
                                 spent_aud)
            if time.time() - start > budget.max_wall_min * 60:
                return self._end(task_id, "budget_exceeded", "wall-clock limit",
                                 spent_aud)

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

            try:
                actions = parse_actions(reply["text"])
            except ProtocolError:
                # One automatic re-ask before failing the task (spec §11).
                history.append({"role": "user", "content":
                                "Reply with valid JSON action blocks only. "
                                "Example: {\"action\": \"list_dir\", "
                                "\"args\": {\"path\": \".\"}}"})
                try:
                    reply = self._chat(history)
                    spent_aud += self._record_cost(reply["cost_usd"])
                    actions = parse_actions(reply["text"])
                    history.append({"role": "assistant", "content": reply["text"]})
                except (ProtocolError, KeyError, RecursionError, ValueError) as exc:
                    seq += 1
                    self.journal.append_step(
                        task_id, seq, "protocol_error",
                        result={"error": str(exc)[:200]})
                    changed = [n for n, t in
                               _snapshot_files(jail.root).items()
                               if files_at_start.get(n) != t]
                    if changed:
                        return self._end(
                            task_id, "completed",
                            "builder stopped following protocol but produced "
                            "artifacts: " + ", ".join(sorted(changed)[:5]),
                            spent_aud)
                    return self._end(task_id, "error",
                                     "builder not following protocol", spent_aud)
                except Exception as exc:  # noqa: BLE001
                    return self._end(task_id, "error", f"builder failed: {exc}",
                                     spent_aud)

            # Conflicting tool selections (fuzz 2026-08-11): a reply may
            # contain byte-identical duplicate blocks (builder confusion or
            # hostile output). Executing them twice wastes step budget and
            # re-runs side effects; dedupe before dispatch.
            seen_sigs: set = set()
            unique_actions: List[Dict[str, Any]] = []
            for act in actions:
                try:
                    sig = json.dumps([act.get("action"), act.get("args")],
                                     sort_keys=True, default=str)
                except (TypeError, ValueError):
                    sig = repr(act)
                if sig in seen_sigs:
                    continue
                seen_sigs.add(sig)
                unique_actions.append(act)
            if len(unique_actions) < len(actions):
                self.journal.append_step(
                    task_id, 0, "note", result={
                        "deduped_actions": len(actions) - len(unique_actions)})

            results_txt: List[str] = []
            terminated: Optional[Dict[str, Any]] = None
            for action in unique_actions:
                seq += 1
                if seq > budget.max_steps:
                    return self._end(task_id, "budget_exceeded", "step limit",
                                     spent_aud)
                name = action["action"]
                if spec_mode and not self._approve.is_set() \
                        and name != "update_plan":
                    # Nothing runs before the human signs the plan.
                    results_txt.append(
                        "SPEC MODE: no action is allowed before the plan is "
                        "approved. Submit update_plan with your plan and "
                        "wait for approval.")
                    continue
                if name == "update_plan":
                    plan = str(action["args"].get("plan", ""))
                    self.journal.append_step(
                        task_id, seq, "plan", tool="update_plan",
                        args=action["args"], result={"ok": True},
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
                                                 "cancelled by user", spent_aud)
                            if time.time() - start > budget.max_wall_min * 60:
                                return self._end(
                                    task_id, "budget_exceeded",
                                    "plan not approved in time", spent_aud)
                        self.journal.set_status(task_id, "running")
                        self._emit(task_id, "plan_approved", {})
                        results_txt.append("plan approved by user - proceed")
                    continue
                if name == "finish":
                    summary = str(action["args"].get("summary", ""))
                    self.journal.append_step(
                        task_id, seq, "finish", tool="finish",
                        args=action["args"], result={"ok": True},
                        model=model_builder)
                    feedback: Optional[str] = None
                    if self.reviewer is not None and review_rounds < _MAX_REVIEW_ROUNDS:
                        review_rounds += 1
                        seq += 1
                        feedback = self._review_gate(
                            task_id, seq, jail, goal, summary, model_reviewer)
                    if feedback is None and self.judge is not None:
                        if judge_rounds < self._judge_cap():
                            judge_rounds += 1
                            seq += 1
                            feedback = self._judge_gate(
                                task_id, seq, jail, goal, summary, model_reviewer)
                        elif self._last_judge_failed:
                            return self._end(
                                task_id, "failed",
                                "judge rejected after "
                                f"{self._judge_cap()} submissions", spent_aud)
                    if feedback is None:
                        self._last_judge_failed = False
                        terminated = self._end(task_id, "completed", summary,
                                               spent_aud)
                        break
                    results_txt.append(feedback)
                    continue
                # Autonomy gate: in "ask" mode, write/run actions need
                # explicit approval from the user before execution.
                _GATED_ACTIONS = {"write_file", "edit_file", "run_command"}
                if (autonomy == "ask" and name in _GATED_ACTIONS
                        and not self._cancel.is_set()):
                    self._tool_approve.clear()
                    self._tool_reject.clear()
                    self._tool_reject_reason = ""
                    self._emit(task_id, "awaiting_tool_approval",
                               {"action": name, "args": action.get("args") or {}})
                    while not (self._tool_approve.is_set()
                               or self._tool_reject.is_set()):
                        self._tool_approve.wait(0.25)
                        if self._cancel.is_set():
                            return self._end(task_id, "cancelled",
                                             "cancelled by user", spent_aud)
                        if time.time() - start > budget.max_wall_min * 60:
                            return self._end(task_id, "budget_exceeded",
                                             "tool approval timeout", spent_aud)
                    if self._tool_reject.is_set():
                        reason = self._tool_reject_reason or "no reason given"
                        self._emit(task_id, "tool_rejected",
                                   {"action": name, "reason": reason})
                        self.journal.append_step(
                            task_id, seq, "tool_denied", tool=name,
                            args=action.get("args") or {},
                            result={"denied": True, "reason": reason},
                            model=model_builder)
                        results_txt.append(
                            f"{name} DENIED by user ({reason}). Do not retry "
                            "the same action; choose a different approach.")
                        continue
                    self._emit(task_id, "tool_approved",
                               {"action": name})
                t0 = time.time()
                try:
                    res = self._exec(jail, action)
                    kind = "tool"
                except (ToolError, KeyError) as exc:
                    res = {"error": str(exc)}
                    kind = "tool_error"
                self.journal.append_step(
                    task_id, seq, kind, tool=name, args=action["args"],
                    result=res, model=model_builder,
                    duration_ms=int((time.time() - t0) * 1000))
                if (name in ("write_file", "edit_file")
                        and isinstance(res, dict) and "error" not in res):
                    self._record_artifact(task_id, name, action.get("args") or {})
                self._emit(task_id, "step",
                           {"seq": seq, "tool": name, "result": res})
                results_txt.append(f"{name} -> {str(res)[:2000]}")
            if terminated is not None:
                return terminated
            history.append({"role": "user", "content": "\n".join(results_txt)})
            history = self.compact_history(history)

        return self._end(task_id, "budget_exceeded", "turn limit", spent_aud)


__all__ = ["LongTaskEngine", "Budget"]
