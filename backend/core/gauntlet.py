"""Infinity Code X: Benchmark Gauntlet.

Turns the newest eval harness report into the Ascension Engine's
benchmark-gap signal. No report => gap 0 (no measurable need => no
ascension justification; the status endpoint reports `report: null` so
missing data is visible, not hidden).
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# Task-name substrings used to derive targeted signals from a report.
_HALLUCINATION_HINTS = ("simpleqa", "hhem")
_TOOL_USE_HINTS = ("tau",)


def latest_reports(data_dir: Path) -> List[Dict[str, Any]]:
    """Parse eval reports from <data_dir>/eval_reports, newest first.

    Both layouts are searched: <data_dir>/eval_reports (harness default)
    and <data_dir>/data/eval_reports (legacy/benchmark-runner layout when
    DATA_DIR defaults to the backend/ base dir).
    """
    reports_dir = Path(data_dir) / "eval_reports"
    legacy_dir = Path(data_dir) / "data" / "eval_reports"
    dirs = [d for d in (reports_dir, legacy_dir) if d.is_dir()]
    if not dirs:
        return []
    files: List[Path] = []
    seen = set()
    for d in dirs:
        for pattern in ("benchmarks_*.json", "eval_*.json"):
            for p in d.glob(pattern):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    files.append(p)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out: List[Dict[str, Any]] = []
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            continue
    return out


def _mean_score(report: Dict[str, Any]) -> float:
    """Mean per-task score (0..1); falls back to aggregate, then pass rate."""
    results = report.get("results") or []
    if results:
        scores = [float(r.get("score", 0.0)) for r in results]
        return sum(scores) / len(scores)
    agg = report.get("aggregate_score")
    if agg is not None:
        return float(agg)
    summary = report.get("summary") or {}
    pr = summary.get("pass_rate")
    if pr is not None:
        return float(pr)
    return 0.0


def benchmark_gap(reports: List[Dict[str, Any]]) -> float:
    """0..100 gap = (1 - mean score) on the newest report; 0 when none."""
    if not reports:
        return 0.0
    return round((1.0 - _mean_score(reports[0])) * 100.0, 2)


def _task_scores(report: Dict[str, Any], hints: tuple) -> List[float]:
    results = report.get("results") or []
    return [
        float(r.get("score", 0.0))
        for r in results
        if any(h in str(r.get("name", "")).lower() for h in hints)
    ]


def hallucination_rate(reports: List[Dict[str, Any]]) -> float:
    """Mean failure rate (0..1) on simpleqa/hhem tasks of the newest report."""
    if not reports:
        return 0.0
    scores = _task_scores(reports[0], _HALLUCINATION_HINTS)
    if not scores:
        return 0.0
    return round(1.0 - sum(scores) / len(scores), 4)


def tool_use_reliability(reports: List[Dict[str, Any]]) -> float:
    """Mean score (0..1) on tau-bench tasks of the newest report."""
    if not reports:
        return 0.0
    scores = _task_scores(reports[0], _TOOL_USE_HINTS)
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 4)


def status(data_dir: Path) -> Dict[str, Any]:
    """Summary for GET /gauntlet/status; {"report": None} when no reports."""
    reports = latest_reports(data_dir)
    if not reports:
        return {"report": None}
    report = reports[0]
    return {
        "report": report.get("model"),
        "tasks_total": report.get("tasks_total"),
        "model": report.get("model"),
        "mean_score": round(_mean_score(report), 4),
        "gap": benchmark_gap(reports),
        "hallucination_rate": hallucination_rate(reports),
        "tool_use_reliability": tool_use_reliability(reports),
    }


def post_gap(engine: Any, data_dir: Path) -> Dict[str, Any]:
    """Measure the gap, feed the effort score, suggest an ascension form.

    Suggests BLUE only on a measured gap >= 60 -- never on missing data.
    """
    try:
        from backend.core.ascension import effort_score
    except ImportError:  # running with backend/ as the working directory
        from core.ascension import effort_score  # type: ignore[no-redef]
    reports = latest_reports(data_dir)
    gap = benchmark_gap(reports)
    engine.effort.benchmark_gap_0_100 = gap
    suggested: Optional[str] = "BLUE" if gap >= 60.0 else None
    return {
        "gap": gap,
        "effort": effort_score(engine.effort),
        "suggested": suggested,
        "report": reports[0].get("model") if reports else None,
    }
