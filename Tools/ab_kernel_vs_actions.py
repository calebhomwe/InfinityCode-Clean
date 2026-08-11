"""A/B benchmark: JSON-action mode (LongTaskEngine) vs kernel mode (KernelTaskEngine).

Runs the SAME task list through both long-task engines with the same builder
client and prints a side-by-side comparison table. Failure-proof: a task that
raises is recorded as a row with status="error" and the run continues.

Usage:
    python Tools/ab_kernel_vs_actions.py --repo <path> --tasks <path|inline> \
        --model <id> [--max-steps 10] [--json] [--local]

Core design:
- `run_ab(builder_factory, tasks, repo, ...)` is the pure, factory-injected
  runner — tests drive it with FakeBuilders, the CLI drives it with a thin
  `ChatClient` over the provider chain (core.provider_chat / router lanes).
- Every engine run gets its own `LongTaskJournal` in a temp dir, and its own
  metering wrapper (`_RecordingClient`) that counts model calls and estimates
  input tokens as sum(len(str(m["content"]))/4) over the messages it saw.
- Both engines receive the identical builder protocol `.chat(messages,
  max_tokens)` so a single client object is fair across modes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "run_ab",
    "run_ab_cli",
    "load_tasks",
    "render_table",
    "render_totals",
    "totals",
    "ChatClient",
    "LongTaskEngine",
    "KernelTaskEngine",
    "Budget",
]

_MODES: tuple = ("actions", "kernel")

# Provider key envs the provider chain understands (openrouter_client /
# dashscope_client / moonshot_client all read these).
_CLOUD_KEYS: tuple = (
    "OPENROUTER_API_KEY",
    "DASHSCOPE_API_KEY",
    "ALIBABA_API_KEY",
    "MOONSHOT_API_KEY",
    "KIMI_API_KEY",
    "DEEPSEEK_API_KEY",
)


def _bootstrap_backend() -> None:
    """Put the infinity-code backend/ package on sys.path so the core.longtask
    imports below resolve whether the script lives in Tools/ (repo layout) or
    in the swarm staging dir (INFINITY_CODE_ROOT)."""
    here = Path(__file__).resolve()
    candidates = [here.parent.parent / "backend", here.parent / "backend"]
    root = os.environ.get("INFINITY_CODE_ROOT", "")
    if root:
        candidates.append(Path(root) / "backend")
    for cand in candidates:
        if (cand / "core" / "longtask" / "engine.py").is_file():
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            return


_bootstrap_backend()

try:
    from core.longtask.engine import Budget, LongTaskEngine
    from core.longtask.journal import LongTaskJournal
except ImportError:  # repo root on path instead of backend/
    from backend.core.longtask.engine import (  # type: ignore[no-redef]
        Budget, LongTaskEngine)
    from backend.core.longtask.journal import (  # type: ignore[no-redef]
        LongTaskJournal)

try:
    from core.longtask.kernel_engine import KernelTaskEngine
except ImportError:  # pragma: no cover - depends on Worker 1's module
    try:
        from backend.core.longtask.kernel_engine import (  # type: ignore[no-redef]
            KernelTaskEngine)
    except ImportError:
        # Kernel mode is unavailable (kernel_engine.py not deployed yet):
        # run_ab records status "unavailable" rows instead of crashing.
        KernelTaskEngine = None  # type: ignore[assignment]


class _EngineUnavailable(RuntimeError):
    """Raised internally when a requested engine mode cannot be built."""


class _RecordingClient:
    """Delegating wrapper that meters what a run's chat calls saw.

    Wraps any object with `.chat(messages, max_tokens)` — a FakeBuilder in
    tests or a real provider client in the CLI — so the same meter works for
    both modes. Approx input tokens: sum(len(str(content)) / 4) over every
    message list passed in (same rule as the swarm's cost model).
    """

    def __init__(self, real: Any) -> None:
        self._real = real
        self.calls = 0
        self.tokens_in = 0.0
        self.cost_usd = 0.0

    def chat(self, messages: List[Dict[str, str]],
             max_tokens: int = 3000) -> Dict[str, Any]:
        self.calls += 1
        self.tokens_in += sum(
            len(str(m.get("content", ""))) / 4 for m in messages)
        reply = self._real.chat(messages, max_tokens=max_tokens)
        text = reply.get("text") if isinstance(reply, dict) else reply
        cost = float(reply.get("cost_usd", 0.0)) if isinstance(reply, dict) else 0.0
        self.cost_usd += cost
        return {"text": str(text), "cost_usd": cost}


def _engine_for(mode: str) -> type:
    if mode == "kernel":
        if KernelTaskEngine is None:
            raise _EngineUnavailable(
                "KernelTaskEngine is not importable (kernel_engine.py missing)")
        return KernelTaskEngine
    return LongTaskEngine


def _files_of(root: Path) -> Dict[str, float]:
    """Snapshot {rel_path: mtime} of every file under root (cheap repos)."""
    out: Dict[str, float] = {}
    for f in root.rglob("*"):
        try:
            if f.is_file():
                out[str(f.relative_to(root))] = f.stat().st_mtime
        except OSError:
            continue
    return out


def _steps_of(journal: LongTaskJournal, task_id: str) -> int:
    if not task_id:
        return 0
    try:
        row = journal.get_task(task_id)
        return int(row.get("steps") or 0) if row else 0
    except Exception:  # noqa: BLE001 - metrics are evidence, never load-bearing
        return 0


def run_ab(builder_factory: Callable[[str], Any], tasks: List[str],
           repo: Path, max_steps: int = 10,
           autonomy: str = "full",
           expect: str = "") -> List[Dict[str, Any]]:
    """Run every task through both engines; never raises on a failed task.

    builder_factory(mode) returns a FRESH builder per mode ("actions" or
    "kernel"); each engine run gets its own LongTaskJournal in a temp dir.
    Returns one row per (mode, goal):
    {"mode", "goal", "status", "calls", "tokens_in", "cost_usd", "wall_s",
     "steps"}.
    """
    repo = Path(repo)
    rows: List[Dict[str, Any]] = []
    for mode in _MODES:
        for goal in tasks:
            before = _files_of(repo) if expect else {}
            wrapped = _RecordingClient(builder_factory(mode))
            workdir = Path(tempfile.mkdtemp(prefix=f"ab_{mode}_"))
            journal = LongTaskJournal(workdir / "ab.db")
            t0 = time.time()
            status, task_id, err = "error", "", ""
            try:
                engine = _engine_for(mode)(builder=wrapped, journal=journal)
                res = engine.run(
                    goal=goal, repo_path=str(repo),
                    budget=Budget(max_steps=max_steps), autonomy=autonomy)
                status = str(res.get("status", "error"))
                task_id = str(res.get("task_id", ""))
            except _EngineUnavailable as exc:
                status = "unavailable"
                err = repr(exc)
            except Exception as exc:  # noqa: BLE001 - record and continue
                status = "error"
                err = repr(exc)
            rows.append({
                "mode": mode,
                "goal": goal,
                "status": status,
                "calls": wrapped.calls,
                "tokens_in": int(round(wrapped.tokens_in)),
                "cost_usd": round(wrapped.cost_usd, 6),
                "wall_s": round(time.time() - t0, 3),
                "steps": _steps_of(journal, task_id),
                "error": err,
            })
            # Artifact gate: a "completed" claim is only real if the expected
            # file was actually created/modified in the repo during the run
            # (kernel false-completed 2026-08-11 with no artifact at all).
            if expect and rows[-1]["status"] == "completed":
                after = _files_of(repo)
                changed = {k for k, v in after.items()
                           if before.get(k) != v}
                if expect.replace("\\", "/") not in {
                        k.replace("\\", "/") for k in changed}:
                    rows[-1]["status"] = "claimed_unverified"
    return rows


# --- reporting ------------------------------------------------------------- #


def render_table(rows: List[Dict[str, Any]]) -> str:
    """Side-by-side per-goal table: mode, status, calls, tokens, cost, wall_s."""
    head = (f"{'goal':<44} {'mode':<8} {'status':<16} {'calls':>6} "
            f"{'tokens_in':>9} {'cost_usd':>10} {'wall_s':>8}")
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(
            f"{str(r['goal'])[:44]:<44} {str(r['mode']):<8} "
            f"{str(r['status']):<16} {int(r['calls']):>6} "
            f"{int(r['tokens_in']):>9} {float(r['cost_usd']):>10.4f} "
            f"{float(r['wall_s']):>8.2f}")
    return "\n".join(lines)


def totals(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Per-mode aggregates: runs, completed, calls, tokens_in, cost_usd, wall_s."""
    out: Dict[str, Dict[str, float]] = {}
    for mode in _MODES:
        rs = [r for r in rows if r["mode"] == mode]
        out[mode] = {
            "runs": float(len(rs)),
            "completed": float(sum(1 for r in rs if r["status"] == "completed")),
            "calls": float(sum(int(r["calls"]) for r in rs)),
            "tokens_in": float(sum(int(r["tokens_in"]) for r in rs)),
            "cost_usd": round(sum(float(r["cost_usd"]) for r in rs), 6),
            "wall_s": round(sum(float(r["wall_s"]) for r in rs), 3),
        }
    return out


def render_totals(ts: Dict[str, Dict[str, float]]) -> str:
    head = (f"{'mode':<8} {'runs':>5} {'completed':>10} {'calls':>6} "
            f"{'tokens_in':>9} {'cost_usd':>10} {'wall_s':>8}")
    lines = ["", "Totals per mode:", head, "-" * len(head)]
    for mode in _MODES:
        t = ts[mode]
        lines.append(
            f"{mode:<8} {int(t['runs']):>5} {int(t['completed']):>10} "
            f"{int(t['calls']):>6} {int(t['tokens_in']):>9} "
            f"{float(t['cost_usd']):>10.4f} {float(t['wall_s']):>8.2f}")
    return "\n".join(lines)


# --- tasks loading --------------------------------------------------------- #


def load_tasks(source: str) -> List[str]:
    """Tasks from a .json file of {"goal": str} dicts, else inline '||' split."""
    if source.endswith(".json"):
        path = Path(source)
        if not path.is_file():
            raise ValueError(f"tasks file not found: {path}")
        try:
            # utf-8-sig: PowerShell/Notepad write a BOM; don't choke on it.
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot parse tasks file {path}: {exc}") from exc
        if not isinstance(data, list):
            raise ValueError(
                "tasks file must contain a JSON list of {\"goal\": ...} dicts")
        goals: List[str] = []
        for item in data:
            if not isinstance(item, dict) or not str(item.get("goal", "")).strip():
                raise ValueError(
                    f"each tasks entry needs a non-empty 'goal': {item!r}")
            goals.append(str(item["goal"]))
        return goals
    return [g.strip() for g in source.split("||") if g.strip()]


# --- real builder over the provider chain ---------------------------------- #


def _load_key_env() -> Dict[str, str]:
    """os.environ plus HOME/.env and cwd/.env (never prints key values)."""
    out: Dict[str, str] = {}
    for p in (Path.home() / ".env", Path.cwd() / ".env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    for k, v in os.environ.items():
        out.setdefault(k, v)
    return out


def _cheap_lane_ids() -> List[str]:
    """The cheapest router lane, primary first (dashscope/qwen-turbo chain)."""
    try:
        from core.router import LANE_CHEAP, LaneRouter
    except ImportError:
        from backend.core.router import LANE_CHEAP, LaneRouter  # type: ignore[no-redef]
    return [spec.id for spec in LaneRouter().chain(LANE_CHEAP)]


def _no_key_error(env: Dict[str, str]) -> Optional[str]:
    if any(env.get(k) for k in _CLOUD_KEYS):
        return None
    return (
        "No LLM provider key configured. Set one of OPENROUTER_API_KEY, "
        "DASHSCOPE_API_KEY, MOONSHOT_API_KEY, or DEEPSEEK_API_KEY (in the "
        "environment or a .env file), or pass --local with LOCAL_BASE_URL "
        "and LOCAL_MODEL set."
    )


class ChatClient:
    """Thin builder over the provider chain (core.provider_chat + router).

    Exposes the engine's protocol `.chat(messages, max_tokens)` and returns
    {"text": str, "cost_usd": float}. Walks the model chain (user model first,
    then the cheapest lane) and dispatches each call through provider_chat,
    exactly like the app's _LongTaskBuilderAdapter. Provider clients are built
    lazily on first chat so constructing a ChatClient is cheap.
    """

    def __init__(self, model: str = "", local: bool = False) -> None:
        self.model = model
        self.local = local
        self._env = _load_key_env()
        self._ids: Optional[List[str]] = None
        self._openrouter: Any = None
        self._moonshot: Any = None
        self._dashscope: Any = None

    def _ensure(self) -> None:
        if self._ids is not None:
            return
        if self.local:
            base = self._env.get("LOCAL_BASE_URL")
            if not base:
                raise RuntimeError(
                    "--local requires LOCAL_BASE_URL to be set")
            model = self.model or self._env.get("LOCAL_MODEL") or "local/model"
            try:
                from tools.openrouter_client import OpenRouterClient
            except ImportError:
                from backend.tools.openrouter_client import OpenRouterClient  # type: ignore[no-redef]
            self._openrouter = OpenRouterClient(
                api_key=self._env.get("LOCAL_API_KEY") or "local", base_url=base, max_retries=1, timeout=120.0)
            self._ids = [model]
            return
        self._ids = ([self.model] if self.model else []) + [
            m for m in _cheap_lane_ids() if m != self.model]
        if self._env.get("DASHSCOPE_API_KEY") or self._env.get("ALIBABA_API_KEY"):
            try:
                from tools.dashscope_client import DashScopeClient
            except ImportError:
                from backend.tools.dashscope_client import DashScopeClient  # type: ignore[no-redef]
            try:
                # Clients default to os.environ; pass the .env key explicitly
                # so a key that only lives in .env still works.
                self._dashscope = DashScopeClient(
                    api_key=self._env.get("DASHSCOPE_API_KEY")
                    or self._env.get("ALIBABA_API_KEY"))
            except Exception:  # noqa: BLE001 - bad config degrades, never kills
                self._dashscope = None
        if self._env.get("MOONSHOT_API_KEY") or self._env.get("KIMI_API_KEY"):
            try:
                from tools.moonshot_client import MoonshotClient
            except ImportError:
                from backend.tools.moonshot_client import MoonshotClient  # type: ignore[no-redef]
            try:
                self._moonshot = MoonshotClient(
                    api_key=self._env.get("MOONSHOT_API_KEY")
                    or self._env.get("KIMI_API_KEY"))
            except Exception:  # noqa: BLE001 - bad config degrades, never kills
                self._moonshot = None
        if any(self._env.get(k) for k in _CLOUD_KEYS):
            try:
                from tools.openrouter_client import OpenRouterClient
            except ImportError:
                from backend.tools.openrouter_client import OpenRouterClient  # type: ignore[no-redef]
            try:
                self._openrouter = OpenRouterClient(max_retries=1)
            except Exception:  # noqa: BLE001 - bad config degrades, never kills
                self._openrouter = None
        # The cheap lane is dashscope-only; append the free OpenRouter meta
        # router so the chain always has a live zero-cost fallback.
        if self._openrouter is not None and "openrouter/free" not in self._ids:
            self._ids.append("openrouter/free")

    def chat(self, messages: List[Dict[str, str]],
             max_tokens: int = 3000) -> Dict[str, Any]:
        self._ensure()
        try:
            from core.provider_chat import provider_chat
        except ImportError:
            from backend.core.provider_chat import provider_chat  # type: ignore[no-redef]
        last_err: Optional[BaseException] = None
        for model_id in self._ids or []:
            try:
                return provider_chat(
                    model_id, messages, max_tokens,
                    openrouter=self._openrouter,
                    moonshot=self._moonshot,
                    dashscope=self._dashscope)
            except Exception as exc:  # noqa: BLE001 - walk the chain
                last_err = exc
        raise RuntimeError(
            f"all builder models failed: {last_err}")


def _cli_builder_factory(model: str, local: bool) -> Callable[[str], ChatClient]:
    def make(mode: str) -> ChatClient:  # noqa: ARG001 - factory protocol
        return ChatClient(model=model, local=local)
    return make


# --- CLI ------------------------------------------------------------------- #


def run_ab_cli(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ab_kernel_vs_actions",
        description="A/B benchmark: JSON-action mode vs kernel mode on the "
                    "same task list.")
    ap.add_argument("--repo", required=True,
                    help="repo path the tasks run against")
    ap.add_argument("--tasks", required=True,
                    help="path to a .json file with a list of {\"goal\": ...} "
                         "dicts, or 'goal1||goal2' inline")
    ap.add_argument("--model", default="",
                    help="builder model id (default: cheapest router lane)")
    ap.add_argument("--max-steps", type=int, default=10,
                    help="engine step budget per run (default: 10)")
    ap.add_argument("--autonomy", default="full", choices=("full", "ask"),
                    help="engine autonomy mode (default: full)")
    ap.add_argument("--json", action="store_true",
                    help="print rows as JSON lines (totals go to stderr)")
    ap.add_argument("--expect", default="",
                    help="repo-relative file the task must create/modify; a "
                         "completed run missing it is downgraded to "
                         "claimed_unverified")
    ap.add_argument("--local", action="store_true",
                    help="use a local OpenAI-compatible endpoint "
                         "(LOCAL_BASE_URL / LOCAL_MODEL env)")
    args = ap.parse_args(argv)

    env = _load_key_env()
    if args.local:
        if not env.get("LOCAL_BASE_URL"):
            print("error: --local requires LOCAL_BASE_URL to be set",
                  file=sys.stderr)
            return 2
    elif _no_key_error(env):
        print(_no_key_error(env), file=sys.stderr)
        return 2

    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"error: repo directory not found: {repo}", file=sys.stderr)
        return 2

    try:
        tasks = load_tasks(args.tasks)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not tasks:
        print("error: no tasks given", file=sys.stderr)
        return 2

    model = args.model or (env.get("LOCAL_MODEL") if args.local else "")
    rows = run_ab(_cli_builder_factory(model=model, local=args.local),
                  tasks, repo, max_steps=args.max_steps,
                  autonomy=args.autonomy, expect=args.expect)
    ts = totals(rows)
    if args.json:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        print(json.dumps({"totals": ts}, ensure_ascii=False), file=sys.stderr)
    else:
        print(render_table(rows))
        print(render_totals(ts))
    return 0


if __name__ == "__main__":
    sys.exit(run_ab_cli())
