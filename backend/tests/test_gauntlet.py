"""Benchmark Gauntlet tests: gap math, hallucination/tool-use signals,
latest-report picking, post_gap effort wiring, and the API routes."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402

from backend.core.ascension import AscensionEngine  # noqa: E402
from backend.core.gauntlet import (  # noqa: E402
    benchmark_gap,
    hallucination_rate,
    latest_reports,
    post_gap,
    status,
    tool_use_reliability,
)


def _result(task_id, name, score, passed=None):
    return {
        "task_id": task_id,
        "name": name,
        "score": score,
        "passed": bool(passed) if passed is not None else score >= 0.5,
        "capability": "math",
    }


FIXTURE = [
    _result("lb-01", "livebench arithmetic chain", 1.0),
    _result("sq-01", "simpleqa geography", 0.5),
    _result("hh-01", "hhem fabricated citation", 0.0),
    _result("tau-01", "tau tool order", 0.75),
]


def _write_report(data_dir: Path, name: str, results, bump_mtime=False):
    reports_dir = data_dir / "eval_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": name,
        "lane": "cheap",
        "model": "deepseek/deepseek-v4-flash",
        "tasks_total": len(results),
        "tasks_passed": sum(1 for r in results if r["passed"]),
        "aggregate_score": round(
            sum(r["score"] for r in results) / len(results), 4) if results else 0.0,
        "results": results,
    }
    path = reports_dir / f"benchmarks_{name}.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    if bump_mtime:
        future = time.time() + 100
        os.utime(path, (future, future))
    return path


def test_gap_math_on_fixture(tmp_path):
    _write_report(tmp_path, "final", FIXTURE)
    reports = latest_reports(tmp_path)
    assert len(reports) == 1
    mean = (1.0 + 0.5 + 0.0 + 0.75) / 4
    assert benchmark_gap(reports) == round((1 - mean) * 100, 2)


def test_hallucination_and_tool_use_signals(tmp_path):
    _write_report(tmp_path, "final", FIXTURE)
    reports = latest_reports(tmp_path)
    assert hallucination_rate(reports) == pytest.approx(0.75)  # 1 - (0.5+0)/2
    assert tool_use_reliability(reports) == pytest.approx(0.75)


def test_no_reports_is_zero_gap_not_escalation(tmp_path):
    assert latest_reports(tmp_path) == []
    assert benchmark_gap([]) == 0.0
    assert status(tmp_path) == {"report": None}


def test_latest_reports_picks_newest(tmp_path):
    _write_report(tmp_path, "old", FIXTURE)
    _write_report(tmp_path, "new", [], bump_mtime=True)
    reports = latest_reports(tmp_path)
    assert reports[0]["run_id"] == "new"


def test_post_gap_feeds_effort_and_suggests_blue(tmp_path):
    engine = AscensionEngine(log_path=str(tmp_path / "asc.jsonl"))
    _write_report(tmp_path, "final", [_result("x", "swe-bench verified", 0.0, False)])
    out = post_gap(engine, tmp_path)
    assert engine.effort.benchmark_gap_0_100 == 100.0
    assert out["gap"] == 100.0
    assert out["suggested"] == "BLUE"
    # effort = 100 * (0.20 * gap_n) with everything else zeroed.
    assert out["effort"] == pytest.approx(20.0, abs=0.01)
    assert out["report"] == "deepseek/deepseek-v4-flash"


def test_post_gap_no_reports_no_suggestion(tmp_path):
    engine = AscensionEngine(log_path=str(tmp_path / "asc.jsonl"))
    out = post_gap(engine, tmp_path)
    assert out["gap"] == 0.0
    assert out["suggested"] is None
    assert out["report"] is None


def test_status_summary(tmp_path):
    _write_report(tmp_path, "final", FIXTURE)
    s = status(tmp_path)
    assert s["report"] == "deepseek/deepseek-v4-flash"
    assert s["tasks_total"] == 4
    assert s["gap"] == benchmark_gap(latest_reports(tmp_path))
    assert "mean_score" in s and "hallucination_rate" in s
    assert "tool_use_reliability" in s


# --- API level --------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    import main
    from core.ascension import AscensionEngine

    monkeypatch.setattr(
        main, "ASCENSION_ENGINE",
        AscensionEngine(config={"dwell_s": 0, "cooldown_s": 0},
                        log_path=str(tmp_path / "asc.jsonl"),
                        passphrase="x-test"),
    )
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    from fastapi.testclient import TestClient
    return TestClient(main.app, headers={"Authorization": f"Bearer {main._API_TOKEN}"})


def test_api_gap_route(client, tmp_path):
    _write_report(tmp_path, "final", FIXTURE)
    r = client.post("/api/v1/gauntlet/gap", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["gap"] == benchmark_gap(latest_reports(tmp_path))
    assert body["suggested"] is None  # fixture mean > 0.4 => gap < 60
    assert body["report"] == "deepseek/deepseek-v4-flash"


def test_api_status_route_without_reports(client):
    r = client.get("/api/v1/gauntlet/status")
    assert r.status_code == 200
    assert r.json() == {"report": None}
