"""Eval harness for the custom model strategy.

Run a fixed suite of tasks against any lane/model and produce a score report.
The harness is intentionally dependency-light: it calls a client that matches
OpenRouterClient.chat(..., session_id, lane) and scores the returned text.
"""

from __future__ import annotations

import ast
import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from backend.core.router import LaneRouter
except ImportError:  # running with backend/ as the working directory
    from core.router import LaneRouter  # type: ignore[no-redef]

logger = logging.getLogger("infinity.eval")

DEFAULT_TASKS_PATH: Path = Path(__file__).resolve().parent.parent / "data" / "eval_tasks.jsonl"
_LOCAL_EVAL_ENDPOINTS: Dict[str, tuple[str, str, Dict[str, Any], bool]] = {
    "local/fable-fast": ("http://127.0.0.1:1234/v1", "fable-fast", {}, False),
    "local/fable-max-35b": ("http://127.0.0.1:8081/v1", "fable-max-35b", {}, False),
    "local/fable-fusion-27b": ("http://127.0.0.1:8082/v1", "fable-fusion-27b", {}, False),
    # Ollama native API. The OpenAI-compat endpoint in Ollama 0.31.x IGNORES
    # think:false (returns empty content — all tokens go to reasoning), so we
    # use /api/chat directly with think:false + options.num_predict.
    "local/qwen3:8b": ("http://localhost:11434", "qwen3:8b", {"think": False}, True),
}


@dataclass
class EvalTask:
    id: str
    name: str
    lane: str
    prompt: str
    expected_contains: Optional[List[str]] = None
    expected_contains_any: Optional[List[str]] = None
    expected_exact: Optional[str] = None
    check_syntax: bool = False
    check_run: bool = False
    expected_output: Optional[str] = None
    max_output_chars: int = 0
    max_tokens: int = 1500
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    system: Optional[str] = None


@dataclass
class EvalResult:
    task_id: str
    name: str
    lane: str
    model: str
    passed: bool
    score: float
    output: str
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    reason: str = ""
    capability: str = "general"


class EvalHarness:
    """Benchmark model lanes against a fixed task suite."""

    def __init__(
        self,
        client: Any,
        router: Any,
        data_dir: Path,
    ) -> None:
        self.client = client
        self.router = router
        self.data_dir = Path(data_dir).resolve()
        self.lane_router: Optional[LaneRouter] = (
            router if isinstance(router, LaneRouter) else LaneRouter(router)
        ) if router is not None else None

    def load_tasks(
        self,
        tasks_path: Optional[Path] = None,
        profile: str = "all",
    ) -> List[EvalTask]:
        """Load tasks, optionally selecting a named benchmark profile.

        Profiles let the cheap lanes practise a focused capability (for example
        ``spatial_3d``) without paying to rerun the whole suite.  Older tasks
        without profile metadata remain in the full suite only.
        """
        path = Path(tasks_path) if tasks_path else DEFAULT_TASKS_PATH
        requested_profile = profile.strip().lower() or "all"
        tasks: List[EvalTask] = []
        if not path.is_file():
            logger.warning("Eval tasks file not found: %s", path)
            return tasks
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError as exc:
                        logger.warning("Skipping malformed eval task line: %s", exc)
                        continue
                    metadata = dict(data.get("metadata", {}))
                    profiles = metadata.get("profiles", ["core"])
                    if isinstance(profiles, str):
                        profiles = [profiles]
                    profile_names = {
                        str(item).strip().lower()
                        for item in profiles
                        if str(item).strip()
                    }
                    if requested_profile != "all" and requested_profile not in profile_names:
                        continue
                    tasks.append(
                        EvalTask(
                            id=str(data.get("id") or uuid.uuid4()),
                            name=str(data["name"]),
                            lane=str(data.get("lane", "cheap")),
                            prompt=str(data["prompt"]),
                            expected_contains=data.get("expected_contains"),
                            expected_contains_any=data.get("expected_contains_any"),
                            expected_exact=data.get("expected_exact"),
                            check_syntax=bool(data.get("check_syntax", False)),
                            check_run=bool(data.get("check_run", False)),
                            expected_output=data.get("expected_output"),
                            max_output_chars=int(data.get("max_output_chars", 0)),
                            max_tokens=int(data.get("max_tokens", 1500)),
                            weight=float(data.get("weight", 1.0)),
                            metadata=metadata,
                            system=data.get("system"),
                        )
                    )
        except OSError as exc:
            logger.error("Could not read eval tasks %s: %s", path, exc)
        return tasks

    @staticmethod
    def _extract_code(text: str) -> str:
        """Pull python out of the first fenced code block, or use the raw text."""
        import re
        match = re.search(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
        return match.group(1).strip() if match else text.strip()

    @staticmethod
    def _run_code(code: str, timeout: int = 15) -> str:
        """Execute code in a subprocess; return stdout (raises on timeout/crash)."""
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "non-zero exit").strip()[:200])
        return proc.stdout

    @staticmethod
    def _norm(text: str) -> str:
        """Lowercase, collapse whitespace, strip quotes so scoring tolerates
        chatty formatting (case, spacing, quote style) without gaming content."""
        import re
        text = text.strip().lower()
        text = text.replace('"', '').replace("'", '')
        text = re.sub(r"\s*=\s*", "=", text)  # code: x = y vs x=y
        text = re.sub(r"-{3,}", "---", text)  # markdown table separators
        return re.sub(r"\s+", " ", text)

    @staticmethod
    def _contains(needle: str, haystack: str) -> bool:
        return EvalHarness._norm(needle) in EvalHarness._norm(haystack)

    @classmethod
    def _score(cls, task: EvalTask, output: str) -> tuple[bool, float, str]:
        output = output.strip()
        if not output:
            return False, 0.0, "empty output"
        # LMArena-style verbosity control: objective cap, no judge needed.
        if task.max_output_chars and len(output) > task.max_output_chars:
            return False, 0.0, f"verbosity: {len(output)} > {task.max_output_chars}"
        # SWE-Bench/SciCode-style execution check: run it, compare stdout.
        if task.check_run:
            code = cls._extract_code(output)
            try:
                stdout = cls._run_code(code)
            except Exception as exc:  # noqa: BLE001
                return False, 0.0, f"run failed: {str(exc)[:120]}"
            if task.expected_output is not None:
                if stdout.strip() == task.expected_output.strip():
                    return True, 1.0, "execution output matched"
                return False, 0.0, "execution output mismatched"
            return True, 1.0, "code executed cleanly"
        if task.expected_exact is not None:
            if output == task.expected_exact.strip():
                return True, 1.0, "exact match"
            # Verbose-model fallback: the answer may be embedded in prose
            # (e.g. "17 * 23 = 378" for an exact "378"). Accept when the
            # expected answer appears as a standalone token (word boundary).
            import re as _re
            if _re.search(rf"(?<!\w){_re.escape(task.expected_exact.strip())}(?!\w)", output, _re.IGNORECASE):
                return True, 1.0, "exact value present in output"
            return False, 0.0, "exact mismatch"
        if task.expected_contains_any:
            found = [s for s in task.expected_contains_any if cls._contains(s, output)]
            if not found:
                return False, 0.0, "none of the expected variants found"
            return True, 1.0, f"matched variant: {found[0]}"
        if task.expected_contains:
            missing = [s for s in task.expected_contains if not cls._contains(s, output)]
            if missing:
                return False, 0.0, f"missing: {', '.join(missing[:3])}"
            return True, 1.0, "all expected substrings found"
        if task.check_syntax:
            code = cls._extract_code(output)
            try:
                ast.parse(code)
                return True, 1.0, "python syntax ok"
            except SyntaxError as exc:
                return False, 0.0, f"syntax error: {exc}"
        # Default: any non-empty response passes.
        return True, 1.0, "non-empty output"

    def _resolve_model(self, lane: str, model_id: Optional[str]) -> str:
        if model_id:
            return model_id
        if self.lane_router is not None:
            # ``chain`` preserves configured custom lanes such as fable_fast;
            # generic route selection would otherwise demote them to cloud cheap.
            return self.lane_router.chain(lane)[0].id
        return "qwen/qwen3-coder"

    @staticmethod
    def _local_endpoint(model_id: str) -> tuple[str, str, Dict[str, Any], bool]:
        if model_id in _LOCAL_EVAL_ENDPOINTS:
            url, served, extra, native = _LOCAL_EVAL_ENDPOINTS[model_id]
            return url, served, dict(extra), native
        base_url = str(__import__("os").environ.get("INFINITY_LOCAL_EVAL_URL") or "http://127.0.0.1:1234/v1")
        return base_url.rstrip("/"), model_id.removeprefix("local/"), {}, False

    def _chat_local(self, model_id: str, messages: List[Dict[str, str]], max_tokens: int) -> str:
        """Call an owner-controlled local server, never the cloud.

        Two paths: the OpenAI-compatible protocol, or Ollama's native /api/chat
        (used when the server ignores OpenAI-style options — e.g. qwen3's
        think:false is only honored on the native endpoint in Ollama 0.31.x).
        """
        base_url, served_model, extra_body, native_ollama = self._local_endpoint(model_id)
        if native_ollama:
            body = {
                "model": served_model,
                "messages": messages,
                "think": False,
                "stream": False,
                "options": {"num_predict": max_tokens},
            }
            body.update(extra_body)
            path = "/api/chat"
        else:
            body = {"model": served_model, "messages": messages, "max_tokens": max_tokens}
            body.update(extra_body)
            path = "/chat/completions"
        request = urllib.request.Request(
            f"{base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310: owner-configured local endpoint
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise RuntimeError(f"local eval model unavailable at {base_url}: {exc}") from exc
        if native_ollama:
            message = data.get("message", {}) if isinstance(data, dict) else {}
            return str(message.get("content") or "")
        choices = data.get("choices") if isinstance(data, dict) else []
        message = choices[0].get("message", {}) if choices else {}
        return str(message.get("content") or "")

    @staticmethod
    def _capability_for(task: EvalTask) -> str:
        """Return a stable capability label for scorecards and regressions.

        A task can specify ``metadata.capability`` directly.  The fallback keeps
        the built-in suite useful while its older task data is gradually tagged.
        """
        explicit = str(task.metadata.get("capability", "")).strip().lower()
        if explicit:
            return explicit
        text = f"{task.name} {task.prompt}".lower()
        classifiers = (
            ("security", ("inject", "security", "permission", "secret", "attack")),
            ("browser", ("browser", "web page", "website", "dom", "playwright")),
            ("tools", ("terminal", "shell", "git", "docker", "command")),
            ("reasoning", ("plan", "architect", "design", "explain", "reason")),
            ("code", ("python", "function", "code", "algorithm", "syntax")),
        )
        for capability, keywords in classifiers:
            if any(keyword in text for keyword in keywords):
                return capability
        return "general"

    # Small local models (8B-class) are chatty: they explain instead of
    # returning only the requested code/answer, which burns max_tokens and
    # fails substring checks. A task's explicit system prompt wins; otherwise
    # local models get a conciseness directive. Cloud models keep their
    # (benchmark-realistic) bare prompt so scores stay comparable.
    _LOCAL_SYSTEM_PROMPT = (
        "You are a precise, concise assistant. When the user asks for code, "
        "output ONLY the complete code (including all necessary imports), "
        "no explanations, no prose. When the user says 'Reply ONLY with X', "
        "output exactly X and nothing else."
    )

    def _run_task(self, task: EvalTask, model_id: str) -> EvalResult:
        messages: List[Dict[str, str]] = []
        if task.system:
            messages.append({"role": "system", "content": task.system})
        elif model_id.startswith("local/"):
            messages.append({"role": "system", "content": self._LOCAL_SYSTEM_PROMPT})
        messages.append({"role": "user", "content": task.prompt})
        session_id = f"eval-{uuid.uuid4()}"
        start = time.time()
        output = ""
        cost_usd = 0.0
        try:
            if model_id.startswith("local/"):
                output = self._chat_local(model_id, messages, task.max_tokens)
                cost_usd = 0.0
            else:
                result = self.client.chat(
                    model_id,
                    messages,
                    max_tokens=task.max_tokens,
                    session_id=session_id,
                    lane=task.lane,
                )
                if isinstance(result, dict):
                    output = str(result.get("text", ""))
                    cost_usd = float(result.get("cost_usd") or 0.0)
                else:
                    output = str(getattr(result, "text", ""))
                    cost_usd = float(getattr(result, "cost_usd", 0.0) or 0.0)
        except Exception as exc:  # noqa: BLE001
            return EvalResult(
                task_id=task.id,
                name=task.name,
                lane=task.lane,
                model=model_id,
                passed=False,
                score=0.0,
                output="",
                reason=f"call failed: {exc}",
                capability=self._capability_for(task),
            )
        latency_ms = (time.time() - start) * 1000
        passed, score, reason = self._score(task, output)
        return EvalResult(
            task_id=task.id,
            name=task.name,
            lane=task.lane,
            model=model_id,
            passed=passed,
            score=score,
            output=output[:2000],
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            reason=reason,
            capability=self._capability_for(task),
        )

    def run(
        self,
        lane: str = "cheap",
        model_id: Optional[str] = None,
        tasks_path: Optional[Path] = None,
        profile: str = "all",
    ) -> Dict[str, Any]:
        """Run the harness and return a report dict."""
        normalized_profile = profile.strip().lower() or "all"
        tasks = self.load_tasks(tasks_path, normalized_profile)
        if not tasks:
            raise ValueError(f"No eval tasks loaded for profile '{normalized_profile}'.")
        model_id = self._resolve_model(lane, model_id)
        results: List[EvalResult] = []
        total_weight = 0.0
        weighted_score = 0.0
        total_cost = 0.0
        total_latency = 0.0
        passed_count = 0

        for task in tasks:
            result = self._run_task(task, model_id)
            results.append(result)
            total_weight += task.weight
            weighted_score += result.score * task.weight
            total_cost += result.cost_usd
            total_latency += result.latency_ms
            if result.passed:
                passed_count += 1

        aggregate = weighted_score / total_weight if total_weight else 0.0
        capability_scores: Dict[str, Dict[str, Any]] = {}
        for task, task_result in zip(tasks, results):
            capability = self._capability_for(task)
            bucket = capability_scores.setdefault(
                capability,
                {"weighted_score": 0.0, "weight": 0.0, "tasks_total": 0, "tasks_passed": 0, "failed_tasks": []},
            )
            bucket["weighted_score"] += task_result.score * task.weight
            bucket["weight"] += task.weight
            bucket["tasks_total"] += 1
            if task_result.passed:
                bucket["tasks_passed"] += 1
            else:
                bucket["failed_tasks"].append({
                    "id": task.id,
                    "name": task.name,
                    "reason": task_result.reason,
                })

        weaknesses: List[Dict[str, Any]] = []
        capabilities: Dict[str, Dict[str, Any]] = {}
        for capability, bucket in capability_scores.items():
            score = bucket["weighted_score"] / bucket["weight"] if bucket["weight"] else 0.0
            entry = {
                "score": round(score, 4),
                "tasks_total": bucket["tasks_total"],
                "tasks_passed": bucket["tasks_passed"],
                "failed_tasks": bucket["failed_tasks"],
            }
            capabilities[capability] = entry
            if score < 1.0:
                weaknesses.append({"capability": capability, **entry})
        weaknesses.sort(key=lambda item: (item["score"], -item["tasks_total"], item["capability"]))
        report: Dict[str, Any] = {
            "run_id": str(uuid.uuid4()),
            "lane": lane,
            "model": model_id,
            "profile": normalized_profile,
            "tasks_total": len(tasks),
            "tasks_passed": passed_count,
            "aggregate_score": round(aggregate, 4),
            "total_cost_usd": round(total_cost, 6),
            "avg_latency_ms": round(total_latency / len(results), 2) if results else 0.0,
            "summary": {
                "pass_rate": round(passed_count / len(results), 4) if results else 0.0,
                "weighted_score": round(aggregate, 4),
                "weakest_capability": weaknesses[0]["capability"] if weaknesses else None,
            },
            "capabilities": capabilities,
            "weaknesses": weaknesses,
            "results": [asdict(r) for r in results],
        }
        return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Infinity Code eval harness")
    parser.add_argument("--lane", default="cheap", help="Lane to evaluate")
    parser.add_argument("--model", default=None, help="Specific model id")
    parser.add_argument("--tasks", default=None, help="Path to eval_tasks.jsonl")
    parser.add_argument("--profile", default="all", help="Benchmark profile (all, agentic, spatial_3d, game_dev)")
    parser.add_argument("--output", default=None, help="Report output path")
    args = parser.parse_args()

    # Lazy import so the harness can be inspected without a live client.
    try:
        from backend.tools.openrouter_client import OpenRouterClient
    except ImportError:
        from tools.openrouter_client import OpenRouterClient  # type: ignore

    _client = OpenRouterClient()
    _harness = EvalHarness(_client, None, Path.cwd())
    _report = _harness.run(
        lane=args.lane,
        profile=args.profile,
        model_id=args.model,
        tasks_path=Path(args.tasks) if args.tasks else None,
    )
    _out = (
        Path(args.output)
        if args.output
        else Path.cwd() / f"eval_{args.lane}_{int(time.time())}.json"
    )
    _out.write_text(json.dumps(_report, indent=2), encoding="utf-8")
    print(f"Report written to {_out}")
    print(json.dumps(_report["summary"], indent=2))
